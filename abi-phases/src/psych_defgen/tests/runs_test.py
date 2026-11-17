import pytest
from naas_abi_core.services.dataset.DatasetPort import DatasetSnapshotConflictError

from psych_defgen.adapters.secondary.definition_run_store import ATTEMPTS
from psych_defgen.application.definition_service import STAGES
from psych_defgen.application.runs import FINISH_ATTEMPTS, new_run_id
from psych_defgen.domain.errors import (
    DefinitionStepError,
    InvalidRunError,
    NotConfiguredError,
    RunNotFoundError,
)
from psych_defgen.tests.conftest import RESULT, FakeDefiner, make_runs


def test_create_fills_the_defaults_and_trims_the_term(runs):
    run = runs.create({"term": "  loneliness \n"})
    assert run == {
        "run_id": "run0001",
        "term": "loneliness",
        "max_results": 100,
        "top_k": 10,
        "model": "openai/gpt-4.1-mini",
        "embedding_model": "text-embedding-3-small",
        "status": "pending",
        "stage": None,
        "error": None,
        "created_at": "2026-11-16T10:00:00Z",
        "started_at": None,
        "finished_at": None,
        "result": None,
    }
    assert runs.get("run0001") == run


def test_create_accepts_the_limits_and_an_offered_model(runs):
    run = runs.create(
        {
            "term": "x" * 200,
            "max_results": 200,
            "top_k": 1,
            "model": "anthropic/claude-sonnet-4",
        }
    )
    assert (run["max_results"], run["top_k"], run["model"]) == (
        200,
        1,
        "anthropic/claude-sonnet-4",
    )
    assert runs.create({"term": "a", "max_results": 1, "top_k": 20})["top_k"] == 20


@pytest.mark.parametrize(
    "payload, field",
    [
        (None, "JSON object"),
        (["loneliness"], "JSON object"),
        ({}, "term"),
        ({"term": ""}, "term"),
        ({"term": "   "}, "term"),
        ({"term": "x" * 201}, "term"),
        ({"term": 7}, "term"),
        ({"term": "a", "max_results": 0}, "max_results"),
        ({"term": "a", "max_results": 201}, "max_results"),
        ({"term": "a", "max_results": 1.5}, "max_results"),
        ({"term": "a", "max_results": True}, "max_results"),
        ({"term": "a", "max_results": "many"}, "max_results"),
        ({"term": "a", "max_results": "5"}, "max_results"),
        ({"term": "a", "top_k": 0}, "top_k"),
        ({"term": "a", "top_k": 21}, "top_k"),
        ({"term": "a", "model": "openai/gpt-5"}, "model"),
        ({"term": "a", "model": ""}, "model"),
    ],
)
def test_create_rejects_out_of_range_requests(runs, store, payload, field):
    with pytest.raises(InvalidRunError, match=field):
        runs.create(payload)
    assert store.page(offset=0, limit=10) == (0, [])


def test_create_names_missing_keys_and_stores_nothing(store):
    runs = make_runs(store, ncbi_email="", openai_api_key=None)
    with pytest.raises(NotConfiguredError) as raised:
        runs.create({"term": "loneliness"})
    assert raised.value.missing == ["NCBI_EMAIL", "OPENAI_API_KEY"]
    assert "NCBI_EMAIL, OPENAI_API_KEY" in str(raised.value)
    assert store.page(offset=0, limit=10) == (0, [])
    assert runs.settings()["missing"] == ["NCBI_EMAIL", "OPENAI_API_KEY"]


def test_settings_follow_the_configuration(runs):
    assert runs.settings() == {
        "models": ["openai/gpt-4.1-mini", "anthropic/claude-sonnet-4"],
        "default_model": "openai/gpt-4.1-mini",
        "embedding_model": "text-embedding-3-small",
        "max_results": {"default": 100, "min": 1, "max": 200},
        "top_k": {"default": 10, "min": 1, "max": 20},
        "missing": [],
    }


