"""Put back rows that a flush deleted in place of others.

What happened. A run that rewrites a row while the earlier version is still
inlined in the DuckLake catalog marks that version deleted. The nightly
``dataset_compaction_job`` then flushes the inlined rows to a Parquet file and
turns those marks into positions in a delete file. On 2026-11-07 (snapshot
157946) it wrote the ``solitude_where`` rows of the reruns in an order that did
not match the positions it computed: 24 rows went to the end of the file, so
every position was 24 rows late. Nine good extractions were deleted and the nine
versions they had replaced became visible again. Time travel shows the same,
because the flush rewrote the older snapshots too.

How it is found. DuckLake records, per snapshot, the rows it inserted and the
rows it deleted (``ducklake_table_insertions`` / ``ducklake_table_deletions``).
Every write to these datasets is an upsert or an outright delete, so a snapshot
that inserted rows deletes only rows whose key it re-inserted. A deleted row
whose key its own snapshot did not insert, in a snapshot that did insert rows,
was deleted by mistake.

What it does, for every ``phases_v2`` dataset:

1. a row deleted by mistake is written back as it was, but only when its key
   has no row now and no snapshot after the mistaken delete inserted or
   deleted that key. DuckLake keeps the mistaken delete in its record for
   good, so every run finds it again; a key written or deleted since was
   rewritten or removed on purpose (a re-run, ``rename_prompt``, a delete),
   and is left as is;
2. a key held by more than one row keeps the version written last (the highest
   ``snapshot_id``), and the others are deleted by their key and snapshot.
   Copies share their key and their DuckLake row id, so the snapshot is what
   tells them apart. When the newest copies of a key were written by one
   snapshot, nothing tells them apart: the key is reported and left as is.

Restoring comes first, so a run stopped in between leaves only duplicates, which
the next run removes. Nothing is projected: restored rows have the ids their
items and projections already name. A second run finds nothing to do.

Run it while no extraction run or compaction is writing: the plan is read
first, and a restore is an upsert, so a row written in between would be
overwritten.

Run on the deployment with::

    uv run abi run script src/phases_v2/migrations/repair_misapplied_deletes.py -- --dry-run
    uv run abi run script src/phases_v2/migrations/repair_misapplied_deletes.py
"""

from __future__ import annotations

import sys
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from naas_abi_core.services.dataset.DatasetPort import DatasetSpec
from naas_abi_core.services.dataset.DatasetService import DatasetService

from phases_v2.datasets import store
from phases_v2.datasets.row_store import DatasetRowStore
from phases_v2.datasets.schemas import DATASETS
from phases_v2.sql import any_row, batches, literal


@dataclass
class DatasetRepair:
    name: str
    #: Rows deleted by mistake, with the snapshot that deleted them.
    misapplied: list[dict[str, Any]] = field(default_factory=list)
    #: Of those, the rows to write back: their key has no row now and was not
    #: written or deleted since.
    restore: list[dict[str, Any]] = field(default_factory=list)
    #: The others: their key was rewritten or deleted since, or has a row now.
    left: list[dict[str, Any]] = field(default_factory=list)
    #: Older versions of keys held by more than one row: key + ``snapshot_id``.
    surplus: list[dict[str, Any]] = field(default_factory=list)
    #: Keys whose newest copies one snapshot wrote: key + ``copies``.
    tied: list[dict[str, Any]] = field(default_factory=list)

    @property
    def changed(self) -> int:
        return len(self.restore) + len(self.surplus)


@dataclass
class Report:
    dry_run: bool
    datasets: list[DatasetRepair]

    @property
    def changed(self) -> int:
        return sum(d.changed for d in self.datasets)

    def render(self) -> str:
        title = "repair of deletes applied to the wrong rows"
        if self.dry_run:
            title += " (dry run: nothing changed)"
        lines = [title]
        width = max((len(d.name) for d in self.datasets), default=0)
        for d in self.datasets:
            if not (d.misapplied or d.surplus or d.tied):
                continue
            line = (
                f"{d.name:{width}}  deleted by mistake {len(d.misapplied)}, "
                f"restored {len(d.restore)}, "
                f"left as is {len(d.left)} (rewritten or deleted since), "
                f"older copies removed {len(d.surplus)}"
            )
            if d.tied:
                line += f", left as is {len(d.tied)} keys (copies from one snapshot)"
            lines.append(line)
        if not self.changed:
            lines.append("nothing to do")
        return "\n".join(lines)


def _key(spec: DatasetSpec, row: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(row[column]) for column in spec.primary_key)


def _changes(spec: DatasetSpec, catalog: str, snapshot: int) -> str:
    """Arguments of DuckLake's change functions: the table's whole history."""
    return f"{literal(catalog)}, {literal(spec.namespace)}, {literal(spec.name)}, 1, {snapshot}"


def _misapplied(
    rows: DatasetRowStore, spec: DatasetSpec, changes: str
) -> list[dict[str, Any]]:
    keys = ", ".join(spec.primary_key)
    match = " AND ".join(f"ins.{c} = del.{c}" for c in spec.primary_key)
    columns = ", ".join(f"del.{c.name}" for c in spec.columns)
    # With no deletions at all, DuckLake returns an empty list that lacks the
    # snapshot_id column, so there is nothing to compare.
    [deleted] = rows.query(
        f"SELECT count(*) AS n FROM ducklake_table_deletions({changes})"  # nosec B608 - names come from the declared schemas
    )
    if not deleted["n"]:
        return []
    return rows.query(
        f"WITH deleted AS (SELECT snapshot_id, * FROM ducklake_table_deletions({changes})), "  # nosec B608 - names come from the declared schemas
        f"inserted AS (SELECT snapshot_id, {keys} "
        f"FROM ducklake_table_insertions({changes})) "
        f"SELECT del.snapshot_id AS deleted_at, {columns} FROM deleted del "
        "WHERE del.snapshot_id IN (SELECT snapshot_id FROM inserted) "
        "AND NOT EXISTS (SELECT 1 FROM inserted ins "
        f"WHERE ins.snapshot_id = del.snapshot_id AND {match}) "
        f"ORDER BY del.snapshot_id, {', '.join(f'del.{c}' for c in spec.primary_key)}"
    )


