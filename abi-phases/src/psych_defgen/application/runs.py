"""Definition runs: one request for a definition, from creation to its result.

A run goes ``pending`` → ``running`` → ``succeeded`` or ``failed``. While it
runs, ``stage`` is the pipeline stage it is in (``STAGES``). The job writes a
run a handful of times (start, each stage, finish), since DuckLake commits
serialise (#635).

Messages stored in ``error`` or raised to callers name settings by their
environment variable and never hold a secret value.
"""

# ``list`` is a method name below; postponed annotations keep the builtin.
from __future__ import annotations

import json
import math
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from naas_abi_core import logger

from psych_defgen.application.definition_service import STAGES
from psych_defgen.domain.errors import (
    DefinitionStepError,
    InvalidRunError,
    NotConfiguredError,
)

PENDING, RUNNING, SUCCEEDED, FAILED = "pending", "running", "succeeded", "failed"
ACTIVE = (PENDING, RUNNING)

TERM_MAX_LENGTH = 200
PAGE_SIZE_DEFAULT = 25
PAGE_SIZE_MAX = 100

#: Snapshot-conflict retries for the writes that start and finish a run:
#: conflicts are catalog-wide (#635), so a busy catalog must neither fail a
#: run before it does any work nor cost the result after minutes of NCBI and
#: LLM work (about 50 s of waiting; stage writes keep the store's short
#: default).
FINISH_ATTEMPTS = 60

_WHOLE_NUMBER = re.compile(r"-?[0-9]+")


@dataclass(frozen=True)
class Limit:
    default: int
    min: int
    max: int

    def as_dict(self) -> dict[str, int]:
        return {"default": self.default, "min": self.min, "max": self.max}


MAX_RESULTS = Limit(default=100, min=1, max=200)
TOP_K = Limit(default=10, min=1, max=20)


@dataclass(frozen=True)
class RunSettings:
    """What a request may ask for, and which required keys are unset."""

    models: tuple[str, ...]
    default_model: str
    embedding_model: str
    missing: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "models": list(self.models),
            "default_model": self.default_model,
            "embedding_model": self.embedding_model,
            "max_results": MAX_RESULTS.as_dict(),
            "top_k": TOP_K.as_dict(),
            "missing": list(self.missing),
        }


class RunStore(Protocol):
    def insert(self, row: dict[str, Any]) -> None: ...

    def get(self, run_id: str) -> dict[str, Any]:
        """The run with ``result`` as JSON text; raises ``RunNotFoundError``."""
        ...

    def page(self, *, offset: int, limit: int) -> tuple[int, list[dict[str, Any]]]:
        """Runs newest first, without ``result``, and the total count."""
        ...

    def with_status(self, *statuses: str) -> list[dict[str, Any]]: ...

    def update(
        self,
        run_id: str,
        change: Callable[[dict[str, Any]], dict[str, Any] | None],
        *,
        attempts: int = ...,
    ) -> dict[str, Any]: ...


class Definer(Protocol):
    def define(
        self,
        term: str,
        *,
        max_results: int,
        top_k: int,
        model: str,
        on_stage: Callable[[str], None] | None = None,
    ) -> dict[str, Any]: ...


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_run_id() -> str:
    """32 hex digits that sort by creation time, to the millisecond."""
    return f"{time.time_ns() // 1_000_000:012x}{secrets.token_hex(10)}"


def _integer(
    value: Any, name: str, low: int, high: int, *, from_text: bool = False
) -> int:
    """``value`` as an int in [low, high]; text is parsed only for query
    parameters (``from_text``), JSON numbers must be integers."""
    message = f"{name} must be a whole number from {low} to {high}."
    if isinstance(value, bool):
        raise InvalidRunError(message)
    if from_text and isinstance(value, str):
        # Plain ASCII digits only: int() would also take "1_0" or "١".
        if not _WHOLE_NUMBER.fullmatch(value.strip()):
            raise InvalidRunError(message)
        value = int(value.strip())
    if not isinstance(value, int) or not low <= value <= high:
        raise InvalidRunError(message)
    return value


