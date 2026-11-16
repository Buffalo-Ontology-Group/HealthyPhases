"""OpenRouter chat and OpenAI embeddings adapters, with fake SDK clients."""

from types import SimpleNamespace

import httpx
import openai
import pytest

from psych_defgen.adapters.secondary.openai_embeddings import (
    MAX_INPUT_CHARS,
    EmbeddingError,
    OpenAIEmbedder,
)
from psych_defgen.adapters.secondary.openrouter_chat import (
    OPENROUTER_BASE_URL,
    LanguageModelError,
    OpenRouterChat,
)

SECRET = "sk-or-v1-0123456789abcdef"


def status_error(cls, status):
    request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
    response = httpx.Response(status, request=request)
    # Providers echo (part of) the key in error bodies: it must not leak.
    message = f"Incorrect API key provided: {SECRET}"
    return cls(message, response=response, body={"error": {"message": message}})


class FakeCompletions:
    def __init__(self, response=None, error=None):
        self.response, self.error = response, error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


def chat_client(response=None, error=None):
    completions = FakeCompletions(response, error)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


def completion(content, prompt_tokens=12, completion_tokens=3, usage=True):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=(
            SimpleNamespace(
                prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
            )
            if usage
            else None
        ),
    )


def test_openrouter_returns_text_and_upstream_usage():
    client, completions = chat_client(completion('  {"definition": null}\n'))

    result = OpenRouterChat(client=client).chat(
        [{"role": "user", "content": "hi"}], model="openai/gpt-4.1-mini", json_mode=True
    )

    assert result == {
        "text": '{"definition": null}',
        "usage": {
            "provider": "openrouter",
            "model": "openai/gpt-4.1-mini",
            "input_tokens": 12,
            "output_tokens": 3,
            "total_tokens": 15,
        },
    }
    assert completions.calls == [
        {
            "model": "openai/gpt-4.1-mini",
            "messages": [{"role": "user", "content": "hi"}],
            "response_format": {"type": "json_object"},
        }
    ]


def test_openrouter_without_json_mode_or_usage():
    client, completions = chat_client(completion(None, usage=False))

    result = OpenRouterChat(client=client).chat([], model="m")

    assert "response_format" not in completions.calls[0]
    assert result["text"] == ""
    assert result["usage"]["total_tokens"] is None


def test_openrouter_client_uses_upstreams_endpoint_and_timeout():
    chat = OpenRouterChat(api_key="test-key")

    assert str(chat.client.base_url).rstrip("/") == OPENROUTER_BASE_URL
    assert chat.client.timeout == 300.0


def test_openrouter_requires_its_own_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-sent-to-openrouter")
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        OpenRouterChat(api_key="")


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            status_error(openai.AuthenticationError, 401),
            "OpenRouter refused the request (HTTP 401); check OPENROUTER_API_KEY.",
        ),
        (
            status_error(openai.NotFoundError, 404),
            "OpenRouter does not offer the requested model (HTTP 404)",
        ),
        (
            status_error(openai.InternalServerError, 502),
            "OpenRouter is unavailable (HTTP 502); retry later.",
        ),
        (
            openai.APITimeoutError(request=httpx.Request("POST", "https://x.invalid")),
            "OpenRouter timed out; retry later.",
        ),
    ],
)
def test_openrouter_failures_are_readable_and_secret_free(error, expected):
    client, _ = chat_client(error=error)

    with pytest.raises(LanguageModelError) as raised:
        OpenRouterChat(client=client).chat([], model="openai/gpt-4.1-mini")

    assert expected in str(raised.value)
    assert "openai/gpt-4.1-mini" in str(raised.value)
    assert SECRET not in str(raised.value)
    assert raised.value.__cause__ is None and raised.value.__suppress_context__


def test_openrouter_without_choices_is_an_error():
    client, _ = chat_client(SimpleNamespace(choices=None, usage=None))

    with pytest.raises(LanguageModelError, match="no answer"):
        OpenRouterChat(client=client).chat([], model="m")


class FakeEmbeddings:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def create(self, *, model, input):
        self.calls.append({"model": model, "input": list(input)})
        if self.error is not None:
            raise self.error
        # Out of order on purpose: the API reports each vector's index.
        data = [
            SimpleNamespace(index=index, embedding=[float(len(text)), 1.0])
            for index, text in enumerate(input)
        ]
        return SimpleNamespace(data=list(reversed(data)))


def test_embeddings_are_batched_and_kept_in_order():
    embeddings = FakeEmbeddings()
    client = SimpleNamespace(embeddings=embeddings)
    embedder = OpenAIEmbedder(
        model="text-embedding-3-small", client=client, batch_size=2
    )

    vectors = embedder.embed(["a", "bb", "ccc", "x" * (MAX_INPUT_CHARS + 10)])

    assert vectors == [
        [1.0, 1.0],
        [2.0, 1.0],
        [3.0, 1.0],
        [float(MAX_INPUT_CHARS), 1.0],
    ]
    assert [len(call["input"]) for call in embeddings.calls] == [2, 2]
    assert {call["model"] for call in embeddings.calls} == {"text-embedding-3-small"}
    assert embedder.model == "text-embedding-3-small"


def test_embedding_client_retries():
    embedder = OpenAIEmbedder(model="m", api_key="test-key")
    assert embedder.client.max_retries == 3


def test_embedding_failures_are_readable_and_secret_free():
    error = status_error(openai.AuthenticationError, 401)
    client = SimpleNamespace(embeddings=FakeEmbeddings(error=error))

    with pytest.raises(EmbeddingError) as raised:
        OpenAIEmbedder(model="m", client=client).embed(["text"])

    assert str(raised.value) == (
        "OpenAI embeddings refused the request (HTTP 401); check OPENAI_API_KEY."
    )
    assert SECRET not in str(raised.value)
    assert raised.value.__cause__ is None


def test_embedding_count_mismatch_is_an_error():
    class Short(FakeEmbeddings):
        def create(self, *, model, input):
            return SimpleNamespace(data=[])

    with pytest.raises(EmbeddingError, match="0 vectors for 1 texts"):
        OpenAIEmbedder(model="m", client=SimpleNamespace(embeddings=Short())).embed(
            ["text"]
        )
