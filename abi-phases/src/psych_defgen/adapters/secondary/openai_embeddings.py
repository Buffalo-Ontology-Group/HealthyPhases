"""The ``Embedder`` port on the OpenAI embeddings API.

Upstream used ``all-MiniLM-L6-v2`` locally, which reads at most 256 word
pieces of a text. Here a text is cut to ``MAX_INPUT_CHARS`` characters, well
under the API's 8,191-token limit, so one long passage cannot fail a run.
"""

import openai
from openai import OpenAI

from psych_defgen.adapters.secondary.openai_errors import describe_openai_error
from psych_defgen.domain.errors import ServiceError

MAX_INPUT_CHARS = 16_000


class EmbeddingError(ServiceError):
    pass


class OpenAIEmbedder:
    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        client=None,
        batch_size: int = 100,
        timeout: float = 60.0,
        max_retries: int = 3,
    ):
        if client is None:
            if not api_key:
                raise ValueError("OpenAI embeddings need OPENAI_API_KEY.")
            # The SDK retries 408, 409, 429, 5xx and connection errors with
            # exponential back-off.
            client = OpenAI(api_key=api_key, timeout=timeout, max_retries=max_retries)
        self.client = client
        self.model = model
        self.batch_size = batch_size

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = [
                text[:MAX_INPUT_CHARS]
                for text in texts[start : start + self.batch_size]
            ]
            try:
                response = self.client.embeddings.create(model=self.model, input=batch)
            except openai.OpenAIError as error:
                raise EmbeddingError(
                    describe_openai_error("OpenAI embeddings", "OPENAI_API_KEY", error)
                ) from None
            data = sorted(response.data, key=lambda item: item.index)
            if len(data) != len(batch):
                raise EmbeddingError(
                    f"OpenAI embeddings returned {len(data)} vectors for "
                    f"{len(batch)} texts."
                )
            vectors.extend(list(item.embedding) for item in data)
        return vectors
