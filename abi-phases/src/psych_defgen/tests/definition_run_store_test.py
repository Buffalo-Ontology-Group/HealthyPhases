import pytest

from psych_defgen.adapters.secondary.definition_run_store import (
    NAMESPACE,
    TABLE,
    DefinitionRunStore,
)
from psych_defgen.domain.errors import RunNotFoundError

COLUMNS = [
    "run_id",
    "term",
    "max_results",
    "top_k",
    "model",
    "embedding_model",
    "status",
    "stage",
    "error",
    "created_at",
    "started_at",
    "finished_at",
    "result",
]


def row(run_id, created_at, status="pending", **values):
    return {
        "run_id": run_id,
        "term": "loneliness",
        "max_results": 100,
        "top_k": 10,
        "model": "openai/gpt-4.1-mini",
        "embedding_model": "text-embedding-3-small",
        "status": status,
        "stage": None,
        "error": None,
        "created_at": created_at,
        "started_at": None,
        "finished_at": None,
        "result": None,
        **values,
    }


def test_ensure_creates_the_table_once_and_keeps_its_rows(dataset):
    store = DefinitionRunStore(dataset)
    store.ensure()
    info = dataset.describe(TABLE, namespace=NAMESPACE)
    assert [column.name for column in info.columns] == COLUMNS
    assert info.primary_key == ("run_id",)

    store.insert(row("a", "2026-11-16T10:00:00Z"))
    store.ensure()
    DefinitionRunStore(dataset).ensure()
    assert store.get("a")["term"] == "loneliness"


def test_rows_round_trip_with_types(store):
    store.insert(row("a", "2026-11-16T10:00:00Z", result='{"term": "x"}'))
    stored = store.get("a")
    assert stored == row("a", "2026-11-16T10:00:00Z", result='{"term": "x"}')
    assert isinstance(stored["max_results"], int)
    with pytest.raises(RunNotFoundError):
        store.get("missing")
    with pytest.raises(RunNotFoundError):
        store.get("x' OR '1'='1")


def test_page_is_newest_first_without_results_and_counts_all(store):
    for index in range(5):
        store.insert(row(f"r{index}", f"2026-11-16T10:00:0{index}Z", result="{}"))
    total, rows = store.page(offset=0, limit=2)
    assert total == 5
    assert [r["run_id"] for r in rows] == ["r4", "r3"]
    assert all("result" not in r for r in rows)
    total, rows = store.page(offset=4, limit=2)
    assert (total, [r["run_id"] for r in rows]) == (5, ["r0"])
    assert store.page(offset=10, limit=2) == (5, [])


def test_an_empty_table_has_no_pages(store):
    assert store.page(offset=0, limit=25) == (0, [])


def test_with_status_is_oldest_first(store):
    store.insert(row("late", "2026-11-16T10:00:02Z"))
    store.insert(row("early", "2026-11-16T10:00:01Z"))
    store.insert(row("busy", "2026-11-16T10:00:00Z", status="running"))
    store.insert(row("done", "2026-11-16T09:00:00Z", status="succeeded"))
    assert [r["run_id"] for r in store.with_status("pending")] == ["early", "late"]
    assert [r["run_id"] for r in store.with_status("pending", "running")] == [
        "busy",
        "early",
        "late",
    ]


def test_update_retries_on_a_concurrent_commit_and_sees_its_change(
    store, dataset, monkeypatch
):
    store.insert(row("a", "2026-11-16T10:00:00Z"))
    store.insert(row("b", "2026-11-16T10:00:01Z"))
    real_write = dataset.write
    interfered = []

    def write(name, rows, **options):
        if options.get("snapshot_id") is not None and not interfered:
            interfered.append(True)
            # Another writer finishes run "a" between our read and our write.
            real_write(
                TABLE,
                [row("a", "2026-11-16T10:00:00Z", status="failed", error="stop")],
                namespace=NAMESPACE,
                mode="upsert",
            )
        return real_write(name, rows, **options)

    monkeypatch.setattr(dataset, "write", write)
    seen = []

    def start(current):
        seen.append(current["status"])
        if current["status"] != "pending":
            return None
        return {**current, "status": "running"}

    assert store.update("a", start)["status"] == "failed"
    assert seen == ["pending", "failed"]
    assert store.get("a")["error"] == "stop"
    assert store.update("b", start)["status"] == "running"
    assert store.get("b")["status"] == "running"


def test_update_without_a_change_does_not_write(store, dataset, monkeypatch):
    store.insert(row("a", "2026-11-16T10:00:00Z"))
    writes = []
    monkeypatch.setattr(dataset, "write", lambda *a, **k: writes.append(a))
    assert store.update("a", lambda current: None)["status"] == "pending"
    assert store.update("a", lambda current: dict(current))["status"] == "pending"
    assert writes == []
