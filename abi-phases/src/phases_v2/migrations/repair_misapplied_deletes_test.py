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

    def fault(new_version, deleted_key):
        """One snapshot that inserts ``new_version`` and deletes ``deleted_key``'s
        row, leaving the version it replaced: what the bad flush left behind."""
        con = duckdb.connect()
        con.execute("LOAD ducklake")
        con.execute(f"ATTACH 'ducklake:sqlite:{catalog}' AS lake (DATA_PATH '{data}')")
        columns = ", ".join(new_version)
        values = ", ".join("?" for _ in new_version)
        con.execute("BEGIN")
        con.execute(
            f"INSERT INTO lake.{NAMESPACE}.extractions ({columns}) VALUES ({values})",
            [
                None if v is None else str(v) if k == "response" else v
                for k, v in new_version.items()
            ],
        )
        con.execute(
            f"DELETE FROM lake.{NAMESPACE}.extractions WHERE extraction_id = ?",
            [deleted_key],
        )
        con.execute("COMMIT")
        con.close()

    return dataset, fault


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
    dataset, fault = lake
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
    assert "deleted by mistake 1, restored 1, older copies removed 1" in report.render()


def test_a_dry_run_changes_nothing(lake):
    dataset, fault = lake
    _seed(dataset, fault)
    before = _extractions(dataset)

    dry = repair(dataset, dry_run=True)

    assert _extractions(dataset) == before
    assert dry.changed == 2
    assert "dry run" in dry.render()


def test_a_second_run_finds_nothing_to_do(lake):
    dataset, fault = lake
    _seed(dataset, fault)
    repair(dataset)
    after = _extractions(dataset)

    again = repair(dataset)

    assert again.changed == 0
    assert "nothing to do" in again.render()
    assert _extractions(dataset) == after


def test_the_repair_survives_a_flush_and_a_compaction(lake):
    dataset, fault = lake
    _seed(dataset, fault)
    repair(dataset)
    after = _extractions(dataset)

    dataset.flush("extractions", namespace=NAMESPACE)
    dataset.compact("extractions", namespace=NAMESPACE)

    assert _extractions(dataset) == after
    assert repair(dataset).changed == 0


def test_a_clean_store_has_nothing_to_repair(lake):
    dataset, _ = lake
    store.write_rows(dataset, "extractions", [_row("a", "r1")])
    store.write_rows(dataset, "extractions", [_row("a", "r2")])
    store.delete_rows(dataset, "extractions", "extraction_id", ["a"])

    report = repair(dataset)

    assert report.changed == 0
    assert all(not d.misapplied for d in report.datasets)
