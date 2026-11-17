"""Dagster job and sensors that turn pending definition runs into definitions.

- ``psych_defgen_run_request_sensor`` (every 30 s) launches ``psych_defgen_run``
  for the oldest pending run, with the run ID as the run key. One run goes at
  a time: every run shares the deployment's NCBI rate limit (3 requests/s
  without a key), and each Dagster run paces itself on its own.
- ``psych_defgen_run`` marks the run running, calls ``define()`` with each
  stage written to the run, and stores the result or the error.
- ``psych_defgen_run_failure_sensor`` marks a run failed when its Dagster run
  fails. The request sensor also fails a run whose Dagster run ended without
  finishing it (canceled, or lost in a restart), so that one stuck run does
  not hold the queue.
"""

from typing import Any

import dagster as dg
from naas_abi_core.orchestrations.DagsterOrchestration import DagsterOrchestration

from psych_defgen.application.runs import FAILED, PENDING, RUNNING, DefinitionRuns

MODULE = "psych_defgen"
TAG = "psych_defgen/run_id"
JOB = "psych_defgen_run"
OP = "define_construct"

FINISHED = {
    dg.DagsterRunStatus.SUCCESS,
    dg.DagsterRunStatus.FAILURE,
    dg.DagsterRunStatus.CANCELED,
}


def _module():
    from naas_abi_core.apps.dagster.dagster import engine

    return engine.modules[MODULE]


def dataset_available() -> bool:
    return _module().engine.services.dataset_available()


def runs() -> DefinitionRuns:
    from psych_defgen.factory import definition_runs

    module = _module()
    return definition_runs(module.engine.services.dataset, module.configuration)


def definer():
    """The definition service; raises ``NotConfiguredError`` naming missing keys."""
    from psych_defgen.factory import definition_service

    return definition_service(_module().configuration)


class RunConfig(dg.Config):
    run_id: str


@dg.op(name=OP)
def define_construct(context: dg.OpExecutionContext, config: RunConfig) -> str:
    service = runs()
    status = service.execute(config.run_id, definer, job_run_id=context.run_id)
    if status == FAILED:
        error = service.get(config.run_id)["error"]
        context.log.warning(f"Definition run {config.run_id} failed: {error}")
        raise dg.Failure(description=error)
    context.log.info(f"Definition run {config.run_id}: {status}")
    return status


@dg.job(name=JOB, tags={"psych_defgen": "definition"})
def run_job():
    define_construct()


def build_run_requests(pending: list[dict[str, Any]]) -> list[dg.RunRequest]:
    return [
        dg.RunRequest(
            run_key=row["run_id"],
            tags={TAG: row["run_id"]},
            run_config={"ops": {OP: {"config": {"run_id": row["run_id"]}}}},
        )
        for row in pending
    ]


def stopped_message(dagster_run) -> str:
    if dagster_run is None:
        return (
            "The run was interrupted before it finished (its Dagster run no "
            "longer exists). Start a new run."
        )
    status = dagster_run.status
    if status == dg.DagsterRunStatus.CANCELED:
        return f"Dagster run {dagster_run.run_id} was canceled. Start a new run."
    return (
        f"Dagster run {dagster_run.run_id} ended ({status.value.lower()}) before "
        "the run finished. Check its logs, then start a new run."
    )


def release_stopped(
    service: DefinitionRuns, active: list[dict[str, Any]], instance
) -> set[str]:
    """Fail the active runs whose Dagster runs have all ended; their IDs.

    A pending run with no Dagster run yet is waiting for this sensor, and is
    left alone.
    """
    stopped = set()
    for row in active:
        dagster_runs = instance.get_runs(
            filters=dg.RunsFilter(tags={TAG: row["run_id"]})
        )
        if any(run.status not in FINISHED for run in dagster_runs):
            continue
        if row["status"] == PENDING and not dagster_runs:
            continue
        latest = dagster_runs[0] if dagster_runs else None
        service.fail(row["run_id"], stopped_message(latest))
        stopped.add(row["run_id"])
    return stopped


@dg.sensor(
    name="psych_defgen_run_request_sensor",
    job=run_job,
    minimum_interval_seconds=30,
    default_status=dg.DefaultSensorStatus.RUNNING,
)
def run_request_sensor(context: dg.SensorEvaluationContext):
    if not dataset_available():
        return dg.SkipReason("DatasetService unavailable")
    service = runs()
    active = service.active()
    stopped = release_stopped(service, active, context.instance)
    active = [row for row in active if row["run_id"] not in stopped]
    if any(row["status"] == RUNNING for row in active):
        return dg.SkipReason("A definition run is in progress")
    pending = [row for row in active if row["status"] == PENDING][:1]
    if not pending:
        return dg.SkipReason("No pending definition runs")
    return build_run_requests(pending)


@dg.run_failure_sensor(
    name="psych_defgen_run_failure_sensor",
    monitored_jobs=[run_job],
    default_status=dg.DefaultSensorStatus.RUNNING,
)
def run_failure_sensor(context: dg.RunFailureSensorContext):
    run_id = context.dagster_run.tags.get(TAG)
    if not run_id or not dataset_available():
        return
    # A failure the job already stored keeps its own, more precise message.
    runs().fail(
        run_id,
        f"Dagster run {context.dagster_run.run_id} failed before the run "
        "finished. Check its logs, then start a new run.",
    )


class PsychDefgenOrchestration(DagsterOrchestration):
    @classmethod
    def New(cls):
        return cls(
            definitions=dg.Definitions(
                jobs=[run_job],
                sensors=[run_request_sensor, run_failure_sensor],
            )
        )
