"""HTTP endpoints for the pipeline app.

A thin translation layer: every decision lives in
:class:`~phases_v2.app.service.PipelineAppService`, so these handlers only map
requests to calls and errors to status codes.

Nothing here contacts the orchestrator. Submitting records a row; a Dagster
sensor picks it up.

Every endpoint knows who is asking (:func:`caller`): the ABI API key, a Nexus
user, or nobody known. The services decide what that caller may do; a refusal
(:class:`~phases_v2.ownership.NotAllowed`) is a 403 with its message.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import Response
from naas_abi_core.apps.api.abi_api_key_auth import (
    extract_abi_api_token,
    is_abi_api_token_valid,
    require_abi_api_token,
)
from starlette.concurrency import run_in_threadpool

from phases_v2.app.adapters.primary.SearchAPI import csv_document
from phases_v2.app.contracts.pipeline_validation import validate_message
from phases_v2.app.pipeline_management import MAX_UPLOAD_BYTES, ManagementUnavailable
from phases_v2.app.service import PipelineAppService
from phases_v2.ownership import NOBODY, SYSTEM, Actor, NotAllowed
from phases_v2.requests.interfaces import RequestNotFound

PREFIX = "/phases_v2/api"

#: Who a Nexus session token belongs to; ``None`` when it cannot be told.
Identify = Callable[[str], Awaitable[Actor | None]]

#: How long who a session token belongs to is kept, and for how many tokens.
CALLER_SECONDS = 60.0
CALLER_TOKENS = 1024

#: Lists show the caller's own items (``mine``) or everyone's (``all``).
OWNER = Query(default="all", pattern="^(mine|all)$")


def caller_dependency(service: PipelineAppService, identify: Identify):
    """A dependency giving each endpoint the caller, with no new auth scheme.

    The ABI API key acts as no user (:data:`SYSTEM`). Any other credential is
    asked of Nexus; one it cannot place is :data:`NOBODY`, which may read but
    change nothing. Whether a write is allowed in at all is still
    ``require_abi_api_token``'s decision.

    Who a token belongs to is kept for :data:`CALLER_SECONDS`, so the app's
    polling does not ask Nexus every time; only answers that name a user are
    kept, under a digest of the token. A token revoked meanwhile still names
    its user for at most that long.
    """
    known: dict[str, tuple[float, Actor]] = {}

    async def caller(request: Request) -> Actor:
        token = extract_abi_api_token(request)
        if is_abi_api_token_valid(token):
            return SYSTEM
        if not token:
            return NOBODY
        key = hashlib.sha256(token.encode()).hexdigest()
        now = time.monotonic()
        cached = known.get(key)
        if cached is not None and now - cached[0] < CALLER_SECONDS:
            return cached[1]
        actor = await identify(token)
        if actor is None:
            known.pop(key, None)
            return NOBODY
        if len(known) >= CALLER_TOKENS:
            for stale in [
                k for k, (at, _) in known.items() if now - at >= CALLER_SECONDS
            ]:
                del known[stale]
            if len(known) >= CALLER_TOKENS:
                known.clear()
        known[key] = (now, actor)
        await run_in_threadpool(service.users.remember, actor)
        return actor

    return caller


def build_router(
    service: PipelineAppService, identify: Identify | None = None
) -> APIRouter:
    router = APIRouter(prefix=PREFIX, tags=["phases_v2"])
    if identify is None:
        from phases_v2.app.adapters.secondary.NexusUsers import nexus_user

        identify = nexus_user
    caller = caller_dependency(service, identify)

    @router.get("/prompts")
    def prompts(owner: str = OWNER, actor: Actor = Depends(caller)):
        """Every prompt version with its owner; ``owner=mine`` keeps the caller's."""
        return {"prompts": service.prompts(actor, owner)}

    @router.get("/models")
    def models():
        return {"models": service.models()}

    @router.get("/chunkers")
    def chunkers():
        return {"chunkers": service.chunkers()}

    @router.get("/locations")
    def locations():
        return {"locations": service.locations()}

    @router.get("/documents")
    def documents(
        location: list[str] = Query(default=[]),
        limit: int = Query(default=200, ge=1, le=1000),
    ):
        """Preview what a run over these locations would ingest."""
        try:
            for prefix in location:
                service.management.location(prefix)
            return service.documents(location, limit=limit)
        except ValueError as invalid:
            raise HTTPException(status_code=422, detail=str(invalid)) from invalid

    @router.get("/chunker-warning")
    def chunker_warning(chunker_id: str = ""):
        return {"warning": service.chunker_warning(chunker_id)}

    @router.post(
        "/requests", status_code=201, dependencies=[Depends(require_abi_api_token)]
    )
    def submit(body: dict, actor: Actor = Depends(caller)):
        # Papers come from storage folders or from a collection. A missing input
        # is a 422 naming the field, rather than a 500.
        request = management_call(service.submit_inputs, body, actor)
        return {"request_id": request.request_id, "status": request.status}

    @router.get("/requests")
    def recent(limit: int = 25, owner: str = OWNER, actor: Actor = Depends(caller)):
        """The latest requests; ``owner=mine`` keeps those the caller requested."""
        return {"requests": service.recent_requests(limit, actor, owner)}

    @router.get("/requests/{request_id}")
    def one(request_id: str, actor: Actor = Depends(caller)):
        try:
            return service.request(request_id, actor)
        except RequestNotFound as missing:
            raise HTTPException(status_code=404, detail=str(missing)) from missing

    def management_call(operation, *args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except ValueError as invalid:
            raise HTTPException(status_code=422, detail=str(invalid)) from invalid
        except KeyError as missing:
            # str() of a KeyError quotes its message; show the message itself.
            detail = missing.args[0] if missing.args else "Not found."
            raise HTTPException(status_code=404, detail=str(detail)) from missing
        except NotAllowed as refused:
            raise HTTPException(status_code=403, detail=str(refused)) from refused
        except ManagementUnavailable as unavailable:
            raise HTTPException(
                status_code=503, detail=str(unavailable)
            ) from unavailable

        except Exception as failure:
            logging.getLogger(__name__).exception(
                "Pipeline management operation failed"
            )
            raise HTTPException(
                status_code=503, detail="Storage operation failed. Please retry."
            ) from failure

    @router.get("/sources/pubmed")
    def pubmed_queries():
        return management_call(service.pubmed_queries)

    @router.get("/sources/pubmed/{query_id}")
    def pubmed_artifacts(query_id: str):
        return {"artifacts": management_call(service.pubmed_artifacts, query_id)}

    @router.post(
        "/sources/pubmed/requests",
        status_code=201,
        dependencies=[Depends(require_abi_api_token)],
    )
    def submit_pubmed(body: dict, actor: Actor = Depends(caller)):
        request = management_call(service.submit_pubmed, body, actor)
        return {"request_id": request.request_id, "status": request.status}

    @router.get("/collections")
    def collections(
        counts: bool = True, owner: str = OWNER, actor: Actor = Depends(caller)
    ):
        """Every collection with its owner, and how many papers each holds unless
        ``counts=false``; ``owner=mine`` keeps the caller's own."""
        return {
            "collections": management_call(
                service.resources.summaries, counts, actor, owner
            )
        }

    @router.get("/collections/{collection_id}")
    def collection(collection_id: str, actor: Actor = Depends(caller)):
        """Members and the papers they resolve to now, each with its status."""
        return management_call(service.resources.detail, collection_id, actor=actor)

    @router.post(
        "/collections", status_code=201, dependencies=[Depends(require_abi_api_token)]
    )
    def create_collection(body: dict, actor: Actor = Depends(caller)):
        return management_call(service.resources.create_collection, body, actor)

    @router.put(
        "/collections/{collection_id}", dependencies=[Depends(require_abi_api_token)]
    )
    def update_collection(
        collection_id: str, body: dict, actor: Actor = Depends(caller)
    ):
        return management_call(
            service.resources.update_collection, collection_id, body, actor
        )

    @router.post(
        "/collections/{collection_id}/members",
        dependencies=[Depends(require_abi_api_token)],
    )
    def add_members(collection_id: str, body: dict, actor: Actor = Depends(caller)):
        return management_call(
            service.resources.add_members, collection_id, body, actor
        )

    @router.delete(
        "/collections/{collection_id}/members/{member_id}",
        dependencies=[Depends(require_abi_api_token)],
    )
    def remove_member(
        collection_id: str, member_id: str, actor: Actor = Depends(caller)
    ):
        return management_call(
            service.resources.remove_member, collection_id, member_id, actor
        )

    @router.post(
        "/collections/{collection_id}/documents",
        status_code=201,
        dependencies=[Depends(require_abi_api_token)],
    )
    async def upload_to_collection(
        request: Request,
        collection_id: str,
        filename: str,
        actor: Actor = Depends(caller),
    ):
        content = await _read_pdf(request)
        return await run_in_threadpool(
            management_call,
            service.upload_to_collection,
            collection_id,
            filename,
            content,
            actor,
        )

    @router.delete(
        "/collections/{collection_id}", dependencies=[Depends(require_abi_api_token)]
    )
    def archive_collection(collection_id: str, actor: Actor = Depends(caller)):
        management_call(service.resources.archive_collection, collection_id, actor)
        return {"archived": True}

    @router.post(
        "/prompts", status_code=201, dependencies=[Depends(require_abi_api_token)]
    )
    def save_prompt(body: dict, actor: Actor = Depends(caller)):
        """Save a version. Under an existing name, only its owner may."""
        return management_call(service.management.save_prompt, body, actor)

    @router.get("/prompts/versions")
    def prompt_versions(name: str = Query(min_length=1, max_length=100)):
        """Every version of one prompt name, with the runs that used each."""
        return management_call(service.versions.versions, name)

    @router.post("/prompts/try", dependencies=[Depends(require_abi_api_token)])
    def try_prompt(body: dict):
        """Run a draft or version on a few chunks and answer at once. Writes nothing."""
        return management_call(service.try_prompt, body)

    @router.get("/papers")
    def papers(
        q: str = Query(default="", max_length=300),
        chunker_id: str = Query(default="", max_length=200),
        collection: str = Query(default="", max_length=200),
        request_id: str = Query(default="", max_length=200),
        prompt: list[str] = Query(default=[]),
        prompt_id: list[str] = Query(default=[]),
        model: list[str] = Query(default=[]),
        has_results: bool | None = None,
        paper_id: list[str] = Query(default=[]),
        sort: str = "title",
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ):
        """Ingested papers with what was extracted from each, one page at a time.

        One listing for every view: Collections adds single papers from it,
        Prompts tries a prompt on one of them, Results opens them. See
        ``PaperListQuery`` for the filters.
        """
        query = {
            "q": q,
            "chunker_id": chunker_id,
            "collection": collection,
            "request_id": request_id,
            "prompts": prompt,
            "prompt_ids": prompt_id,
            "models": model,
            "paper_ids": paper_id,
            "sort": sort,
            "limit": limit,
            "offset": offset,
        }
        if has_results is not None:
            query["has_results"] = has_results
        return management_call(service.paper_list, query)

    @router.get("/papers/{paper_id}")
    def paper(paper_id: str, actor: Actor = Depends(caller)):
        """One paper: its records, what ran on it, and every result grouped by
        prompt version and model."""
        return management_call(service.paper, paper_id, actor)

    @router.get("/papers/{paper_id}/export")
    def export_paper(paper_id: str, prompt_id: str = "", model_id: str | None = None):
        """The paper's results as CSV, with the search export's columns.

        Without ``model_id`` every model's results; ``model_id=`` (empty) keeps
        the results whose model is unknown.
        """
        query = {"paper_id": paper_id, "prompt_id": prompt_id}
        if model_id is not None:
            query["model_id"] = model_id
        management_call(validate_message, "PaperExportQuery", query)
        hits = management_call(
            lambda: list(service.paper_results.hits(paper_id, prompt_id, model_id))
        )
        return Response(
            b"".join(csv_document(hits, "", "paper")),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": "attachment; "
                f'filename="healthyphases-paper-{re.sub(r"[^0-9A-Za-z_-]", "", paper_id)[:16]}.csv"',
                "Cache-Control": "private, no-store",
            },
        )

    @router.get("/papers/{paper_id}/chunks")
    def paper_chunks(paper_id: str, chunker_id: str = Query(min_length=1)):
        return management_call(service.papers.chunks, paper_id, chunker_id)

    @router.post(
        "/locations", status_code=201, dependencies=[Depends(require_abi_api_token)]
    )
    def create_location(body: dict):
        return management_call(service.management.create_location, body)

    @router.post(
        "/documents", status_code=201, dependencies=[Depends(require_abi_api_token)]
    )
    async def upload_document(
        request: Request, location: str, filename: str, actor: Actor = Depends(caller)
    ):
        """Upload into a storage folder; a collection's upload folder only for
        who may change that collection."""
        content = await _read_pdf(request)
        return await run_in_threadpool(
            management_call,
            service.upload_document,
            location,
            filename,
            content,
            actor,
        )

    @router.get("/pipelines")
    def pipelines(owner: str = OWNER, actor: Actor = Depends(caller)):
        """Saved configurations with their owner; ``owner=mine`` keeps the caller's."""
        return {
            "pipelines": management_call(service.management.pipelines, actor, owner)
        }

    @router.post(
        "/pipelines", status_code=201, dependencies=[Depends(require_abi_api_token)]
    )
    def save_pipeline(body: dict, actor: Actor = Depends(caller)):
        return management_call(service.management.save_pipeline, body, actor)

    @router.post(
        "/pipelines/{pipeline_id}/requests",
        status_code=201,
        dependencies=[Depends(require_abi_api_token)],
    )
    def run_pipeline(pipeline_id: str, actor: Actor = Depends(caller)):
        """Anyone may run any saved configuration; the run is the caller's."""
        pipeline = management_call(service.management.pipeline, pipeline_id)
        return submit(pipeline["inputs"], actor)

    return router


async def _read_pdf(request: Request) -> bytes:
    # Raw PDF streaming puts a bound on memory before parsing or storing it.
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="PDFs must not exceed 25 MiB.")
        content.extend(chunk)
    return bytes(content)


def register(
    app: FastAPI, service: PipelineAppService, identify: Identify | None = None
) -> None:
    app.include_router(build_router(service, identify))
