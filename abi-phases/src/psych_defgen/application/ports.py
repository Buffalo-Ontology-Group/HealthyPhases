"""Ports the definition service needs from hosted services.

Each adapter raises ``psych_defgen.domain.errors.ServiceError`` with a message
that names the service and never holds a secret value.
"""

from typing import Any, Protocol, TypedDict


class LlmUsage(TypedDict):
    """Token usage in upstream's ``generate_llm_response`` shape."""

    provider: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None


class LlmResponse(TypedDict):
    text: str
    usage: LlmUsage | None


class Literature(Protocol):
    """PubMed and PubMed Central, as upstream's Entrez helpers used them."""

    def search(self, term: str, max_results: int) -> list[str]:
        """PMIDs whose title or abstract contains ``term``, by relevance."""
        ...

    def pmc_ids(self, pmids: list[str]) -> dict[str, str]:
        """Map each PMID that has a PMC copy to its ``PMC…`` ID.

        A PMID whose lookup fails is left out, as upstream does.
        """
        ...

    def full_text(self, pmcid: str) -> tuple[str, str]:
        """The PMC article as ``(title, text)``."""
        ...

    def abstracts(self, pmids: list[str]) -> list[dict[str, Any]]:
        """Abstract records: pmid, title, authors, journal, year, abstract,
        source and pubmed_url, for the PMIDs that have an abstract."""
        ...

    def authors(self, pmids: list[str]) -> dict[str, list[str]]:
        """Author names by PMID."""
        ...


class Embedder(Protocol):
    model: str

    def embed(self, texts: list[str]) -> list[list[float]]:
        """One vector per text, in order."""
        ...


class LanguageModel(Protocol):
    provider: str

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        json_mode: bool = False,
    ) -> LlmResponse:
        """The stripped reply text and its token usage."""
        ...


class Dictionary(Protocol):
    def lookup(self, term: str) -> dict[str, Any]:
        """The APA Dictionary entry check, in upstream's ``apa_entry`` shape.

        Never raises for a network failure: that is ``status: request_error``.
        """
        ...