def test_list_pages_newest_first_without_results(runs):
    for index in range(30):
        runs.create({"term": f"term {index}"})
    first = runs.list(1, 25)
    assert (first["total"], first["page"], first["page_count"]) == (30, 1, 2)
    assert [r["term"] for r in first["runs"][:2]] == ["term 29", "term 28"]
    assert len(first["runs"]) == 25
    assert all("result" not in r for r in first["runs"])
    second = runs.list("2", "25")
    assert [r["term"] for r in second["runs"]] == [
        f"term {i}" for i in range(4, -1, -1)
    ]
    assert runs.list(3, 25)["runs"] == []
    assert runs.list()["page_count"] == 2  # page 1, 25 per page by default
    assert runs.list(1, 100)["page_count"] == 1


def test_an_empty_history_has_no_pages(runs):
    assert runs.list() == {"runs": [], "total": 0, "page": 1, "page_count": 0}


@pytest.mark.parametrize(
    "page, page_size", [(0, 25), (-1, 25), ("x", 25), (1, 0), (1, 101), (1, "")]
)
def test_list_rejects_bad_pages(runs, page, page_size):
    with pytest.raises(InvalidRunError):
        runs.list(page, page_size)


def test_get_unknown_run(runs):
    with pytest.raises(RunNotFoundError):
        runs.get("nope")


def test_a_run_goes_pending_running_succeeded(runs):
    run_id = runs.create({"term": "loneliness"})["run_id"]
    assert [r["run_id"] for r in runs.pending()] == [run_id]

    started = runs.start(run_id)
    assert started["status"] == "running"
    assert started["started_at"] == "2026-11-16T10:00:01Z"
    assert runs.start(run_id) is None  # already started
    assert runs.pending() == []
    assert [r["status"] for r in runs.active()] == ["running"]

    runs.set_stage(run_id, "search")
    assert runs.get(run_id)["stage"] == "search"
    with pytest.raises(ValueError):
        runs.set_stage(run_id, "dreaming")

    runs.succeed(run_id, RESULT)
    run = runs.get(run_id)
    assert run["status"] == "succeeded"
    assert run["result"] == RESULT
    assert run["finished_at"] == "2026-11-16T10:00:02Z"
    assert runs.active() == []

    # A finished run stays finished.
    runs.fail(run_id, "too late")
    runs.set_stage(run_id, "apa")
    assert runs.get(run_id)["status"] == "succeeded"
    assert runs.get(run_id)["stage"] == "search"


def test_a_run_can_fail_before_or_while_running(runs):
    waiting = runs.create({"term": "a"})["run_id"]
    runs.fail(waiting, "Dagster run d1 was canceled. Start a new run.")
    run = runs.get(waiting)
    assert (run["status"], run["error"], run["result"]) == (
        "failed",
        "Dagster run d1 was canceled. Start a new run.",
        None,
    )
    assert runs.start(waiting) is None
    with_stage = runs.create({"term": "b"})["run_id"]
    runs.start(with_stage)
    runs.fail(with_stage, "stopped")
    assert runs.get(with_stage)["status"] == "failed"
    runs.succeed(with_stage, RESULT)  # a failed run is not revived
    assert runs.get(with_stage)["result"] is None


def test_stage_writes_happen_only_on_changes(runs, store, monkeypatch):
    run_id = runs.create({"term": "a"})["run_id"]
    runs.start(run_id)
    writes = []
    real = store.dataset.write
    monkeypatch.setattr(
        store.dataset,
        "write",
        lambda *a, **k: writes.append(a[0]) or real(*a, **k),
    )
    runs.set_stage(run_id, "apa")
    runs.set_stage(run_id, "apa")
    assert len(writes) == 1


