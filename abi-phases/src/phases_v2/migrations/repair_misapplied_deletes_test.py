"""Undoing deletes that hit the wrong rows, on a real DuckLake catalog."""

from datetime import datetime

import duckdb
import pytest
from naas_abi_core.services.dataset.adapters.secondary.DatasetSecondaryAdapterDuckLake import (
    DatasetSecondaryAdapterDuckLake,
)
from naas_abi_core.services.dataset.DatasetService import DatasetService

from phases_v2.datasets import store
from phases_v2.datasets.schemas import NAMESPACE
from phases_v2.migrations.repair_misapplied_deletes import repair

WHEN = datetime(2026, 11, 5, 8, 40)


def _row(key, run, status="succeeded", response=None):
    return {
        "extraction_id": key,
        "chunk_id": f"chunk-{key}",
        "model_id": "openai/gpt-4.1-mini",
        "prompt_id": "solitude_where_eadbe78f900c",
        "run_id": run,
        "status": status,
        "response": response,
        "raw_response": "{}",
        "error": None if status == "succeeded" else "Connection error.",
        "item_count": len((response or {}).get("where", [])),
        "completed_at": WHEN,
    }


@pytest.fixture
def lake(tmp_path):
    catalog, data = tmp_path / "catalog.sqlite", str(tmp_path / "data") + "/"
    dataset = DatasetService(
        adapter=DatasetSecondaryAdapterDuckLake(
            catalog=f"sqlite:{catalog}", data_path=data
        )
    )
    store.ensure_datasets(dataset)

    def raw(*statements):
        """Run ``statements`` in one DuckDB transaction, bypassing the store:
        the only way to leave what a storage fault leaves."""
        con = duckdb.connect()
        con.execute("LOAD ducklake")
        con.execute(f"ATTACH 'ducklake:sqlite:{catalog}' AS lake (DATA_PATH '{data}')")
        con.execute("BEGIN")
        for sql, params in statements:
            con.execute(sql.replace("{table}", f"lake.{NAMESPACE}.extractions"), params)
        con.execute("COMMIT")
        con.close()

    def insert(row):
        columns = ", ".join(row)
        values = ", ".join("?" for _ in row)
        return (
            f"INSERT INTO {{table}} ({columns}) VALUES ({values})",
            [
                None if v is None else str(v) if k == "response" else v
                for k, v in row.items()
            ],
        )

    def fault(new_version, deleted_key):
        """One snapshot that inserts ``new_version`` and deletes ``deleted_key``'s
        row, leaving the version it replaced: what the bad flush left behind."""
        raw(
            insert(new_version),
            ("DELETE FROM {table} WHERE extraction_id = ?", [deleted_key]),
        )

    def copies(row, n):
        """``n`` copies of ``row`` written by one snapshot."""
        raw(*[insert(row)] * n)

    return dataset, fault, copies


def _extractions(dataset):
    return sorted(
        (
            (r["extraction_id"], r["run_id"], r["status"], r["response"])
            for r in dataset.query(
                "SELECT * FROM extractions", namespace=NAMESPACE
            ).rows
        ),
    )


def _seed(dataset, fault):
    good = _row("b", "ef543e81", response={"where": ["at home", "outdoors"]})
    store.write_rows(
        dataset,
        "extractions",
        [_row("a", "ef543e81", "failed"), good, _row("c", "r1"), _row("d", "r1")],
    )
    # A real upsert and a real delete: both must be left as they are.
    store.write_rows(dataset, "extractions", [_row("c", "r2")])
    store.delete_rows(dataset, "extractions", "extraction_id", ["d"])
    fault(_row("a", "a087ca55"), "b")
    return good


