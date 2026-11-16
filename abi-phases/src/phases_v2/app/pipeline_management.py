"""Pipeline authoring use cases over the existing row and object stores."""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from phases_v2.app.contracts.pipeline_validation import validate_message
from phases_v2.chunking.chunkers import DECLARED_CHUNKERS
from phases_v2.identity import paper_id
from phases_v2.models.catalog import DECLARED_MODELS
from phases_v2.ownership import (
    EVERYONE,
    NOBODY,
    Actor,
    NotAllowed,
    creation_refusal,
    keep,
    owner_view,
    prompt_refusal,
)
from phases_v2.papers.interfaces import ObjectSource
from phases_v2.ports import RowStore
from phases_v2.prompts.domain import PromptTemplate, register_prompts
from phases_v2.prompts.templates import declared_prompts
from phases_v2.sql import literal

MAX_UPLOAD_BYTES = 25 * 1024 * 1024


class ManagementUnavailable(RuntimeError):
    pass


def prompt_catalog(rows: RowStore | None = None) -> list[dict]:
    """Every prompt version, each with its name's owner (``None``: shared).

    A name belongs to whoever saved its first version. Names are compared
    without case, so two users never hold look-alike names; a declared name is
    always shared, whoever saved a version under it before owners existed.
    """
    declared = {p.prompt_id for p in declared_prompts()}
    catalog = {
        p.prompt_id: {
            "prompt_id": p.prompt_id,
            "name": p.name,
            "template": p.template,
            "output_key": p.output_key,
            "owner_id": None,
            "registered_at": None,
        }
        for p in declared_prompts()
    }
    if rows is not None:
        for row in rows.query(
            "SELECT prompt_id, name, template, output_key, owner_id, registered_at "
            "FROM prompts"
        ):
            catalog[row["prompt_id"]] = dict(row)
    owners = _name_owners(catalog.values(), declared)
    return sorted(
        (
            {
                "prompt_id": p["prompt_id"],
                "name": p["name"],
                "template": p["template"],
                "output_key": p["output_key"],
                "owner_id": owners[name_key(p["name"])],
                "declared": p["prompt_id"] in declared,
            }
            for p in catalog.values()
        ),
        key=lambda p: (p["name"], p["prompt_id"]),
    )


def name_key(name: str) -> str:
    """What makes two prompt names the same name."""
    return name.strip().casefold()


def _name_owners(versions, declared: set[str]) -> dict[str, str | None]:
    first: dict[str, tuple] = {}
    shared: set[str] = set()
    for version in versions:
        key = name_key(version["name"])
        if version["prompt_id"] in declared:
            shared.add(key)
        registered = version.get("registered_at")
        order = (
            registered is None,
            registered.isoformat() if hasattr(registered, "isoformat") else "",
            version["prompt_id"],
        )
        if key not in first or order < first[key][0]:
            first[key] = (order, version.get("owner_id"))
    return {key: None if key in shared else owner for key, (_, owner) in first.items()}


