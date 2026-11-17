import dagster as dg
import pytest
from dagster._core.storage.dagster_run import DagsterRun
from fastapi import FastAPI
from fastapi.testclient import TestClient

from psych_defgen.application.definition_service import STAGES
from psych_defgen.domain.errors import DefinitionStepError
from psych_defgen.orchestrations import psych_defgen_orchestration as orchestration
from psych_defgen.tests.conftest import SECRETS, FakeDefiner

TAG = orchestration.TAG


@pytest.fixture
def instance():
    with dg.DagsterInstance.ephemeral() as instance:
        yield instance


@pytest.fixture
def wired(runs, monkeypatch):
    """The orchestration on the test runs service, with a fake definer."""
    state = {"definer": FakeDefiner()}
    monkeypatch.setattr(orchestration, "runs", lambda: runs)
    monkeypatch.setattr(orchestration, "dataset_available", lambda: True)
    monkeypatch.setattr(orchestration, "definer", lambda: state["definer"])
    return state


def tick(instance):
    return orchestration.run_request_sensor(dg.build_sensor_context(instance=instance))


def add_dagster_run(instance, run_id, status, dagster_run_id="d1"):
    instance.add_run(
        DagsterRun(
            job_name=orchestration.JOB,
            run_id=dagster_run_id,
            tags={TAG: run_id},
            status=status,
        )
    )


def launch(instance, request):
    return orchestration.run_job.execute_in_process(
        run_config=request.run_config,
        tags=request.tags,
        instance=instance,
        raise_on_error=False,
    )


def test_run_requests_use_the_run_id_as_run_key():
    requests = orchestration.build_run_requests([{"run_id": "a"}, {"run_id": "b"}])
    assert [r.run_key for r in requests] == ["a", "b"]
    assert requests[0].tags == {TAG: "a"}
    assert requests[0].run_config == {
        "ops": {"define_construct": {"config": {"run_id": "a"}}}
    }


def test_the_sensor_launches_the_oldest_pending_run(runs, wired, instance):
    assert isinstance(tick(instance), dg.SkipReason)
    first = runs.create({"term": "a"})["run_id"]
    runs.create({"term": "b"})
    requests = tick(instance)
    assert [r.run_key for r in requests] == [first]


def test_the_sensor_waits_while_a_run_is_in_progress(runs, wired, instance):
    busy = runs.create({"term": "a"})["run_id"]
    runs.create({"term": "b"})
    runs.start(busy)
    add_dagster_run(instance, busy, dg.DagsterRunStatus.STARTED)
    result = tick(instance)
    assert isinstance(result, dg.SkipReason)
    assert "in progress" in result.skip_message
    assert runs.get(busy)["status"] == "running"


def test_a_launched_run_that_has_not_started_is_not_launched_twice(
    runs, wired, instance
):
    waiting = runs.create({"term": "a"})["run_id"]
    add_dagster_run(instance, waiting, dg.DagsterRunStatus.STARTING)
    # Same run key: Dagster drops the duplicate request.
    assert [r.run_key for r in tick(instance)] == [waiting]
    assert runs.get(waiting)["status"] == "pending"


@pytest.mark.parametrize(
    "status, dagster_status, message",
    [
        ("running", None, "no longer exists"),
        ("running", dg.DagsterRunStatus.CANCELED, "Dagster run d1 was canceled"),
        ("running", dg.DagsterRunStatus.SUCCESS, "Dagster run d1 ended (success)"),
        ("pending", dg.DagsterRunStatus.FAILURE, "Dagster run d1 ended (failure)"),
    ],
)
def test_the_sensor_releases_runs_whose_dagster_run_ended(
    runs, wired, instance, status, dagster_status, message
):
    stuck = runs.create({"term": "a"})["run_id"]
    following = runs.create({"term": "b"})["run_id"]
    if status == "running":
        runs.start(stuck)
    if dagster_status is not None:
        add_dagster_run(instance, stuck, dagster_status)
    requests = tick(instance)
    run = runs.get(stuck)
    assert run["status"] == "failed"
    assert message in run["error"]
    assert [r.run_key for r in requests] == [following]