class DefinitionRuns:
    def __init__(
        self,
        store: RunStore,
        settings: RunSettings,
        *,
        clock: Callable[[], str] = utc_now,
        new_id: Callable[[], str] = new_run_id,
    ):
        self.store = store
        self._settings = settings
        self.clock = clock
        self.new_id = new_id

    def settings(self) -> dict[str, Any]:
        return self._settings.as_dict()

    def create(self, payload: Any) -> dict[str, Any]:
        """Queue a run. Raises ``InvalidRunError`` or ``NotConfiguredError``."""
        if not isinstance(payload, dict):
            raise InvalidRunError('Send a JSON object such as {"term": "loneliness"}.')
        term = payload.get("term")
        if not isinstance(term, str) or not 1 <= len(term.strip()) <= TERM_MAX_LENGTH:
            raise InvalidRunError(
                f"term must be 1 to {TERM_MAX_LENGTH} characters, "
                "not counting surrounding spaces."
            )
        max_results = payload.get("max_results")
        top_k = payload.get("top_k")
        model = payload.get("model")
        if model is None:
            model = self._settings.default_model
        elif model not in self._settings.models:
            raise InvalidRunError(
                "model must be one of: " + ", ".join(self._settings.models) + "."
            )
        row = {
            "run_id": self.new_id(),
            "term": term.strip(),
            "max_results": MAX_RESULTS.default
            if max_results is None
            else _integer(max_results, "max_results", MAX_RESULTS.min, MAX_RESULTS.max),
            "top_k": TOP_K.default
            if top_k is None
            else _integer(top_k, "top_k", TOP_K.min, TOP_K.max),
            "model": model,
            "embedding_model": self._settings.embedding_model,
            "status": PENDING,
            "stage": None,
            "error": None,
            "created_at": self.clock(),
            "started_at": None,
            "finished_at": None,
            "result": None,
        }
        if self._settings.missing:
            raise NotConfiguredError(list(self._settings.missing))
        self.store.insert(row)
        return row

    def list(self, page: Any = 1, page_size: Any = PAGE_SIZE_DEFAULT) -> dict[str, Any]:
        """One page of runs, newest first, without results."""
        page = _integer(page, "page", 1, 1_000_000, from_text=True)
        page_size = _integer(page_size, "page_size", 1, PAGE_SIZE_MAX, from_text=True)
        total, rows = self.store.page(offset=(page - 1) * page_size, limit=page_size)
        return {
            "runs": rows,
            "total": total,
            "page": page,
            "page_count": math.ceil(total / page_size),
        }

    def get(self, run_id: str) -> dict[str, Any]:
        """The run with its result. Raises ``RunNotFoundError``."""
        row = dict(self.store.get(run_id))
        if row.get("result") is not None:
            row["result"] = json.loads(row["result"])
        return row

    def pending(self) -> list[dict[str, Any]]:
        """Pending runs, oldest first, without results."""
        return self.store.with_status(PENDING)

    def active(self) -> list[dict[str, Any]]:
        """Pending and running runs, oldest first, without results."""
        return self.store.with_status(*ACTIVE)

    def start(self, run_id: str) -> dict[str, Any] | None:
        """Mark a pending run running; ``None`` if it was not pending."""

        started: list[bool] = []

        def change(row):
            # Called again on each retry: only the last call counts.
            started.clear()
            if row["status"] != PENDING:
                return None
            started.append(True)
            return {**row, "status": RUNNING, "started_at": self.clock()}

        row = self.store.update(run_id, change, attempts=FINISH_ATTEMPTS)
        return row if started else None

    def set_stage(self, run_id: str, stage: str) -> None:
        if stage not in STAGES:
            raise ValueError(f"Unknown stage {stage!r}; expected one of {STAGES}")
        self.store.update(
            run_id,
            lambda row: (
                {**row, "stage": stage}
                if row["status"] == RUNNING and row["stage"] != stage
                else None
            ),
        )

    def succeed(self, run_id: str, result: dict[str, Any]) -> dict[str, Any]:
        """Store the result of a running run; returns the row as stored, which
        is left as it was if the run is no longer running."""
        text = json.dumps(result, ensure_ascii=False, allow_nan=False)
        return self.store.update(
            run_id,
            lambda row: (
                {
                    **row,
                    "status": SUCCEEDED,
                    "error": None,
                    "finished_at": self.clock(),
                    "result": text,
                }
                if row["status"] == RUNNING
                else None
            ),
            attempts=FINISH_ATTEMPTS,
        )

    def fail(self, run_id: str, message: str) -> dict[str, Any]:
        """Mark an unfinished run failed; a finished run is left as it is."""
        return self.store.update(
            run_id,
            lambda row: (
                {
                    **row,
                    "status": FAILED,
                    "error": message,
                    "finished_at": self.clock(),
                }
                if row["status"] in ACTIVE
                else None
            ),
            attempts=FINISH_ATTEMPTS,
        )

    def execute(
        self,
        run_id: str,
        definer: Callable[[], Definer],
        *,
        job_run_id: str,
    ) -> str:
        """Run a pending definition request to its end; returns its status.

        ``definer`` builds the definition service (it raises
        ``NotConfiguredError`` when keys are missing). A failure the pipeline
        explains is stored as the run's error. Anything else is a bug: the
        run is marked failed, pointing at the job run's logs, and the error
        is raised again so the job fails too.
        """
        run = self.start(run_id)
        if run is None:
            return self.store.get(run_id)["status"]
        reached: list[str] = []

        def on_stage(stage: str) -> None:
            # Progress only: a store that cannot take it must not stop the run.
            reached.append(stage)
            try:
                self.set_stage(run_id, stage)
            except Exception:  # noqa: BLE001
                logger.opt(exception=True).warning(
                    f"Definition run {run_id}: stage '{stage}' not stored; "
                    "the run goes on."
                )

        try:
            result = definer().define(
                run["term"],
                max_results=run["max_results"],
                top_k=run["top_k"],
                model=run["model"],
                on_stage=on_stage,
            )
        except (DefinitionStepError, NotConfiguredError) as exc:
            self.fail(run_id, str(exc))
            return FAILED
        except Exception as exc:
            where = f" during stage '{reached[-1]}'" if reached else ""
            self.fail(
                run_id,
                f"The run stopped on an unexpected {type(exc).__name__}{where}. "
                f"See Dagster run {job_run_id} for details, then start a new run.",
            )
            raise
        try:
            stored = self.succeed(run_id, result)
        except Exception as exc:
            # Fail the run with the reason; the job fails too.
            self.fail(
                run_id,
                f"The result could not be stored ({type(exc).__name__}). "
                f"See Dagster run {job_run_id} for details, then start a new run.",
            )
            raise
        if stored["status"] != SUCCEEDED:
            logger.warning(
                f"Definition run {run_id} was {stored['status']} before its "
                "result could be stored; the result was discarded."
            )
        return stored["status"]
