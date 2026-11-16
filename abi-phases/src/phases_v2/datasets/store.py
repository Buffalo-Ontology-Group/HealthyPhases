"""The module's only doorway to the dataset service.

Everything the pipeline writes goes through :func:`write_rows`, which always
upserts. That is not a stylistic preference: ``DatasetSpec.primary_key`` is
advisory — the store matches upserts against it but enforces no uniqueness — so
an ``append`` into a keyed dataset duplicates rows silently, with no error to
notice. Keeping one function in one file makes that invariant reviewable, and
``store_test`` fails if any other module file writes directly. The two ways to
remove rows, :func:`delete_rows` and :func:`truncate`, live here for the same
reason.
"""

from __future__ import annotations

import json
from typing import Any

from naas_abi_core import logger
from naas_abi_core.services.dataset.DatasetPort import (
    DatasetAlreadyExistsError,
    DatasetNotFoundError,
    DatasetSpec,
)
from naas_abi_core.services.dataset.DatasetService import DatasetService

from phases_v2.datasets.schemas import DATASETS, NAMESPACE
from phases_v2.sql import in_list

_BY_NAME: dict[str, DatasetSpec] = {spec.name: spec for spec in DATASETS}

#: Nullable columns declared after their table already existed in production.
#: ``ensure_datasets`` adds each to a live table that lacks it; the rows already
#: there read NULL, which for ``owner_id`` means shared. Only string columns.
ADDED_COLUMNS: dict[str, tuple[str, ...]] = {
    "prompts": ("owner_id",),
    "paper_collections": ("owner_id",),
    "pipelines": ("owner_id",),
}


def ensure_datasets(dataset: DatasetService) -> set[str]:
    """Create any declared dataset that does not exist yet.

    Runs on every engine start, in every process, so it must be cheap and
    repeatable. Returns the names it created, which is empty on all but the
    first run. Datasets that already exist are left exactly as they are —
    their rows are never touched.
    """
    live = {info.name: info for info in dataset.list(namespace=NAMESPACE)}
    existing = set(live)
    added = add_missing_columns(dataset, live)
    if added:
        logger.info(f"phases_v2: added columns {added}; existing rows read NULL")
        live = {info.name: info for info in dataset.list(namespace=NAMESPACE)}
    _warn_about_drift(live)
    created = set()
    for spec in DATASETS:
        if spec.name in existing:
            continue
        try:
            dataset.create(spec)
        except DatasetAlreadyExistsError:
            # Another process created it between the list and the create.
            continue
        created.add(spec.name)
    return created