def test_the_job_succeeds_and_stores_each_stage(runs, wired, instance):
    run_id = runs.create({"term": "loneliness", "max_results": 5})["run_id"]
    stages = []
    wired["definer"] = FakeDefiner(
        seen=lambda _: stages.append(runs.get(run_id)["stage"])
    )
    (request,) = tick(instance)

    outcome = launch(instance, request)

    assert outcome.success
    assert stages == list(STAGES)
    run = runs.get(run_id)
    assert run["status"] == "succeeded"
    assert run["result"]["term"] == "loneliness"
    assert wired["definer"].calls == [("loneliness", 5, 10, "openai/gpt-4.1-mini")]


def test_the_job_stores_a_step_failure_and_fails(runs, wired, instance):
    run_id = runs.create({"term": "loneliness"})["run_id"]
    wired["definer"] = FakeDefiner(
        error=DefinitionStepError("search", "NCBI did not answer; try again later."),
        fail_at="search",
    )
    (request,) = tick(instance)

    outcome = launch(instance, request)

    assert not outcome.success
    run = runs.get(run_id)
    assert (run["status"], run["stage"]) == ("failed", "search")
    assert run["error"] == (
        "Step 'search' failed: NCBI did not answer; try again later."
    )


def test_the_failure_sensor_marks_the_run_failed(runs, wired, instance):
    run_id = runs.create({"term": "loneliness"})["run_id"]
    runs.start(run_id)
    dagster_run = DagsterRun(
        job_name=orchestration.JOB, run_id="d9", tags={TAG: run_id}
    )
    event = dg.DagsterEvent(
        event_type_value="PIPELINE_FAILURE", job_name=orchestration.JOB
    )
    context = dg.build_run_status_sensor_context(
        "psych_defgen_run_failure_sensor", event, instance, dagster_run
    ).for_run_failure()
    orchestration.run_failure_sensor(context)
    run = runs.get(run_id)
    assert run["status"] == "failed"
    assert run["error"] == (
        "Dagster run d9 failed before the run finished. "
        "Check its logs, then start a new run."
    )


def test_a_run_from_the_api_reaches_succeeded_or_failed_through_the_job(
    runs, wired, instance, monkeypatch
):
    from psych_defgen.adapters.primary.runs_api import router

    monkeypatch.setenv("ABI_API_KEY", "token")
    app = FastAPI()
    app.include_router(router(runs))
    client = TestClient(app, headers={"Authorization": "Bearer token"})
    api = "/psych_defgen/api/runs"

    good = client.post(api, json={"term": "loneliness"}).json()["run_id"]
    launch(instance, tick(instance)[0])
    succeeded = client.get(f"{api}/{good}").json()
    assert succeeded["status"] == "succeeded"
    assert succeeded["result"]["definition"]
    assert succeeded["finished_at"].endswith("Z")

    wired["definer"] = FakeDefiner(
        error=DefinitionStepError(
            "synthesis",
            "OpenRouter refused the request (HTTP 401); check OPENROUTER_API_KEY.",
        )
    )
    bad = client.post(api, json={"term": "solitude"}).json()["run_id"]
    launch(instance, tick(instance)[0])
    failed = client.get(f"{api}/{bad}").json()
    assert failed["status"] == "failed"
    assert failed["error"].startswith("Step 'synthesis' failed: OpenRouter refused")
    assert failed["result"] is None

    listed = client.get(api).text
    for value in SECRETS.values():
        assert value not in listed + str(succeeded) + str(failed)


def test_the_module_registers_the_orchestration():
    from naas_abi_core.module.ModuleOrchestrationLoader import ModuleOrchestrationLoader

    from psych_defgen import ABIModule

    (found,) = ModuleOrchestrationLoader.load_orchestrations(ABIModule)
    definitions = found.New().definitions
    assert definitions.get_job_def("psych_defgen_run")
    assert definitions.get_sensor_def("psych_defgen_run_request_sensor")
    assert definitions.get_sensor_def("psych_defgen_run_failure_sensor")
    sensor = definitions.get_sensor_def("psych_defgen_run_request_sensor")
    assert sensor.minimum_interval_seconds == 30
