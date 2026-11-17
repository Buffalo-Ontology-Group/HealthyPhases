"""In-memory stand-ins for the ports; nothing here reaches the network."""

import math
import re

from psych_defgen.domain.errors import ServiceError

FILLER = (
    "Participants in community samples described their daily routines, "
    "their contacts with family and friends, and their use of local services "
    "over the course of the study period in considerable detail."
)


def passage(sentence: str) -> str:
    """A passage long enough to pass upstream's usefulness filter."""
    return f"{sentence} {FILLER}"


class FakeEmbedder:
    """Every query embeds to (1, 0); a text holding ``[s=0.42]`` embeds so
    that its cosine similarity to the queries is 0.42 (default 0.1)."""

    model = "fake-embedding"

    def __init__(self, error: Exception | None = None):
        self.calls: list[list[str]] = []
        self.error = error

    def embed(self, texts):
        self.calls.append(list(texts))
        if self.error is not None:
            raise self.error
        vectors = []
        for text in texts:
            if text.startswith(("What ", "How ", "Conceptual ")):
                vectors.append([1.0, 0.0])
                continue
            match = re.search(r"\[s=([0-9.]+)\]", text)
            score = float(match.group(1)) if match else 0.1
            vectors.append([score, math.sqrt(1 - score * score)])
        return vectors


def usage(model, input_tokens, output_tokens):
    return {
        "provider": "openrouter",
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": (
            input_tokens + output_tokens
            if input_tokens is not None and output_tokens is not None
            else None
        ),
    }


class FakeLanguageModel:
    provider = "openrouter"

    def __init__(
        self,
        *,
        selection='{"definition": null}',
        synthesis="Loneliness is a distressing feeling of unmet social needs.",
        selection_tokens=(10, 5),
        synthesis_tokens=(100, 20),
        error: Exception | None = None,
    ):
        self.selection = selection
        self.synthesis = synthesis
        self.selection_tokens = selection_tokens
        self.synthesis_tokens = synthesis_tokens
        self.error = error
        self.calls = []

    def chat(self, messages, *, model, json_mode=False):
        self.calls.append(
            {"messages": messages, "model": model, "json_mode": json_mode}
        )
        if self.error is not None:
            raise self.error
        if json_mode:
            return {
                "text": self.selection,
                "usage": usage(model, *self.selection_tokens),
            }
        return {"text": self.synthesis, "usage": usage(model, *self.synthesis_tokens)}


class FakeDictionary:
    def __init__(self):
        self.terms = []

    def lookup(self, term):
        self.terms.append(term)
        return {
            "term": term,
            "source": "APA Dictionary of Psychology",
            "url": f"https://dictionary.apa.org/{term}",
            "status": "found",
            "entry_heading": term,
            "http_status": 200,
        }


class FakeLiterature:
    def __init__(
        self,
        *,
        pmids=(),
        pmc=None,
        full_texts=None,
        abstracts=(),
        authors=None,
        search_error: Exception | None = None,
    ):
        self.pmids = list(pmids)
        self.pmc = dict(pmc or {})
        self.full_texts = dict(full_texts or {})
        self.abstract_records = list(abstracts)
        self.author_names = dict(authors or {})
        self.search_error = search_error
        self.calls = []

    def search(self, term, max_results):
        self.calls.append(("search", term, max_results))
        if self.search_error is not None:
            raise self.search_error
        return self.pmids[:max_results]

    def pmc_ids(self, pmids):
        self.calls.append(("pmc_ids", list(pmids)))
        return {pmid: self.pmc[pmid] for pmid in pmids if pmid in self.pmc}

    def full_text(self, pmcid):
        self.calls.append(("full_text", pmcid))
        value = self.full_texts[pmcid]
        if isinstance(value, Exception):
            raise value
        return value

    def abstracts(self, pmids):
        self.calls.append(("abstracts", list(pmids)))
        return [record for record in self.abstract_records if record["pmid"] in pmids]

    def authors(self, pmids):
        self.calls.append(("authors", sorted(pmids)))
        return {
            pmid: self.author_names[pmid] for pmid in pmids if pmid in self.author_names
        }


def abstract_record(pmid, text, title="An abstract"):
    return {
        "pmid": pmid,
        "title": title,
        "authors": ["Doe J"],
        "journal": "Journal of Studies",
        "year": "2024",
        "abstract": text,
        "source": "PubMed abstract",
        "pubmed_url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
    }


class Unavailable(ServiceError):
    pass
