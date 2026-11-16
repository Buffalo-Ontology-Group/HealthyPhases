"""The module's only doorway to the dataset service.

Two invariants live here. Creation is idempotent, because every engine process
runs it on load. And every write is an upsert — ``primary_key`` is advisory, so
a single stray ``append`` would duplicate rows with no error and no warning.
"""

import ast
import pathlib
import re

import pytest
from naas_abi_core.services.dataset.adapters.secondary.DatasetSecondaryAdapterDuckLake import (
    DatasetSecondaryAdapterDuckLake,
)
from naas_abi_core.services.dataset.DatasetService import DatasetService

from phases_v2.datasets import store
from phases_v2.datasets.schemas import DATASETS, NAMESPACE


@pytest.fixture
def dataset(tmp_path) -> DatasetService:
    return DatasetService(
        adapter=DatasetSecondaryAdapterDuckLake(
            catalog=f"sqlite:{tmp_path / 'catalog.sqlite'}",
            data_path=str(tmp_path / "data") + "/",
        )
    )


# -- ensure_datasets ----------------------------------------------------


def test_ensure_datasets_creates_every_declared_dataset(dataset):
    created = store.ensure_datasets(dataset)

    assert created == {spec.name for spec in DATASETS}
    existing = {info.name for info in dataset.list(namespace=NAMESPACE)}
    assert existing == {spec.name for spec in DATASETS}


def test_ensure_datasets_is_idempotent(dataset):
    store.ensure_datasets(dataset)

    created = store.ensure_datasets(dataset)

    assert created == set()
    existing = [info.name for info in dataset.list(namespace=NAMESPACE)]
    assert len(existing) == len(set(existing)) == len(DATASETS)


def test_ensure_datasets_creates_only_what_is_missing(dataset):
    store.ensure_datasets(dataset)
    dataset.drop("chunks", namespace=NAMESPACE)

    created = store.ensure_datasets(dataset)

    assert created == {"chunks"}


def test_ensure_datasets_preserves_rows_already_written(dataset):
    store.ensure_datasets(dataset)
    store.write_rows(dataset, "papers", [_paper("p1")])

    store.ensure_datasets(dataset)

    assert _paper_ids(dataset) == ["p1"]


# -- write_rows ---------------------------------------------------------


def test_write_rows_upserts_rather_than_appending(dataset):
    store.ensure_datasets(dataset)

    store.write_rows(dataset, "papers", [_paper("p1", file_name="first.pdf")])
    store.write_rows(dataset, "papers", [_paper("p1", file_name="second.pdf")])

    rows = dataset.query(
        "SELECT paper_id, file_name FROM papers", namespace=NAMESPACE
    ).rows
    assert rows == [{"paper_id": "p1", "file_name": "second.pdf"}]


def test_write_rows_writes_several_rows_at_once(dataset):
    store.ensure_datasets(dataset)

    store.write_rows(dataset, "papers", [_paper("p1"), _paper("p2")])

    assert _paper_ids(dataset) == ["p1", "p2"]


def test_write_rows_accepts_an_empty_batch(dataset):
    store.ensure_datasets(dataset)

    store.write_rows(dataset, "papers", [])

    assert _paper_ids(dataset) == []


def test_write_rows_round_trips_a_json_column(dataset):
    store.ensure_datasets(dataset)

    store.write_rows(
        dataset,
        "chunkers",
        [
            {
                "chunker_id": "c1",
                "name": "window",
                "version": "1",
                "params": {"size": 512, "overlap": 128},
                "registered_at": None,
            }
        ],
    )

    [row] = dataset.query("SELECT params FROM chunkers", namespace=NAMESPACE).rows
    assert row["params"] == {"size": 512, "overlap": 128}


def test_write_rows_rejects_an_unknown_dataset(dataset):
    store.ensure_datasets(dataset)

    with pytest.raises(KeyError):
        store.write_rows(dataset, "not_a_dataset", [])


def test_write_rows_fills_declared_columns_the_caller_omitted(dataset):
    # The store requires every column to be present; making the caller repeat
    # a dozen Nones per row is how rows drift out of sync with the schema.
    store.ensure_datasets(dataset)

    store.write_rows(dataset, "papers", [{"paper_id": "p1"}])

    [row] = dataset.query(
        "SELECT paper_id, file_name FROM papers", namespace=NAMESPACE
    ).rows
    assert row == {"paper_id": "p1", "file_name": None}


def test_write_rows_rejects_a_column_that_is_not_in_the_schema(dataset):
    store.ensure_datasets(dataset)

    with pytest.raises(Exception):
        store.write_rows(dataset, "papers", [{"paper_id": "p1", "nope": 1}])


# -- the invariant that makes all of the above true ---------------------


