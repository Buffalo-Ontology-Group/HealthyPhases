from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from psych_defgen.adapters.primary.runs_api import router
from psych_defgen.tests.conftest import SECRETS, make_runs

API = "/psych_defgen/api"
TOKEN = "psych-defgen-test-token"


def client_for(runs, *, token=TOKEN):
    app = FastAPI()
    app.include_router(router(runs))
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return TestClient(app, headers=headers)


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    monkeypatch.setenv("ABI_API_KEY", TOKEN)


@pytest.fixture
def client(runs):
    return client_for(runs)


def test_settings(client):
    response = client.get(f"{API}/settings")
    assert response.status_code == 200
    assert response.json() == {
        "models": ["openai/gpt-4.1-mini", "anthropic/claude-sonnet-4"],
        "default_model": "openai/gpt-4.1-mini",
        "embedding_model": "text-embedding-3-small",
        "max_results": {"default": 100, "min": 1, "max": 200},
        "top_k": {"default": 10, "min": 1, "max": 20},
        "missing": [],
    }


def test_settings_name_the_missing_keys(store):
    client = client_for(make_runs(store, ncbi_email="", openrouter_api_key=None))
    assert client.get(f"{API}/settings").json()["missing"] == [
        "NCBI_EMAIL",
        "OPENROUTER_API_KEY",
    ]


def test_create_list_and_get(client):
    created = client.post(f"{API}/runs", json={"term": " loneliness ", "top_k": 5})
    assert created.status_code == 201
    run = created.json()
    assert run["term"] == "loneliness"
    assert (run["max_results"], run["top_k"]) == (100, 5)
    assert run["status"] == "pending"
    assert run["result"] is None
    assert run["created_at"] == "2026-11-16T10:00:00Z"
    client.post(f"{API}/runs", json={"term": "solitude"})

    listed = client.get(f"{API}/runs").json()
    assert listed["total"] == 2
    assert (listed["page"], listed["page_count"]) == (1, 1)
    assert [r["term"] for r in listed["runs"]] == ["solitude", "loneliness"]
    assert all("result" not in r for r in listed["runs"])
    one_per_page = client.get(f"{API}/runs?page=2&page_size=1").json()
    assert [r["term"] for r in one_per_page["runs"]] == ["loneliness"]
    assert one_per_page["page_count"] == 2

    fetched = client.get(f"{API}/runs/{run['run_id']}")
    assert fetched.status_code == 200
    assert fetched.json() == run


def test_a_finished_run_has_its_result_but_the_list_does_not(client, runs):
    run_id = client.post(f"{API}/runs", json={"term": "loneliness"}).json()["run_id"]
    runs.start(run_id)
    runs.succeed(run_id, {"term": "loneliness", "definition": "A feeling."})
    assert client.get(f"{API}/runs/{run_id}").json()["result"] == {
        "term": "loneliness",
        "definition": "A feeling.",
    }
    listed = client.get(f"{API}/runs").json()["runs"][0]
    assert listed["status"] == "succeeded"
    assert "result" not in listed


@pytest.mark.parametrize(
    "body",
    [
        {"term": ""},
        {"term": "x" * 201},
        {"term": "a", "max_results": 201},
        {"term": "a", "top_k": 0},
        {"term": "a", "model": "someone/else"},
        ["a"],
    ],
)
def test_invalid_requests_are_422_with_a_readable_detail(client, body):
    response = client.post(f"{API}/runs", json=body)
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)
    assert client.get(f"{API}/runs").json()["total"] == 0


def test_a_request_without_a_body_is_422(client):
    response = client.post(f"{API}/runs")
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)


@pytest.mark.parametrize(
    "content", [b'{"term": "loneliness"', b"term=loneliness", b'{"term": "\xff"}']
)
def test_a_malformed_body_is_422_with_a_readable_detail(client, content):
    response = client.post(
        f"{API}/runs", content=content, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    assert response.json() == {
        "detail": 'The request body must be JSON, such as {"term": "loneliness"}.'
    }
    assert client.get(f"{API}/runs").json()["total"] == 0


@pytest.mark.parametrize("query", ["page=1_0", "page_size=%D9%A1"])
def test_page_numbers_must_be_plain_digits(client, query):
    response = client.get(f"{API}/runs?{query}")
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)


