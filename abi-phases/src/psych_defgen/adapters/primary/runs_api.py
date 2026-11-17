"""HTTP API for definition runs, under ``/psych_defgen/api``.

Requests only queue runs and read them: the definitions themselves are made
by the Dagster job ``psych_defgen_run``. Errors are ``{"detail": "..."}``.
"""

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from naas_abi_core.apps.api.abi_api_key_auth import require_abi_api_token

from psych_defgen.application.runs import PAGE_SIZE_DEFAULT, DefinitionRuns
from psych_defgen.domain.errors import (
    InvalidRunError,
    NotConfiguredError,
    RunNotFoundError,
)

PREFIX = "/psych_defgen/api"


def router(runs: DefinitionRuns) -> APIRouter:
    api = APIRouter(
        prefix=PREFIX,
        tags=["psych_defgen"],
        dependencies=[Depends(require_abi_api_token)],
    )

    def call(action, *args):
        try:
            return action(*args)
        except InvalidRunError as exc:
            raise HTTPException(422, str(exc)) from exc
        except NotConfiguredError as exc:
            raise HTTPException(409, str(exc)) from exc
        except RunNotFoundError as exc:
            raise HTTPException(404, "Definition run not found.") from exc

    @api.get("/settings")
    def settings() -> dict[str, Any]:
        return runs.settings()

    # The body and the page numbers are parsed here, not by FastAPI, so that
    # a malformed one gets the same {"detail": "..."} as every other error.
    @api.post("/runs", status_code=201)
    async def create_run(request: Request) -> dict[str, Any]:
        raw = await request.body()
        try:
            body = json.loads(raw) if raw.strip() else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise HTTPException(
                422, 'The request body must be JSON, such as {"term": "loneliness"}.'
            ) from None
        # Creating a run writes to the dataset: keep it off the event loop.
        return await run_in_threadpool(call, runs.create, body)

    @api.get("/runs")
    def list_runs(
        page: str = "1", page_size: str = str(PAGE_SIZE_DEFAULT)
    ) -> dict[str, Any]:
        return call(runs.list, page, page_size)

    @api.get("/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        return call(runs.get, run_id)

    return api