def test_store_is_the_only_module_code_that_writes_to_a_dataset():
    # Parsed, not grepped: prose in a docstring that mentions mode="upsert" is
    # not a write, and a guard that cannot tell the difference is one people
    # learn to ignore.
    module_root = pathlib.Path(__file__).resolve().parent.parent
    offenders = []
    for path in sorted(module_root.rglob("*.py")):
        if path.name in {"store.py", "store_test.py"}:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            writes = isinstance(node.func, ast.Attribute) and node.func.attr == "write"
            passes_mode = any(kw.arg == "mode" for kw in node.keywords)
            if writes or passes_mode:
                offenders.append(f"{path.relative_to(module_root)}:{node.lineno}")

    offenders += _sql_mutations_outside_the_store(module_root)

    assert offenders == [], (
        "these call sites write to a dataset directly instead of going through "
        f"store.write_rows, so they can bypass mode='upsert': {offenders}"
    )


_MUTATING_SQL = re.compile(
    r"^\s*(DELETE\s+FROM|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DROP\s+TABLE|TRUNCATE\s+TABLE)\b",
    re.IGNORECASE,
)


def _sql_mutations_outside_the_store(module_root: pathlib.Path) -> list[str]:
    """``DatasetService.query`` accepts any SQL, so a DELETE written anywhere
    else would bypass the store just as a direct ``write`` would. Tests build
    their own fixtures and are left alone."""
    found = []
    for path in sorted(module_root.rglob("*.py")):
        if path.name in {"store.py"} or path.name.endswith("_test.py"):
            continue
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            text = None
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value
            elif isinstance(node, ast.JoinedStr) and node.values:
                first = node.values[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    text = first.value
            if text and _MUTATING_SQL.match(text):
                found.append(f"{path.relative_to(module_root)}:{node.lineno}")
    return found


# -- helpers ------------------------------------------------------------


def _paper(paper_id: str, **overrides) -> dict:
    row = {"paper_id": paper_id, "file_name": f"{paper_id}.pdf"}
    row.update(overrides)
    return row


def _paper_ids(dataset: DatasetService) -> list[str]:
    return [
        row["paper_id"]
        for row in dataset.query(
            "SELECT paper_id FROM papers ORDER BY paper_id", namespace=NAMESPACE
        ).rows
    ]


def test_truncate_empties_a_dataset_without_dropping_it(dataset):
    store.ensure_datasets(dataset)
    store.write_rows(dataset, "papers", [_paper("p1"), _paper("p2")])

    store.truncate(dataset, "papers")

    assert _paper_ids(dataset) == []
    assert "papers" in {info.name for info in dataset.list(namespace=NAMESPACE)}


def test_truncate_rejects_an_unknown_dataset(dataset):
    with pytest.raises(KeyError):
        store.truncate(dataset, "not_a_dataset")


# -- schema drift -------------------------------------------------------


def test_a_matching_warehouse_reports_no_drift(dataset):
    store.ensure_datasets(dataset)

    assert store.schema_drift(dataset) == {}


def test_a_dataset_missing_a_declared_column_is_reported(dataset):
    # ensure_datasets never alters an existing table, so a schema change here
    # leaves the warehouse behind. Without this it surfaces much later as a
    # write failing with "has no column".
    from naas_abi_core.services.dataset.DatasetPort import ColumnSpec, DatasetSpec

    dataset.create(
        DatasetSpec(
            name="papers",
            namespace=NAMESPACE,
            columns=(ColumnSpec(name="paper_id", type="string"),),
            primary_key=("paper_id",),
        )
    )

    drift = store.schema_drift(dataset)

    assert "papers" in drift
    assert "file_name" in drift["papers"]["missing"]


def test_a_dataset_with_a_stale_column_is_reported(dataset):
    from naas_abi_core.services.dataset.DatasetPort import ColumnSpec, DatasetSpec

    columns = tuple(
        ColumnSpec(name=c.name, type=c.type) for c in store._BY_NAME["papers"].columns
    ) + (ColumnSpec(name="left_over", type="string"),)
    dataset.create(
        DatasetSpec(
            name="papers",
            namespace=NAMESPACE,
            columns=columns,
            primary_key=("paper_id",),
        )
    )

    assert store.schema_drift(dataset)["papers"]["stale"] == ["left_over"]


def test_recreate_brings_a_drifted_dataset_back_to_its_declared_schema(dataset):
    from naas_abi_core.services.dataset.DatasetPort import ColumnSpec, DatasetSpec

    dataset.create(
        DatasetSpec(
            name="papers",
            namespace=NAMESPACE,
            columns=(ColumnSpec(name="paper_id", type="string"),),
            primary_key=("paper_id",),
        )
    )

    store.recreate(dataset, "papers")

    assert "papers" not in store.schema_drift(dataset)


def test_recreate_rejects_an_unknown_dataset(dataset):
    with pytest.raises(KeyError):
        store.recreate(dataset, "not_a_dataset")


def test_delete_rows_removes_only_the_named_rows(dataset):
    store.ensure_datasets(dataset)
    store.write_rows(dataset, "papers", [_paper("p1"), _paper("p2"), _paper("p3")])

    deleted = store.delete_rows(dataset, "papers", "paper_id", ["p1", "p3", "nope"])

    assert deleted == 2
    assert _paper_ids(dataset) == ["p2"]


def test_delete_rows_of_nothing_is_a_no_op(dataset):
    store.ensure_datasets(dataset)
    store.write_rows(dataset, "papers", [_paper("p1")])

    assert store.delete_rows(dataset, "papers", "paper_id", []) == 0
    assert _paper_ids(dataset) == ["p1"]


def test_delete_rows_cannot_be_pointed_at_anything_but_a_declared_column(dataset):
    store.ensure_datasets(dataset)

    with pytest.raises(KeyError):
        store.delete_rows(dataset, "nope", "paper_id", ["p1"])
    with pytest.raises(KeyError):
        store.delete_rows(dataset, "papers", "1=1 OR paper_id", ["p1"])


def test_delete_rows_treats_values_as_data_not_sql(dataset):
    store.ensure_datasets(dataset)
    store.write_rows(dataset, "papers", [_paper("p1"), _paper("it's")])

    store.delete_rows(dataset, "papers", "paper_id", ["it's", "x' OR '1'='1"])

    assert _paper_ids(dataset) == ["p1"]


# -- add_missing_columns: owners on tables that already exist -------------


def _as_before_owners(spec):
    """``spec`` as production created it, before owners were recorded."""
    return spec.model_copy(
        update={"columns": tuple(c for c in spec.columns if c.name != "owner_id")}
    )


def test_existing_tables_gain_an_owner_column_and_their_rows_stay_shared(dataset):
    from datetime import UTC, datetime

    for name in store.ADDED_COLUMNS:
        dataset.create(_as_before_owners(store._BY_NAME[name]))
    now = datetime.now(UTC)
    dataset.write(
        "prompts",
        [
            {
                "prompt_id": "claims_aaa",
                "name": "Claims",
                "prompt_sha256": "aaa",
                "template": "{chunk_text}",
                "output_key": "results",
                "registered_at": now,
            }
        ],
        namespace=NAMESPACE,
        mode="upsert",
    )
    dataset.write(
        "pipelines",
        [{"pipeline_id": "p1", "name": "Weekly", "inputs": {}, "created_at": now}],
        namespace=NAMESPACE,
        mode="upsert",
    )

    store.ensure_datasets(dataset)

    for name, columns in store.ADDED_COLUMNS.items():
        described = {
            c.name for c in dataset.describe(name, namespace=NAMESPACE).columns
        }
        assert set(columns) <= described, name
    assert store.schema_drift(dataset) == {}
    rows = dataset.query(
        "SELECT prompt_id, name, owner_id FROM prompts", namespace=NAMESPACE
    ).rows
    assert rows == [{"prompt_id": "claims_aaa", "name": "Claims", "owner_id": None}]
    assert dataset.query(
        "SELECT pipeline_id, owner_id FROM pipelines", namespace=NAMESPACE
    ).rows == [{"pipeline_id": "p1", "owner_id": None}]
    # Writes that name an owner are accepted now.
    store.write_rows(
        dataset,
        "pipelines",
        [{"pipeline_id": "p2", "name": "Mine", "inputs": {}, "owner_id": "user-ada"}],
    )
    assert dataset.query(
        "SELECT owner_id FROM pipelines WHERE pipeline_id = 'p2'", namespace=NAMESPACE
    ).rows == [{"owner_id": "user-ada"}]


def test_adding_owner_columns_twice_changes_nothing(dataset):
    for name in store.ADDED_COLUMNS:
        dataset.create(_as_before_owners(store._BY_NAME[name]))
    first = store.add_missing_columns(dataset)

    assert first == {name: list(cols) for name, cols in store.ADDED_COLUMNS.items()}
    assert store.add_missing_columns(dataset) == {}
    assert store.ensure_datasets(dataset) == {spec.name for spec in DATASETS} - set(
        store.ADDED_COLUMNS
    )


def test_a_column_added_but_not_yet_recorded_is_recorded(dataset):
    """A process stopped between altering the table and recording it."""
    dataset.create(_as_before_owners(store._BY_NAME["pipelines"]))
    dataset.query(
        "ALTER TABLE pipelines ADD COLUMN owner_id VARCHAR", namespace=NAMESPACE
    )

    assert store.add_missing_columns(dataset) == {"pipelines": ["owner_id"]}
    described = {
        c.name for c in dataset.describe("pipelines", namespace=NAMESPACE).columns
    }
    assert "owner_id" in described


def test_a_table_with_other_drift_is_left_alone_and_named_in_an_error(
    dataset, monkeypatch
):
    errors = []
    monkeypatch.setattr(store.logger, "error", errors.append)
    old = _as_before_owners(store._BY_NAME["pipelines"])
    drifted = old.model_copy(
        update={"columns": tuple(c for c in old.columns if c.name != "created_at")}
    )
    dataset.create(drifted)

    assert store.add_missing_columns(dataset) == {}
    [error] = errors
    assert "owner_id was not added to 'pipelines'" in error
    described = {
        c.name for c in dataset.describe("pipelines", namespace=NAMESPACE).columns
    }
    assert "owner_id" not in described
