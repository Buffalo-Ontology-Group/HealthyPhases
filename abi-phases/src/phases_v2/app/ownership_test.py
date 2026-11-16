"""Two users, on real datasets and through the HTTP API.

Ada and Bea sign in to Nexus; their session tokens reach the API, which asks
Nexus (faked here) who each one is. Each sees her own prompts, collections,
configurations and runs under Mine and the other's under everyone's; neither
changes what the other owns, and the refusal holds in the services and as a
403; each can duplicate the other's prompt; shared collections stay editable
by both; prompt names stay unique across them. The ABI API key acts as no user.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from naas_abi_core.apps.api import abi_api_key_auth
from naas_abi_core.services.dataset.adapters.secondary.DatasetSecondaryAdapterDuckLake import (
    DatasetSecondaryAdapterDuckLake,
)
from naas_abi_core.services.dataset.DatasetService import DatasetService

from phases_v2.app.adapters.primary.PipelineAPI import PREFIX, register
from phases_v2.app.app_resources_test import PdfOnly, Storage
from phases_v2.app.service import PipelineAppService
from phases_v2.chunking.chunkers import WINDOW_512_128
from phases_v2.datasets.row_store import DatasetRowStore
from phases_v2.datasets.store import ensure_datasets
from phases_v2.models.catalog import DECLARED_MODELS
from phases_v2.ownership import SYSTEM, Actor, IdentityUnavailable, NotAllowed
from phases_v2.paper_collections.adapters.secondary.StoredMemberSources import (
    StoredMemberSources,
)
from phases_v2.prompts.templates import declared_prompts
from phases_v2.requests.adapters.secondary.DatasetRequestStore import (
    DatasetRequestStore,
)
from phases_v2.sources.adapters.secondary.phases_v2_pubmed_catalog import PubmedCatalog

API_KEY = "test-pipeline-key"
ADA = Actor.user("user-ada", "Ada Example", "ada@example.org")
BEA = Actor.user("user-bea", "Bea Example", "bea@example.org")
#: Session tokens Nexus would issue, and whom it says each belongs to.
SESSIONS = {"ada-session": ADA, "bea-session": BEA}
#: A token the validator accepts but Nexus cannot place (a deleted user, say).
UNPLACED = "unplaced-session"
#: A token the validator accepts while Nexus itself is failing.
OUTAGE = "outage-session"
PDF = b"%PDF-1.7 a paper"


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    path = tmp_path_factory.mktemp("ownership")
    dataset = DatasetService(
        adapter=DatasetSecondaryAdapterDuckLake(
            catalog=f"sqlite:{path / 'catalog.sqlite'}",
            data_path=str(path / "warehouse") + "/",
        )
    )
    ensure_datasets(dataset)
    storage = Storage()
    storage.put_object("phases_v2/papers", "a.pdf", PDF)
    return SimpleNamespace(
        dataset=dataset, rows=DatasetRowStore(dataset), storage=storage
    )


@pytest.fixture
def env(store, monkeypatch):
    monkeypatch.setenv("ABI_API_KEY", API_KEY)
    # Nexus's validator, as registered at startup: it only says yes or no.
    monkeypatch.setattr(
        abi_api_key_auth,
        "_EXTRA_APP_HTML_TOKEN_VALIDATORS",
        [lambda token, _request: token in SESSIONS or token in (UNPLACED, OUTAGE)],
    )

    identified = []

    async def identify(token):
        identified.append(token)
        if token == OUTAGE:
            raise IdentityUnavailable()
        return SESSIONS.get(token)

    pubmed = PubmedCatalog(store.dataset)
    service = PipelineAppService(
        DatasetRequestStore(store.rows),
        object_storage=store.storage,
        rows=store.rows,
        pubmed_catalog=pubmed,
        collection_sources=StoredMemberSources(store.rows, store.storage, pubmed),
        renderer=PdfOnly(),
    )
    app = FastAPI()
    register(app, service, identify=identify)

    def client(token):
        return TestClient(app, headers={"Authorization": f"Bearer {token}"})

    return SimpleNamespace(
        service=service,
        rows=store.rows,
        storage=store.storage,
        ada=client("ada-session"),
        bea=client("bea-session"),
        key=client(API_KEY),
        unplaced=client(UNPLACED),
        outage=client(OUTAGE),
        identified=identified,
    )


def unique(name):
    """Tests share one store, so each names its things apart."""
    return f"{name} {uuid4().hex[:6]}"


def ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def refused(response, *words):
    assert response.status_code == 403, response.text
    detail = response.json()["detail"]
    for word in words:
        assert word in detail, detail
    return detail


def save_prompt(client, name, text="Claims in {chunk_text}", output_key="results"):
    return client.post(
        f"{PREFIX}/prompts",
        json={"name": name, "template": text, "output_key": output_key},
    )


def create_collection(client, name):
    return ok(
        client.post(
            f"{PREFIX}/collections",
            json={
                "name": name,
                "members": [{"folder": {"prefix": "phases_v2/papers"}}],
            },
        ),
        201,
    )


def run_inputs(collection_id):
    return {
        "collection_id": collection_id,
        "chunker_id": WINDOW_512_128.chunker_id,
        "prompt_ids": [declared_prompts()[0].prompt_id],
        "model_id": DECLARED_MODELS[0].model_id,
    }


def listed(client, what, owner, key="name"):
    body = ok(client.get(f"{PREFIX}/{what}", params={"owner": owner}))
    return {row[key]: row for row in body[what]}


# -- Mine and everyone's -------------------------------------------------


def test_each_sees_their_own_under_mine_and_the_others_under_everyones(env):
    ada_prompt, bea_prompt = unique("Ada prompt"), unique("Bea prompt")
    ok(save_prompt(env.ada, ada_prompt), 201)
    ok(save_prompt(env.bea, bea_prompt), 201)
    ada_set = create_collection(env.ada, unique("Ada set"))
    bea_set = create_collection(env.bea, unique("Bea set"))
    ada_config = ok(
        env.ada.post(
            f"{PREFIX}/pipelines",
            json={
                "name": unique("Ada config"),
                "inputs": run_inputs(ada_set["collection_id"]),
            },
        ),
        201,
    )

    mine = listed(env.ada, "prompts", "mine")
    assert ada_prompt in mine and bea_prompt not in mine
    assert {p["owner_name"] for p in mine.values()} == {"Ada Example"}
    assert all(p["owned_by_you"] and p["can_change"] for p in mine.values())
    everyone = listed(env.ada, "prompts", "all")
    theirs = everyone[bea_prompt]
    assert (theirs["owner_name"], theirs["owned_by_you"], theirs["can_change"]) == (
        "Bea Example",
        False,
        False,
    )
    assert "Duplicate prompt" in theirs["read_only_reason"]

    mine = listed(env.bea, "collections", "mine", "collection_id")
    assert bea_set["collection_id"] in mine and ada_set["collection_id"] not in mine
    everyone = listed(env.bea, "collections", "all", "collection_id")
    adas = everyone[ada_set["collection_id"]]
    assert (adas["owner_name"], adas["owned_by_you"], adas["can_change"]) == (
        "Ada Example",
        False,
        False,
    )

    assert ada_config["pipeline_id"] in listed(
        env.ada, "pipelines", "mine", "pipeline_id"
    )
    assert ada_config["pipeline_id"] not in listed(
        env.bea, "pipelines", "mine", "pipeline_id"
    )
    config = listed(env.bea, "pipelines", "all", "pipeline_id")[
        ada_config["pipeline_id"]
    ]
    assert config["owner_name"] == "Ada Example"


def test_the_built_in_prompts_are_shared_and_read_only(env):
    built_in = {p.prompt_id for p in declared_prompts()}
    prompts = listed(env.ada, "prompts", "all", "prompt_id")
    for prompt_id in built_in:
        prompt = prompts[prompt_id]
        assert (prompt["shared"], prompt["owner_id"], prompt["can_change"]) == (
            True,
            None,
            False,
        )
        assert "shared prompt" in prompt["read_only_reason"]
    assert not built_in & set(listed(env.ada, "prompts", "mine", "prompt_id"))


# -- only the owner changes it -------------------------------------------


def test_neither_saves_a_version_of_the_others_prompt_or_of_a_shared_one(env):
    name = unique("Ada effects")
    ok(save_prompt(env.ada, name, "Effects in {chunk_text}"), 201)

    refused(
        save_prompt(env.bea, name, "Bea's effects in {chunk_text}"),
        "belongs to Ada Example",
        "Duplicate prompt",
    )
    built_in = declared_prompts()[0]
    refused(
        save_prompt(
            env.bea, built_in.name, built_in.template + " More.", built_in.output_key
        ),
        "shared prompt",
        "read-only",
    )
    with pytest.raises(NotAllowed, match="belongs to Ada Example"):
        env.service.management.save_prompt(
            {"name": name, "template": "Again {chunk_text}", "output_key": "results"},
            BEA,
        )
    # Ada's history under the name is still hers alone.
    versions = ok(env.ada.get(f"{PREFIX}/prompts/versions", params={"name": name}))
    assert len(versions["versions"]) == 1
    ok(save_prompt(env.ada, name, "Effects, second version, in {chunk_text}"), 201)


def test_prompt_names_stay_unique_across_users(env):
    name = unique("Claims")
    ok(save_prompt(env.ada, name), 201)

    detail = refused(save_prompt(env.bea, name.upper()), "belongs to Ada Example")
    assert "Choose another name" in detail
    owners = {
        p["owner_id"]
        for p in listed(env.ada, "prompts", "all", "prompt_id").values()
        if p["name"].casefold() == name.casefold()
    }
    assert owners == {ADA.user_id}


def test_neither_changes_the_others_collection(env):
    adas = create_collection(env.ada, unique("Ada set"))
    path = f"{PREFIX}/collections/{adas['collection_id']}"
    [member] = ok(env.ada.get(path))["members"]

    refused(
        env.bea.put(path, json={"name": "Taken", "description": ""}),
        "belongs to Ada Example",
    )
    refused(
        env.bea.post(
            f"{path}/members", json={"members": [{"folder": {"prefix": "phases_v2"}}]}
        ),
        "only its owner changes it",
    )
    refused(env.bea.delete(f"{path}/members/{member['member_id']}"), "Ada Example")
    refused(env.bea.delete(path), "Ada Example")
    refused(
        env.bea.post(
            f"{path}/documents",
            params={"filename": "b.pdf"},
            content=b"%PDF-1.7 Bea's paper",
            headers={"Content-Type": "application/pdf"},
        ),
        "Ada Example",
    )
    for change in (
        lambda: env.service.resources.update_collection(
            adas["collection_id"], {"name": "Taken", "description": ""}, BEA
        ),
        lambda: env.service.resources.archive_collection(adas["collection_id"], BEA),
        lambda: env.service.upload_to_collection(
            adas["collection_id"], "b.pdf", b"%PDF-1.7 x", BEA
        ),
    ):
        with pytest.raises(NotAllowed):
            change()

    after = ok(env.ada.get(path))
    assert (after["name"], len(after["members"])) == (adas["name"], 1)
    # The refused upload stored nothing.
    folder = f"phases_v2/collections/{adas['collection_id']}"
    assert not [p for p, _key in env.storage.files if p.startswith(folder)]
    # Bea can still read it, run on it and search within it.
    assert ok(env.bea.get(path))["can_change"] is False
    ok(env.bea.post(f"{PREFIX}/requests", json=run_inputs(adas["collection_id"])), 201)
    # Ada changes it as before.
    ok(
        env.ada.put(
            path, json={"name": adas["name"] + " renamed", "description": "Hers"}
        )
    )


def test_each_can_duplicate_the_others_prompt(env):
    name = unique("Bea causes")
    ok(save_prompt(env.bea, name, "Causes in {chunk_text}", "causes"), 201)

    copy = env.service.duplicate_prompt(name, actor=ADA)
    assert (copy["name"], copy["owner_id"]) == (f"{name} copy", ADA.user_id)
    assert copy["template"] == "Causes in {chunk_text}"
    # The app duplicates by saving the text under a new name.
    renamed = ok(
        save_prompt(env.ada, unique("Ada causes"), "Causes in {chunk_text}", "causes"),
        201,
    )
    assert renamed["owner_id"] == ADA.user_id
    ok(
        save_prompt(env.ada, copy["name"], "Ada's causes in {chunk_text}", "causes"),
        201,
    )
    assert env.service.duplicate_prompt(name, actor=ADA)["name"] == f"{name} copy 2"


def test_anyone_can_still_edit_a_shared_collection(env):
    legacy_id = str(uuid4())
    env.rows.write_rows(
        "input_collections",
        [
            {
                "collection_id": legacy_id,
                "name": "Old folders",
                "locations": ["phases_v2/papers"],
                "archived": False,
            }
        ],
    )
    shared_id = str(uuid4())
    now = datetime.now(UTC)
    env.rows.write_rows(
        "paper_collections",
        [
            {
                "collection_id": shared_id,
                "name": "Made before owners",
                "description": "",
                "members": [],
                "archived": False,
                "created_at": now,
                "updated_at": now,
            }
        ],
    )

    for client, collection_id in (
        (env.ada, legacy_id),
        (env.bea, shared_id),
        (env.bea, legacy_id),
    ):
        path = f"{PREFIX}/collections/{collection_id}"
        before = ok(client.get(path))
        assert (before["shared"], before["can_change"]) == (True, True)
        updated = ok(
            client.put(path, json={"name": before["name"], "description": "Edited"})
        )
        assert (updated["owner_id"], updated["shared"]) == (None, True)
    ok(
        env.bea.post(
            f"{PREFIX}/collections/{shared_id}/members",
            json={"members": [{"folder": {"prefix": "phases_v2/papers"}}]},
        )
    )
    assert shared_id not in listed(env.ada, "collections", "mine", "collection_id")
    assert shared_id in listed(env.ada, "collections", "all", "collection_id")


# -- runs ------------------------------------------------------------------


def test_a_run_requested_from_the_app_records_its_requester(env):
    collection = create_collection(env.ada, unique("Ada run set"))
    # A requester named in the body is not taken from a user.
    body = dict(run_inputs(collection["collection_id"]), requested_by="someone-else")
    queued = ok(env.ada.post(f"{PREFIX}/requests", json=body), 201)

    request = ok(env.ada.get(f"{PREFIX}/requests/{queued['request_id']}"))
    assert request["requested_by"] == ADA.user_id
    assert (request["requested_by_name"], request["requested_by_you"]) == (
        "Ada Example",
        True,
    )

    def ids(client, owner):
        found = ok(
            client.get(f"{PREFIX}/requests", params={"limit": 100, "owner": owner})
        )
        return {r["request_id"]: r for r in found["requests"]}

    assert queued["request_id"] in ids(env.ada, "mine")
    assert queued["request_id"] not in ids(env.bea, "mine")
    seen_by_bea = ids(env.bea, "all")[queued["request_id"]]
    assert (seen_by_bea["requested_by_name"], seen_by_bea["requested_by_you"]) == (
        "Ada Example",
        False,
    )
    assert all(r["requested_by"] == BEA.user_id for r in ids(env.bea, "mine").values())


def test_a_saved_configuration_run_by_another_user_is_theirs(env):
    collection = create_collection(env.ada, unique("Ada config set"))
    config = ok(
        env.ada.post(
            f"{PREFIX}/pipelines",
            json={
                "name": unique("Weekly"),
                "inputs": run_inputs(collection["collection_id"]),
            },
        ),
        201,
    )
    queued = ok(
        env.bea.post(f"{PREFIX}/pipelines/{config['pipeline_id']}/requests"), 201
    )
    assert ok(env.bea.get(f"{PREFIX}/requests/{queued['request_id']}"))[
        "requested_by_you"
    ]


# -- the API key, and a caller nobody can place --------------------------


def test_the_api_key_acts_as_no_user_and_keeps_its_rights(env):
    name = unique("Scripted")
    saved = ok(save_prompt(env.key, name), 201)
    assert saved["owner_id"] is None
    created = create_collection(env.key, unique("Scripted set"))
    assert (created["owner_id"], created["shared"]) == (None, True)
    # It may still change what a user owns, as before owners existed.
    adas = create_collection(env.ada, unique("Ada set"))
    ok(
        env.key.put(
            f"{PREFIX}/collections/{adas['collection_id']}",
            json={"name": adas["name"], "description": "Fixed by a script"},
        )
    )
    built_in = declared_prompts()[0]
    ok(
        save_prompt(
            env.key,
            built_in.name,
            built_in.template + " Scripted.",
            built_in.output_key,
        ),
        201,
    )
    body = dict(run_inputs(created["collection_id"]), requested_by="nightly-script")
    queued = ok(env.key.post(f"{PREFIX}/requests", json=body), 201)
    assert ok(env.key.get(f"{PREFIX}/requests/{queued['request_id']}"))[
        "requested_by"
    ] == ("nightly-script")
    assert (
        env.service.management.save_prompt(
            {"name": name, "template": "Again {chunk_text}", "output_key": "results"},
            SYSTEM,
        )["owner_id"]
        is None
    )


def test_a_session_nexus_cannot_place_changes_nothing(env):
    detail = refused(
        env.unplaced.post(f"{PREFIX}/collections", json={"name": unique("Nobody's")}),
        "could not tell who you are",
    )
    assert "sign in again" in detail
    refused(
        save_prompt(env.unplaced, unique("Nobody prompt")),
        "could not tell who you are",
    )
    collection = create_collection(env.ada, unique("Ada set"))
    refused(
        env.unplaced.post(
            f"{PREFIX}/requests", json=run_inputs(collection["collection_id"])
        ),
        "could not tell who you are",
    )
    # Reading still works, and nothing is anyone's.
    assert (
        ok(env.unplaced.get(f"{PREFIX}/prompts", params={"owner": "mine"}))["prompts"]
        == []
    )


def test_when_nexus_cannot_answer_the_user_is_asked_to_try_again(env):
    for response in (
        env.outage.get(f"{PREFIX}/prompts"),
        env.outage.post(f"{PREFIX}/collections", json={"name": unique("Outage")}),
        save_prompt(env.outage, unique("Outage prompt")),
    ):
        assert response.status_code == 503, response.text
        detail = response.json()["detail"]
        assert "Try again in a few moments" in detail
        assert "sign in again" not in detail
    # Nothing is remembered about the failure: the next request asks again.
    env.outage.get(f"{PREFIX}/prompts")
    assert env.identified.count(OUTAGE) == 4


def test_an_owner_filter_other_than_mine_or_all_is_refused(env):
    assert (
        env.ada.get(f"{PREFIX}/prompts", params={"owner": "theirs"}).status_code == 422
    )


def test_a_users_name_is_recorded_once_and_follows_a_change_in_nexus(env):
    ok(env.ada.get(f"{PREFIX}/prompts"))
    ok(env.ada.get(f"{PREFIX}/collections", params={"counts": "false"}))
    rows = env.rows.query("SELECT user_id, name, email FROM app_users")
    assert {
        "user_id": ADA.user_id,
        "name": "Ada Example",
        "email": "ada@example.org",
    } in rows

    renamed = Actor.user(ADA.user_id, "Ada Renamed", "ada@example.org")
    env.service.users.remember(renamed)
    assert env.service.users.names()[ADA.user_id] == "Ada Renamed"
    env.service.users.remember(ADA)


# -- uploads into a collection's own folder ------------------------------


def upload_to_folder(client, location, name="b.pdf", content=b"%PDF-1.7 a paper"):
    return client.post(
        f"{PREFIX}/documents",
        params={"location": location, "filename": name},
        content=content,
        headers={"Content-Type": "application/pdf"},
    )


def stored_under(env, folder):
    return [p for p, _key in env.storage.files if p.startswith(folder)]


def test_nobody_uploads_into_another_users_collection_folder(env):
    adas = create_collection(env.ada, unique("Ada uploads"))
    folder = f"phases_v2/collections/{adas['collection_id']}"

    refused(upload_to_folder(env.bea, folder), "belongs to Ada Example")
    refused(upload_to_folder(env.bea, f"{folder}/deeper"), "belongs to Ada Example")
    refused(upload_to_folder(env.unplaced, folder), "could not tell who you are")
    assert stored_under(env, folder) == []
    with pytest.raises(NotAllowed):
        env.service.upload_document(folder, "b.pdf", b"%PDF-1.7 x", BEA)

    ok(upload_to_folder(env.ada, folder), 201)
    assert stored_under(env, folder)
    # Plain storage folders, and the API key, upload as before.
    ok(upload_to_folder(env.bea, "phases_v2/bea-folder"), 201)
    ok(upload_to_folder(env.key, folder, "scripted.pdf", b"%PDF-1.7 script"), 201)


def test_an_archived_collections_folder_takes_uploads_from_the_api_key_only(env):
    adas = create_collection(env.ada, unique("Ada archived"))
    folder = f"phases_v2/collections/{adas['collection_id']}"
    env.service.resources.archive_collection(adas["collection_id"], ADA)

    refused(upload_to_folder(env.ada, folder), "is archived")
    refused(upload_to_folder(env.bea, folder), "is archived")
    assert stored_under(env, folder) == []
    ok(upload_to_folder(env.key, folder, "scripted.pdf", b"%PDF-1.7 script"), 201)


def test_a_shared_collections_folder_still_takes_uploads(env):
    shared_id = str(uuid4())
    now = datetime.now(UTC)
    env.rows.write_rows(
        "paper_collections",
        [
            {
                "collection_id": shared_id,
                "name": "Shared uploads",
                "description": "",
                "members": [],
                "archived": False,
                "created_at": now,
                "updated_at": now,
            }
        ],
    )
    folder = f"phases_v2/collections/{shared_id}"

    ok(upload_to_folder(env.bea, folder), 201)
    assert stored_under(env, folder)


# -- names, and when they cannot be read ---------------------------------


def test_a_papers_page_names_who_requested_each_run(env, monkeypatch):
    monkeypatch.setattr(
        env.service.paper_results,
        "paper",
        lambda paper_id: {
            "requests": [
                {"request_id": "r1", "requested_by": ADA.user_id},
                {"request_id": "r2", "requested_by": None},
            ]
        },
    )
    ok(env.ada.get(f"{PREFIX}/prompts"))  # Ada has used the app

    requests = ok(env.bea.get(f"{PREFIX}/papers/p1"))["requests"]
    assert [(r["requested_by_name"], r["requested_by_you"]) for r in requests] == [
        ("Ada Example", False),
        (None, False),
    ]
    mine = ok(env.ada.get(f"{PREFIX}/papers/p1"))["requests"][0]
    assert mine["requested_by_you"] is True


def test_a_requester_shows_as_their_name_their_label_or_nothing(env, monkeypatch):
    monkeypatch.setattr(
        env.service.paper_results,
        "paper",
        lambda paper_id: {
            "requests": [
                # A run the Assistant queued before runs recorded user ids.
                {"request_id": "r1", "requested_by": "ada@example.org"},
                # A script's run, through the API key.
                {"request_id": "r2", "requested_by": "nightly-script"},
                # Someone who has not used the app since names were recorded.
                {"request_id": "r3", "requested_by": "cy@example.org"},
                {"request_id": "r4", "requested_by": None},
            ]
        },
    )
    ok(env.ada.get(f"{PREFIX}/prompts"))  # Ada has used the app

    seen_by_bea = ok(env.bea.get(f"{PREFIX}/papers/p1"))["requests"]
    assert [(r["requested_by_name"], r["requested_by_you"]) for r in seen_by_bea] == [
        ("Ada Example", False),
        ("nightly-script", False),
        ("cy@example.org", False),
        (None, False),
    ]
    seen_by_ada = ok(env.ada.get(f"{PREFIX}/papers/p1"))["requests"]
    assert [r["requested_by_you"] for r in seen_by_ada] == [True, False, False, False]


def test_a_scripts_run_shows_its_label_in_the_run_list(env):
    collection = create_collection(env.key, unique("Scripted set"))
    body = dict(run_inputs(collection["collection_id"]), requested_by="nightly-script")
    queued = ok(env.key.post(f"{PREFIX}/requests", json=body), 201)

    run = ok(env.bea.get(f"{PREFIX}/requests/{queued['request_id']}"))
    assert (run["requested_by_name"], run["requested_by_you"]) == (
        "nightly-script",
        False,
    )
    listed = ok(env.bea.get(f"{PREFIX}/requests", params={"owner": "all"}))
    mine = [r for r in listed["requests"] if r["request_id"] == queued["request_id"]]
    assert [r["requested_by_name"] for r in mine] == ["nightly-script"]


def test_a_prompts_history_never_splits_by_letter_case(env):
    name = unique("Stress Prompt")
    ok(save_prompt(env.ada, name), 201)

    saved = ok(save_prompt(env.ada, name.lower(), "Stress again {chunk_text}"), 201)
    assert saved["name"] == name
    versions = env.service.versions.versions(name)["versions"]
    assert {v["template"] for v in versions} == {
        "Claims in {chunk_text}",
        "Stress again {chunk_text}",
    }
    names = {p["name"] for p in ok(env.ada.get(f"{PREFIX}/prompts"))["prompts"]}
    assert name in names and name.lower() not in names
    refused(save_prompt(env.bea, name.upper(), "Bea's {chunk_text}"), "Ada Example")


def test_a_copy_never_takes_a_name_already_in_use(env):
    name = unique("Source")
    ok(save_prompt(env.ada, name), 201)
    ok(save_prompt(env.ada, name, "Newer {chunk_text}"), 201)
    other = unique("Other")
    ok(save_prompt(env.ada, other), 201)

    for taken in (other, other.upper(), f" {other.lower()} ", name):
        with pytest.raises(ValueError, match="already named"):
            env.service.duplicate_prompt(name, new_name=taken, actor=ADA)
    assert len(env.service.versions.versions(other)["versions"]) == 1

    fresh = unique("Fresh")
    copy = env.service.duplicate_prompt(name, new_name=fresh, actor=ADA)
    # Without a version named, the newest one is copied.
    assert (copy["name"], copy["template"], copy["owner_id"]) == (
        fresh,
        "Newer {chunk_text}",
        ADA.user_id,
    )
    with pytest.raises(KeyError, match="No prompt is named"):
        env.service.duplicate_prompt(unique("Missing"), actor=ADA)


def test_listings_and_runs_still_work_when_names_cannot_be_read(env, monkeypatch):
    collection = create_collection(env.ada, unique("Ada set"))

    def unreadable():
        raise RuntimeError("catalog unavailable")

    monkeypatch.setattr(env.service.users, "_read", unreadable)
    prompts = ok(env.bea.get(f"{PREFIX}/prompts"))["prompts"]
    owned = [p for p in prompts if p["owner_id"] == ADA.user_id]
    assert owned and {p["owner_name"] for p in owned} == {"another user"}
    ok(env.bea.get(f"{PREFIX}/collections", params={"counts": "false"}))
    ok(env.bea.get(f"{PREFIX}/pipelines"))
    ok(env.bea.get(f"{PREFIX}/requests"))
    ok(
        env.bea.post(
            f"{PREFIX}/requests", json=run_inputs(collection["collection_id"])
        ),
        201,
    )


def test_a_copy_of_a_long_prompt_name_fits(env):
    name = "L" * 94 + uuid4().hex[:6]
    ok(save_prompt(env.bea, name), 201)

    first = env.service.duplicate_prompt(name, actor=ADA)
    second = env.service.duplicate_prompt(name, actor=ADA)
    assert first["name"] == name[:95] + " copy"
    assert second["name"] == name[:93] + " copy 2"
    assert len(first["name"]) == len(second["name"]) == 100


def test_who_a_session_belongs_to_is_asked_of_nexus_once_a_minute(env):
    for _ in range(3):
        ok(env.ada.get(f"{PREFIX}/requests"))
    assert env.identified.count("ada-session") == 1