def test_execute_stores_the_result_and_each_stage(runs):
    run_id = runs.create({"term": "loneliness", "max_results": 5, "top_k": 3})["run_id"]
    stages = []
    definer = FakeDefiner(seen=lambda _: stages.append(runs.get(run_id)["stage"]))

    assert runs.execute(run_id, lambda: definer, job_run_id="d1") == "succeeded"

    assert definer.calls == [("loneliness", 5, 3, "openai/gpt-4.1-mini")]
    assert stages == list(STAGES)
    run = runs.get(run_id)
    assert run["status"] == "succeeded"
    assert run["result"]["term"] == "loneliness"
    assert run["error"] is None
    assert run["started_at"] and run["finished_at"]


def test_execute_stores_a_step_failure(runs):
    run_id = runs.create({"term": "loneliness"})["run_id"]
    error = DefinitionStepError(
        "ranking", "OpenAI refused the request (HTTP 401); check OPENAI_API_KEY."
    )
    status = runs.execute(
        run_id, lambda: FakeDefiner(error=error, fail_at="ranking"), job_run_id="d1"
    )
    assert status == "failed"
    run = runs.get(run_id)
    assert run["status"] == "failed"
    assert run["stage"] == "ranking"
    assert run["error"] == (
        "Step 'ranking' failed: OpenAI refused the request (HTTP 401); "
        "check OPENAI_API_KEY."
    )
    assert run["result"] is None


def test_execute_fails_a_run_when_keys_went_missing(runs):
    run_id = runs.create({"term": "loneliness"})["run_id"]

    def unconfigured():
        raise NotConfiguredError(["OPENROUTER_API_KEY"])

    assert runs.execute(run_id, unconfigured, job_run_id="d1") == "failed"
    assert "OPENROUTER_API_KEY" in runs.get(run_id)["error"]


def test_execute_points_unexpected_errors_to_the_job_logs(runs):
    run_id = runs.create({"term": "loneliness"})["run_id"]
    error = KeyError("sk-or-v1-secret-value")  # an error text is never stored
    with pytest.raises(KeyError):
        runs.execute(
            run_id,
            lambda: FakeDefiner(error=error, fail_at="selection"),
            job_run_id="d1",
        )
    run = runs.get(run_id)
    assert run["status"] == "failed"
    assert run["error"] == (
        "The run stopped on an unexpected KeyError during stage 'selection'. "
        "See Dagster run d1 for details, then start a new run."
    )


def test_execute_skips_a_run_that_is_not_pending(runs):
    run_id = runs.create({"term": "loneliness"})["run_id"]
    runs.fail(run_id, "canceled")
    definer = FakeDefiner()
    assert runs.execute(run_id, lambda: definer, job_run_id="d1") == "failed"
    assert definer.calls == []


def test_run_ids_sort_by_creation_time():
    first = new_run_id()
    assert len(first) == 32 and int(first, 16) >= 0
    assert new_run_id()[:12] >= first[:12]


class Contention:
    """Makes the dataset refuse matching writes with a snapshot conflict,
    as a busy catalog does when other modules commit in between."""

    def __init__(self, dataset, monkeypatch, refuse):
        self.real_write = dataset.write
        self.refuse = refuse  # (row) -> how many more times to refuse it
        self.refused = 0
        monkeypatch.setattr(dataset, "write", self.write)

    def write(self, name, rows, **options):
        if options.get("snapshot_id") is not None and self.refuse(rows[0]):
            self.refused += 1
            raise DatasetSnapshotConflictError(
                options["snapshot_id"], options["snapshot_id"] + 1
            )
        return self.real_write(name, rows, **options)


def test_a_stage_that_cannot_be_stored_does_not_stop_the_run(runs, store, monkeypatch):
    run_id = runs.create({"term": "loneliness"})["run_id"]
    busy = Contention(store.dataset, monkeypatch, lambda row: row["stage"] == "ranking")

    assert runs.execute(run_id, FakeDefiner, job_run_id="d1") == "succeeded"

    assert busy.refused == ATTEMPTS  # every attempt at the ranking stage
    run = runs.get(run_id)
    assert run["status"] == "succeeded"
    assert run["result"]["term"] == "loneliness"