def add_missing_columns(
    dataset: DatasetService, live: dict | None = None
) -> dict[str, list[str]]:
    """Add the :data:`ADDED_COLUMNS` a live table lacks. Returns what it added.

    The dataset service creates tables but has no way to change one, so this
    alters the DuckLake table with SQL and then rewrites the table comment,
    where the service keeps the declared schema it checks writes against, in
    the form the service itself writes on creation. Rows are not touched.

    Each step checks first, so a second run, or a second process starting at
    the same time, finds nothing to do. A table whose schema differs in any
    other way is left alone, with the drift warning ``ensure_datasets`` gives.
    """
    from naas_abi_core.services.dataset.adapters.secondary.DatasetSecondaryAdapterDuckLake import (
        SPEC_COMMENT_PREFIX,
    )

    if live is None:
        live = {info.name: info for info in dataset.list(namespace=NAMESPACE)}
    added: dict[str, list[str]] = {}
    for name, columns in ADDED_COLUMNS.items():
        info = live.get(name)
        if info is None:
            continue
        spec = _BY_NAME[name]
        declared = [column.name for column in spec.columns]
        recorded = [column.name for column in info.columns]
        missing = [column for column in columns if column not in recorded]
        if not missing:
            continue
        if sorted(recorded + missing) != sorted(declared):
            # Reads of this table name the new columns, so they fail until it
            # is fixed: say so where a deploy shows it, not only as drift.
            logger.error(
                f"phases_v2: {', '.join(missing)} was not added to {name!r}, whose "
                "schema also differs in other ways (see the drift warning). "
                f"Listings that read {name} will fail until its schema matches "
                "phases_v2.datasets.schemas."
            )
            continue
        for column in missing:
            _add_column(dataset, name, column)
        comment = SPEC_COMMENT_PREFIX + json.dumps(
            spec.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        _run_once(
            dataset,
            f"COMMENT ON TABLE {name} IS {_sql_string(comment)}",
            done=lambda: (
                {c.name for c in dataset.describe(name, namespace=NAMESPACE).columns}
                >= set(missing)
            ),
        )
        added[name] = missing
    return added


def _physical_columns(dataset: DatasetService, name: str) -> set[str]:
    rows = dataset.query(f"DESCRIBE {name}", namespace=NAMESPACE).rows  # nosec B608 - a declared dataset name
    return {row["column_name"] for row in rows}


def _add_column(dataset: DatasetService, name: str, column: str) -> None:
    spec_type = next(c.type for c in _BY_NAME[name].columns if c.name == column)
    if spec_type != "string":
        raise ValueError(f"Only string columns can be added; {name}.{column} is not.")
    if column in _physical_columns(dataset, name):
        return
    _run_once(
        dataset,
        f"ALTER TABLE {name} ADD COLUMN {column} VARCHAR",
        done=lambda: column in _physical_columns(dataset, name),
    )


def _run_once(dataset: DatasetService, sql: str, done) -> None:
    """Run ``sql``; when it fails, accept it only if another process did it."""
    try:
        dataset.query(sql, namespace=NAMESPACE)
    except Exception:
        if done():
            return
        raise


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def write_rows(
    dataset: DatasetService,
    name: str,
    rows: list[dict[str, Any]],
) -> None:
    """Upsert ``rows`` into the named dataset, keyed on its primary key.

    Columns the caller omits are written as NULL, so a caller only states the
    fields it actually knows. Columns that are not in the schema are rejected
    by the store rather than silently dropped.
    """
    spec = _BY_NAME.get(name)
    if spec is None:
        raise KeyError(
            f"{name!r} is not a phases_v2 dataset; "
            f"declared datasets are {', '.join(sorted(_BY_NAME))}"
        )
    if not rows:
        return

    columns = [column.name for column in spec.columns]
    complete = [{column: row.get(column) for column in columns} for row in rows]
    unknown = sorted({key for row in rows for key in row} - set(columns))
    if unknown:
        raise KeyError(f"{name} has no column(s) {', '.join(unknown)}")

    dataset.write(name, complete, namespace=NAMESPACE, mode="upsert")


# Bounds one DELETE statement; the dataset service takes SQL as a string.
_DELETE_BATCH = 500


def delete_rows(
    dataset: DatasetService, name: str, column: str, values: list[str]
) -> int:
    """Delete the rows of ``name`` whose ``column`` is one of ``values``.

    Nothing in the pipeline deletes: results are corrected by upserting over
    their own key. This exists for the one case an upsert cannot express — a
    row whose key has changed, as when ids derived from a prompt's name are
    re-derived under a new name, leaving the old rows to be removed. Returns
    how many rows went.
    """
    spec = _BY_NAME.get(name)
    if spec is None:
        raise KeyError(f"{name!r} is not a phases_v2 dataset")
    if column not in {c.name for c in spec.columns}:
        raise KeyError(f"{name} has no column {column!r}")

    deleted = 0
    unique = sorted(set(values))
    for start in range(0, len(unique), _DELETE_BATCH):
        batch = in_list(unique[start : start + _DELETE_BATCH])
        matching = dataset.query(
            f"SELECT count(*) AS total FROM {name} WHERE {column} IN {batch}",  # nosec B608 - name and column are checked against the schema above
            namespace=NAMESPACE,
        ).rows[0]["total"]
        if not matching:
            continue
        dataset.query(
            f"DELETE FROM {name} WHERE {column} IN {batch}",  # nosec B608
            namespace=NAMESPACE,
        )
        deleted += int(matching)
    return deleted


def truncate(dataset: DatasetService, name: str) -> None:
    """Empty a dataset without dropping it.

    Only for rebuilding a projection from scratch — clearing the ledger so the
    projectors re-derive the graph or the collections. Nothing in the pipeline
    calls this during normal operation.
    """
    if name not in _BY_NAME:
        raise KeyError(f"{name!r} is not a phases_v2 dataset")
    dataset.write(name, [], namespace=NAMESPACE, mode="replace")


def schema_drift(dataset: DatasetService) -> dict[str, dict[str, list[str]]]:
    """Datasets whose live columns no longer match what this module declares.

    ``ensure_datasets`` only creates what is missing — it never alters a table
    that already exists — so a schema change here leaves the warehouse behind.
    That surfaces much later as a write failing with "has no column", which is
    a long way from the cause.
    """
    live = {info.name: info for info in dataset.list(namespace=NAMESPACE)}
    return _drift(live)


def _drift(live: dict) -> dict[str, dict[str, list[str]]]:
    out: dict[str, dict[str, list[str]]] = {}
    for spec in DATASETS:
        info = live.get(spec.name)
        if info is None:
            continue
        declared = {column.name for column in spec.columns}
        actual = {column.name for column in info.columns}
        if declared == actual:
            continue
        out[spec.name] = {
            "missing": sorted(declared - actual),
            "stale": sorted(actual - declared),
        }
    return out


def _warn_about_drift(live: dict) -> None:
    for name, diff in _drift(live).items():
        logger.warning(
            f"phases_v2: dataset {name!r} does not match its declared schema "
            f"(missing {diff['missing']}, stale {diff['stale']}). Writes to it "
            "will fail until it is recreated — see store.recreate()."
        )


def recreate(dataset: DatasetService, name: str) -> None:
    """Drop and re-create a dataset from its declared schema.

    Destructive: every row is lost. Only for a dataset whose schema has drifted
    and whose contents are rebuildable — the projections ledger, or anything
    empty. Never call it on `papers` or `extractions` holding real work.
    """
    spec = _BY_NAME.get(name)
    if spec is None:
        raise KeyError(f"{name!r} is not a phases_v2 dataset")
    try:
        dataset.drop(name, namespace=NAMESPACE)
    except DatasetNotFoundError:
        pass
    dataset.create(spec)
