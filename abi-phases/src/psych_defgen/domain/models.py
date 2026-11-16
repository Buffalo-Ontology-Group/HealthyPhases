from dataclasses import dataclass


@dataclass
class Article:
    """Represent a retrieved article and its text and metadata."""

    pmid: str
    title: str
    source: str
    text: str
    pmcid: str | None = None


@dataclass
class TextChunk:
    """Represent a text chunk with its source metadata and optional score."""

    pmid: str
    title: str
    text: str
    source: str
    pmcid: str | None = None
    score: float | None = None