def test_the_result_survives_a_long_run_of_conflicts(runs, store, monkeypatch):
    run_id = runs.create({"term": "loneliness"})["run_id"]
    refusals = iter(range(3 * ATTEMPTS))
    busy = Contention(
        store.dataset,
        monkeypatch,
        lambda row: row["status"] == "succeeded" and next(refusals, None) is not None,
    )

    assert runs.execute(run_id, FakeDefiner, job_run_id="d1") == "succeeded"

    assert busy.refused == 3 * ATTEMPTS
    assert runs.get(run_id)["result"]["term"] == "loneliness"


def test_a_result_that_cannot_be_stored_fails_the_run_with_the_reason(runs):
    run_id = runs.create({"term": "loneliness"})["run_id"]

    class NaNDefiner(FakeDefiner):
        def define(self, term, **options):
            return {**super().define(term, **options), "score": float("nan")}

    with pytest.raises(ValueError):
        runs.execute(run_id, NaNDefiner, job_run_id="d7")

    run = runs.get(run_id)
    assert run["status"] == "failed"
    assert run["error"] == (
        "The result could not be stored (ValueError). "
        "See Dagster run d7 for details, then start a new run."
    )
    assert run["result"] is None


def test_a_start_survives_a_long_run_of_conflicts(runs, store, monkeypatch):
    run_id = runs.create({"term": "loneliness"})["run_id"]
    refusals = iter(range(20))
    busy = Contention(
        store.dataset,
        monkeypatch,
        lambda row: (
            row["status"] == "running"
            and row["stage"] is None
            and next(refusals, None) is not None
        ),
    )

    assert runs.execute(run_id, FakeDefiner, job_run_id="d1") == "succeeded"

    assert busy.refused == 20 > ATTEMPTS
    run = runs.get(run_id)
    assert run["status"] == "succeeded"
    assert run["started_at"] is not None


def test_starting_a_run_waits_as_long_as_finishing_it(store):
    pauses = []
    store.pause = pauses.append
    runs = make_runs(store)
    run_id = runs.create({"term": "a"})["run_id"]

    def always_refuse(name, rows, **options):
        raise DatasetSnapshotConflictError(1, 2)

    store.dataset.write = always_refuse
    with pytest.raises(DatasetSnapshotConflictError):
        runs.start(run_id)
    assert len(pauses) == FINISH_ATTEMPTS - 1


def test_finishing_writes_wait_about_fifty_seconds_at_most(store):
    pauses = []
    store.pause = pauses.append
    runs = make_runs(store)
    run_id = runs.create({"term": "a"})["run_id"]
    runs.start(run_id)

    def always_refuse(name, rows, **options):
        raise DatasetSnapshotConflictError(1, 2)

    store.dataset.write = always_refuse
    with pytest.raises(DatasetSnapshotConflictError):
        runs.succeed(run_id, RESULT)
    assert len(pauses) == FINISH_ATTEMPTS - 1
    assert 45 < sum(pauses) < 55


def test_a_result_for_a_run_failed_meanwhile_is_not_reported_as_stored(
    runs, caplog_messages
):
    run_id = runs.create({"term": "loneliness"})["run_id"]

    def released(stage):
        if stage == "synthesis":  # e.g. a sensor fails the run meanwhile
            runs.fail(run_id, "Dagster run d0 was canceled. Start a new run.")

    status = runs.execute(run_id, lambda: FakeDefiner(seen=released), job_run_id="d1")

    assert status == "failed"
    run = runs.get(run_id)
    assert run["result"] is None
    assert run["error"] == "Dagster run d0 was canceled. Start a new run."
    assert any("result was discarded" in m for m in caplog_messages)


@pytest.mark.parametrize("text", ["1_0", "١", "2.0", "+-1", "--1", "0x10", ""])
def test_page_numbers_are_plain_digits(runs, text):
    with pytest.raises(InvalidRunError):
        runs.list(text, "25")


def test_page_numbers_may_have_surrounding_spaces(runs):
    assert runs.list(" 1 ", "10 ")["page"] == 1
    with pytest.raises(InvalidRunError, match="from 1 to"):
        runs.list("-1", "25")