def test_a_lost_row_is_restored_and_the_stale_copy_removed(lake):
    dataset, fault, _ = lake
    good = _seed(dataset, fault)
    assert [r[:2] for r in _extractions(dataset)] == [
        ("a", "a087ca55"),
        ("a", "ef543e81"),
        ("c", "r2"),
    ]

    report = repair(dataset)

    assert _extractions(dataset) == [
        ("a", "a087ca55", "succeeded", None),
        ("b", "ef543e81", "succeeded", {"where": ["at home", "outdoors"]}),
        ("c", "r2", "succeeded", None),
    ]
    [restored] = dataset.query(
        "SELECT * FROM extractions WHERE extraction_id = 'b'", namespace=NAMESPACE
    ).rows
    assert restored == dict(good, completed_at=WHEN.isoformat())
    [extractions] = [d for d in report.datasets if d.name == "extractions"]
    assert [r["extraction_id"] for r in extractions.misapplied] == ["b"]
    assert len(extractions.restore) == 1
    assert [r["extraction_id"] for r in extractions.surplus] == ["a"]
    assert (
        "deleted by mistake 1, restored 1, left as is 0 (rewritten or deleted since), "
        "older copies removed 1"
    ) in report.render()


def test_a_dry_run_changes_nothing(lake):
    dataset, fault, _ = lake
    _seed(dataset, fault)
    before = _extractions(dataset)

    dry = repair(dataset, dry_run=True)

    assert _extractions(dataset) == before
    assert dry.changed == 2
    assert "dry run" in dry.render()


def test_a_second_run_finds_nothing_to_do(lake):
    dataset, fault, _ = lake
    _seed(dataset, fault)
    repair(dataset)
    after = _extractions(dataset)

    again = repair(dataset)

    assert again.changed == 0
    assert "nothing to do" in again.render()
    assert _extractions(dataset) == after


def test_the_repair_survives_a_flush_and_a_compaction(lake):
    dataset, fault, _ = lake
    _seed(dataset, fault)
    repair(dataset)
    after = _extractions(dataset)

    dataset.flush("extractions", namespace=NAMESPACE)
    dataset.compact("extractions", namespace=NAMESPACE)

    assert _extractions(dataset) == after
    assert repair(dataset).changed == 0


def test_a_clean_store_has_nothing_to_repair(lake):
    dataset, _, _ = lake
    store.write_rows(dataset, "extractions", [_row("a", "r1")])
    store.write_rows(dataset, "extractions", [_row("a", "r2")])
    store.delete_rows(dataset, "extractions", "extraction_id", ["a"])

    report = repair(dataset)

    assert report.changed == 0
    assert all(not d.misapplied for d in report.datasets)


def test_a_key_deleted_on_purpose_after_the_repair_stays_deleted(lake):
    dataset, fault, _ = lake
    _seed(dataset, fault)
    repair(dataset)
    # DuckLake keeps the mistaken delete in its record: every run sees it.
    store.delete_rows(dataset, "extractions", "extraction_id", ["b"])
    after = _extractions(dataset)

    again = repair(dataset)

    assert _extractions(dataset) == after
    assert "b" not in {r[0] for r in after}
    [extractions] = [d for d in again.datasets if d.name == "extractions"]
    assert [r["extraction_id"] for r in extractions.misapplied] == ["b"]
    assert extractions.restore == []
    assert [r["extraction_id"] for r in extractions.left] == ["b"]
    assert again.changed == 0
    assert "left as is 1 (rewritten or deleted since)" in again.render()
    assert "nothing to do" in again.render()


def test_a_key_rewritten_and_deleted_before_the_repair_is_left_as_is(lake):
    dataset, fault, _ = lake
    _seed(dataset, fault)
    store.write_rows(dataset, "extractions", [_row("b", "r3")])
    store.delete_rows(dataset, "extractions", "extraction_id", ["b"])

    report = repair(dataset)

    [extractions] = [d for d in report.datasets if d.name == "extractions"]
    assert extractions.restore == []
    assert [r["extraction_id"] for r in extractions.left] == ["b"]
    assert "b" not in {r[0] for r in _extractions(dataset)}


def test_copies_one_snapshot_wrote_are_reported_and_left(lake):
    dataset, _, copies = lake
    copies(_row("e", "r1"), 2)

    report = repair(dataset)

    [extractions] = [d for d in report.datasets if d.name == "extractions"]
    assert extractions.surplus == []
    assert [(t["extraction_id"], t["copies"]) for t in extractions.tied] == [("e", 2)]
    assert report.changed == 0
    assert "left as is 1 keys (copies from one snapshot)" in report.render()
    assert [r[0] for r in _extractions(dataset)] == ["e", "e"]