@pytest.mark.parametrize("query", ["page=0", "page_size=0", "page_size=101", "page=x"])
def test_bad_pages_are_422(client, query):
    response = client.get(f"{API}/runs?{query}")
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)


def test_missing_keys_are_409_naming_them(store):
    client = client_for(make_runs(store, openai_api_key=""))
    response = client.post(f"{API}/runs", json={"term": "loneliness"})
    assert response.status_code == 409
    assert "OPENAI_API_KEY" in response.json()["detail"]
    assert client.get(f"{API}/runs").json()["total"] == 0


def test_unknown_run_is_404(client):
    response = client.get(f"{API}/runs/does-not-exist")
    assert response.status_code == 404
    assert response.json() == {"detail": "Definition run not found."}


def test_every_endpoint_needs_the_api_token(runs):
    for token in (None, "wrong"):
        client = client_for(runs, token=token)
        for method, path in [
            ("get", "/settings"),
            ("post", "/runs"),
            ("get", "/runs"),
            ("get", "/runs/x"),
        ]:
            response = client.request(method, API + path, json={"term": "a"})
            assert response.status_code in (401, 403), (method, path)


def test_no_response_holds_a_secret(store):
    runs = make_runs(store)
    partial = make_runs(store, openai_api_key="")
    responses = []
    for client in (client_for(runs), client_for(partial)):
        responses += [
            client.get(f"{API}/settings"),
            client.post(f"{API}/runs", json={"term": "loneliness"}),
            client.post(f"{API}/runs", json={"term": ""}),
            client.get(f"{API}/runs"),
            client.get(f"{API}/runs/nope"),
        ]
    run_id = responses[1].json()["run_id"]
    runs.start(run_id)
    runs.fail(run_id, "Step 'search' failed: NCBI refused the request (HTTP 429).")
    responses.append(client_for(runs).get(f"{API}/runs/{run_id}"))
    text = "".join(response.text for response in responses)
    for value in SECRETS.values():
        assert value not in text


# Module wiring -------------------------------------------------------------


def module(dataset=None, **config):
    from psych_defgen import ABIModule
    from psych_defgen.tests.conftest import configuration

    services = SimpleNamespace(
        dataset_available=lambda: dataset is not None,
        dataset=dataset,
        secret=SimpleNamespace(get=lambda name, default=None: default),
    )
    return ABIModule(SimpleNamespace(services=services), configuration(**config))


def test_the_module_mounts_the_api_and_creates_the_table(dataset):
    from psych_defgen.adapters.secondary.definition_run_store import NAMESPACE, TABLE

    loaded = module(dataset)
    loaded.on_initialized()
    assert dataset.describe(TABLE, namespace=NAMESPACE)
    loaded.on_initialized()  # idempotent

    app = FastAPI()
    loaded.api(app)
    client = TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"})
    assert client.get(f"{API}/settings").status_code == 200
    assert client.post(f"{API}/runs", json={"term": "loneliness"}).status_code == 201


def test_the_module_stays_inert_without_a_dataset_or_keys(caplog_messages):
    loaded = module(None, ncbi_email="", openrouter_api_key="", openai_api_key="")
    loaded.on_initialized()
    app = FastAPI()
    loaded.api(app)
    assert not [route for route in app.routes if API in getattr(route, "path", "")]
    joined = " ".join(caplog_messages)
    assert "NCBI_EMAIL, OPENROUTER_API_KEY, OPENAI_API_KEY" in joined
    assert "dataset service" in joined
    for value in SECRETS.values():
        assert value not in joined


def test_a_wiring_error_is_logged_and_does_not_stop_the_engine(
    dataset, monkeypatch, caplog_messages
):
    def broken(*_args):
        raise RuntimeError("wiring broke")

    monkeypatch.setattr("psych_defgen.factory.definition_runs", broken)
    app = FastAPI()
    module(dataset).api(app)
    (logged,) = [m for m in caplog_messages if "Failed to mount" in m]
    # The traceback is kept, not just the message.
    assert "Traceback" in logged and "RuntimeError: wiring broke" in logged