class PipelineManagement:
    def __init__(
        self,
        rows: RowStore | None,
        storage: ObjectSource | None,
        papers_root: str,
        is_model_available: Callable[[str], bool] | None = None,
        paper_collections=None,
        users=None,
    ):
        self.rows = rows
        #: Names owners the way Nexus does; see ``AppUsers``.
        self.users = users
        self.storage = storage
        self.root = papers_root
        self.is_model_available = is_model_available
        #: Looks a collection up by id; raises KeyError when there is none.
        self.paper_collections = paper_collections

    def _rows(self) -> RowStore:
        if self.rows is None:
            raise ManagementUnavailable("The dataset service is unavailable.")
        return self.rows

    def location(self, value: str) -> str:
        if (
            not value
            or "\\" in value
            or any(ord(c) < 32 for c in value)
            or any(p in ("", ".", "..") for p in value.split("/"))
            or not (value == self.root or value.startswith(self.root + "/"))
        ):
            raise ValueError("locations must be beneath the module's papers root")
        return value

    def names(self) -> dict[str, str]:
        return self.users.names() if self.users is not None else {}

    def prompts(self, actor: Actor = NOBODY, owner: str = EVERYONE) -> list[dict]:
        """Every prompt version, with its owner and whether ``actor`` may add
        versions under its name; ``owner="mine"`` keeps the caller's own."""
        names = self.names()
        listed = []
        for prompt in prompt_catalog(self.rows):
            view = owner_view(prompt["owner_id"], actor, names)
            refusal = prompt_refusal(
                actor, prompt["name"], prompt["owner_id"], view["owner_name"]
            )
            listed.append(
                {
                    **prompt,
                    **view,
                    "can_change": refusal is None,
                    "read_only_reason": refusal,
                }
            )
        return keep(listed, actor, owner)

    def save_prompt(self, payload: dict, actor: Actor = NOBODY) -> dict:
        """Save a version under the payload's name.

        A new name becomes the caller's. Under an existing name, only its owner
        (or the API key) saves; a version always takes the name's owner, so
        one name's history never splits between people.
        """
        validate_message("SavePrompt", payload)
        prompt = PromptTemplate(**payload)
        catalog = prompt_catalog(self.rows)
        same_name = [p for p in catalog if name_key(p["name"]) == name_key(prompt.name)]
        if same_name:
            owner_id = same_name[0]["owner_id"]
            refusal = prompt_refusal(
                actor,
                same_name[0]["name"],
                owner_id,
                self.names().get(owner_id) if owner_id else None,
            )
        else:
            owner_id = actor.owner_id
            refusal = creation_refusal(actor)
        if refusal:
            raise NotAllowed(refusal)
        existing = next(
            (p for p in catalog if p["prompt_id"] == prompt.prompt_id), None
        )
        if existing:
            if existing["output_key"] != prompt.output_key:
                raise ValueError(
                    "Changing output format requires a new name or template text."
                )
            return existing
        register_prompts(self._rows(), [prompt], owner_id=owner_id)
        return {"prompt_id": prompt.prompt_id, **payload, "owner_id": owner_id}

    def collections(self) -> list[dict]:
        if self.rows is None:
            return []
        return [
            row
            for row in self.rows.query("SELECT prefix, name FROM input_locations")
            if row["prefix"].startswith(self.root + "/")
        ]

    def create_location(self, payload: dict) -> dict:
        validate_message("CreateLocation", payload)
        prefix = self.location(f"{self.root}/{payload['name']}")
        self._rows().write_rows(
            "input_locations",
            [
                {
                    "prefix": prefix,
                    "name": payload["name"],
                    "created_at": datetime.now(UTC),
                }
            ],
        )
        return {"prefix": prefix, "name": payload["name"]}

    def upload(self, location: str, filename: str, content: bytes) -> dict:
        validate_message("UploadDocument", {"location": location, "filename": filename})
        self.location(location)
        if (
            "/" in filename
            or "\\" in filename
            or any(ord(c) < 32 for c in filename)
            or filename.startswith(".")
            or not filename.lower().endswith(".pdf")
        ):
            raise ValueError("Use a PDF filename without directory separators.")
        if not content or len(content) > MAX_UPLOAD_BYTES:
            raise ValueError("PDFs must be between 1 byte and 25 MiB.")
        if not content.startswith(b"%PDF-"):
            raise ValueError("The file does not have a PDF header.")
        if self.storage is None:
            raise ManagementUnavailable("The object storage service is unavailable.")
        # Content-addressed keys make retries idempotent and preserve different
        # documents sharing a filename, including concurrent uploads.
        key = f"{paper_id(content)}/{filename}"
        self.storage.put_object(location, key, content)
        return {
            "location": location,
            "key": f"{location}/{key}",
            "name": filename,
            "size_bytes": len(content),
        }

    def validate_inputs(self, inputs: dict) -> dict:
        """Papers from storage folders or from one collection, and valid choices."""
        validate_message("PipelineInputs", inputs)
        locations = inputs.get("locations") or []
        collection_id = inputs.get("collection_id") or ""
        if locations and collection_id:
            raise ValueError("Choose a collection or storage folders, not both.")
        if not locations and not collection_id:
            raise ValueError(
                "locations: choose at least one storage folder, or a collection"
            )
        for location in locations:
            self.location(location)
        if collection_id:
            if self.paper_collections is None:
                raise ManagementUnavailable("Collections are unavailable.")
            self.paper_collections.collection(collection_id)
        self.validate_choices(inputs)
        return inputs

    def validate_choices(self, inputs: dict) -> None:
        if inputs["chunker_id"] not in {c.chunker_id for c in DECLARED_CHUNKERS}:
            raise ValueError("Unknown chunker_id")
        if inputs["model_id"] not in {m.model_id for m in DECLARED_MODELS}:
            raise ValueError("Unknown model_id")
        if self.is_model_available and not self.is_model_available(inputs["model_id"]):
            raise ValueError("The selected model_id is unavailable on this deployment")
        # The catalog alone: whose a prompt is never decides whether it can run.
        known = {p["prompt_id"] for p in prompt_catalog(self.rows)}
        if set(inputs["prompt_ids"]) - known:
            raise ValueError("Unknown prompt_ids; reload the prompt catalog")

    def pipelines(self, actor: Actor = NOBODY, owner: str = EVERYONE) -> list[dict]:
        """Saved configurations, newest first, each with its owner."""
        names = self.names()
        rows = self._rows().query(
            "SELECT pipeline_id, name, inputs, created_at, owner_id FROM pipelines "
            "ORDER BY created_at DESC"
        )
        return keep(
            [{**row, **owner_view(row["owner_id"], actor, names)} for row in rows],
            actor,
            owner,
        )

    def save_pipeline(self, payload: dict, actor: Actor = NOBODY) -> dict:
        """Save a configuration. It belongs to the caller; nobody changes it."""
        validate_message("SavePipeline", payload)
        if refusal := creation_refusal(actor):
            raise NotAllowed(refusal)
        self.validate_inputs(payload["inputs"])
        row = {
            "pipeline_id": str(uuid.uuid4()),
            **payload,
            "created_at": datetime.now(UTC),
            "owner_id": actor.owner_id,
        }
        self._rows().write_rows("pipelines", [row])
        return row

    def pipeline(self, pipeline_id: str) -> dict:
        rows = self._rows().query(
            "SELECT pipeline_id, name, inputs, created_at, owner_id FROM pipelines "
            f"WHERE pipeline_id = {literal(pipeline_id)}"
        )
        if not rows:
            raise KeyError("Saved pipeline not found")
        return rows[0]