def _copies(
    rows: DatasetRowStore, spec: DatasetSpec
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Of the keys held by more than one row: the older versions, and the keys
    whose newest copies one snapshot wrote."""
    keys = ", ".join(spec.primary_key)
    match = " AND ".join(f"t.{c} = dup.{c}" for c in spec.primary_key)
    copies = rows.query(
        f"WITH dup AS (SELECT {keys} FROM {spec.name} GROUP BY ALL "  # nosec B608 - names come from the declared schemas
        "HAVING count(*) > 1), "
        f"v AS (SELECT t.{', t.'.join(spec.primary_key)}, t.snapshot_id, "
        f"max(t.snapshot_id) OVER (PARTITION BY {', '.join(f't.{c}' for c in spec.primary_key)}) AS latest "
        f"FROM {spec.name} t JOIN dup ON {match}) "
        f"SELECT {keys}, snapshot_id, latest FROM v ORDER BY {keys}, snapshot_id"
    )
    surplus = [
        {c: r[c] for c in (*spec.primary_key, "snapshot_id")}
        for r in copies
        if r["snapshot_id"] < r["latest"]
    ]
    newest = Counter(_key(spec, r) for r in copies if r["snapshot_id"] == r["latest"])
    tied = [
        dict(zip(spec.primary_key, key), copies=n)
        for key, n in sorted(newest.items())
        if n > 1
    ]
    return surplus, tied


def _unique_keys(
    spec: DatasetSpec, candidates: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    keys = {_key(spec, r): r for r in candidates}
    return [{c: r[c] for c in spec.primary_key} for r in keys.values()]


def _present(
    rows: DatasetRowStore, spec: DatasetSpec, candidates: list[dict[str, Any]]
) -> set[tuple[str, ...]]:
    keys = ", ".join(spec.primary_key)
    present = set()
    for batch in batches(_unique_keys(spec, candidates)):
        present |= {
            _key(spec, r)
            for r in rows.query(
                f"SELECT DISTINCT {keys} FROM {spec.name} "  # nosec B608 - values go through literal
                f"WHERE {any_row(spec.primary_key, batch)}"
            )
        }
    return present


def _last_change(
    rows: DatasetRowStore,
    spec: DatasetSpec,
    changes: str,
    candidates: list[dict[str, Any]],
) -> dict[tuple[str, ...], int]:
    """The last snapshot that inserted or deleted each candidate key."""
    keys = ", ".join(spec.primary_key)
    last = {}
    # Candidates come from a snapshot that inserted and deleted rows, so both
    # change functions return their snapshot_id column.
    for batch in batches(_unique_keys(spec, candidates)):
        for r in rows.query(
            f"SELECT {keys}, max(snapshot_id) AS last FROM ("  # nosec B608 - names come from the declared schemas; values go through literal
            f"SELECT snapshot_id, {keys} FROM ducklake_table_insertions({changes}) "
            f"UNION ALL SELECT snapshot_id, {keys} "
            f"FROM ducklake_table_deletions({changes})) "
            f"WHERE {any_row(spec.primary_key, batch)} GROUP BY ALL"
        ):
            last[_key(spec, r)] = int(r["last"])
    return last


def plan(dataset: DatasetService) -> list[DatasetRepair]:
    rows = DatasetRowStore(dataset)
    [where] = rows.query("SELECT current_database() AS catalog")
    snapshot = rows.snapshot()
    repairs = []
    if snapshot is None:
        return repairs
    for spec in DATASETS:
        changes = _changes(spec, where["catalog"], snapshot)
        repair = DatasetRepair(spec.name)
        repair.misapplied = _misapplied(rows, spec, changes)
        present = _present(rows, spec, repair.misapplied)
        last = _last_change(rows, spec, changes, repair.misapplied)
        decided: set[tuple[str, ...]] = set()
        # The version deleted last is the newest one; earlier ones of the same
        # key were superseded by it.
        for row in reversed(repair.misapplied):
            key = _key(spec, row)
            since = last.get(key, row["deleted_at"]) > row["deleted_at"]
            if key in decided or key in present or since:
                repair.left.insert(0, row)
            else:
                repair.restore.insert(0, {c.name: row[c.name] for c in spec.columns})
            decided.add(key)
        repair.surplus, repair.tied = _copies(rows, spec)
        repairs.append(repair)
    return repairs


def repair(dataset: DatasetService, *, dry_run: bool = False) -> Report:
    repairs = plan(dataset)
    if not dry_run:
        for item in repairs:
            store.write_rows(dataset, item.name, item.restore)
        for item in repairs:
            store.delete_versions(dataset, item.name, item.surplus)
    return Report(dry_run=dry_run, datasets=repairs)


def main(engine: Any, argv: list[str]) -> Report:
    report = repair(engine.services.dataset, dry_run="--dry-run" in argv)
    print(report.render(), flush=True)
    return report


if __name__ == "__main__":
    from phases_v2 import ABIModule

    main(ABIModule.get_instance().engine, sys.argv[1:])
