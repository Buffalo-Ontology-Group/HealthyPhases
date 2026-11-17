"""Rank evidence by similarity to five definition queries.

Upstream embedded with ``all-MiniLM-L6-v2`` (sentence-transformers) and
compared with scikit-learn's ``cosine_similarity``. Here the vectors come from
the ``Embedder`` port and the cosine similarity is computed with numpy; the
queries, the mean, the sort and the 2-per-article cap are upstream's.
"""

import numpy as np

from psych_defgen.domain.definition_patterns import DEFINITION_KEYWORDS
from psych_defgen.domain.select_definition_sentence import (
    clean_sentence,
    is_explicit_definition_sentence,
    is_fallback_definition_sentence,
    split_sentences,
)


def cosine_similarity(a, b):
    """scikit-learn's ``cosine_similarity(a, b)``: zero vectors score 0."""

    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)

    a_norms = np.linalg.norm(a, axis=1, keepdims=True)
    b_norms = np.linalg.norm(b, axis=1, keepdims=True)
    a_norms[a_norms == 0] = 1.0
    b_norms[b_norms == 0] = 1.0

    return (a / a_norms) @ (b / b_norms).T


def has_definition_pattern(text, term=None):
    """
    Check whether a passage contains general
    or term-specific definitional language.

    Term-specific patterns help identify sentences
    such as:

    "Gerotranscendence is a psychosocial theory..."
    """

    if not text:
        return False

    text_lower = text.lower()

    if any(
        pattern in text_lower
        for pattern in DEFINITION_KEYWORDS
    ):
        return True

    # Term-specific definition patterns.
    if term:
        term_lower = term.lower().strip()

        term_patterns = [
            f"{term_lower} is a ",
            f"{term_lower} is an ",
            f"{term_lower} refers to ",
            f"{term_lower} describes ",
            f"{term_lower} represents ",
            f"{term_lower} involves ",
            f"{term_lower} can be defined as ",
            f"{term_lower} can be conceptualized as ",
            f"{term_lower} is defined as ",
            f"{term_lower} is conceptualized as ",
            f"{term_lower} is characterized by ",
            f"{term_lower} is understood as ",
        ]

        if any(
            pattern in text_lower
            for pattern in term_patterns
        ):
            return True

    return False


def is_useful_text(text: str, term: str) -> bool:
    words = text.split()

    if len(words) < 12:
        return False

    if has_definition_pattern(text, term):
        return True

    return len(words) >= 30


def normalize_evidence_item(item):
    """
    Convert an evidence item into a consistent
    dictionary format.

    Dictionary records preserve article metadata.
    Plain strings remain supported for backward
    compatibility.
    """

    if isinstance(item, str):
        return {
            "text": item,
            "title": None,
            "authors": [],
            "journal": None,
            "year": None,
            "pmid": None,
            "pmcid": None,
            "source": None,
            "pubmed_url": None,
            "url": None,
        }

    if not isinstance(item, dict):
        return None

    text = item.get("text")

    if not text:
        return None

    return {
        "text": text,
        "title": item.get("title"),
        "authors": item.get("authors", []),
        "journal": item.get("journal"),
        "year": item.get("year"),
        "pmid": item.get("pmid"),
        "pmcid": item.get("pmcid"),
        "source": item.get("source"),
        "pubmed_url": item.get("pubmed_url"),
        "url": item.get("url"),
    }

def select_evidence_sentence(term, passage):
    """
    Select a definition-like sentence without
    calculating sentence similarity scores.
    """

    sentences = []

    for sentence in split_sentences(passage):
        sentence = clean_sentence(sentence)

        if not sentence:
            continue

        words = sentence.split()

        if len(words) < 8 or len(words) > 150:
            continue

        if sum(char.isalpha() for char in sentence) < 30:
            continue

        sentences.append(sentence)

    for category, checker in [
        ("Explicit definition", is_explicit_definition_sentence),
        ("Conceptual definition", is_fallback_definition_sentence),
    ]:
        for sentence in sentences:
            if checker(sentence, term):
                return {
                    "evidence_sentence": sentence,
                    "evidence_type": category,
                }

    return {
        "evidence_sentence": None,
        "evidence_type": "Related evidence" if sentences else "Unusable extracted text",
    }

def retrieve_relevant_texts(
    term,
    items,
    embedder,
    top_k=5,
):
    """
    Retrieve the top-k semantically relevant
    evidence passages while preserving article
    metadata.

    Definition-like passages are retained during
    filtering, but final ranking remains based
    only on cosine similarity.

    No heuristic score boosts are added.
    """

    normalized_items = []

    for item in items:
        normalized_item = normalize_evidence_item(
            item
        )

        if normalized_item is None:
            continue

        if not is_useful_text(
            normalized_item["text"],
            term=term,
        ):
            continue

        normalized_items.append(
            normalized_item
        )

    if not normalized_items:
        return []

    # Multiple definition-focused queries.
    queries = [
        f"What is {term}?",
        f"How is {term} defined in scientific literature?",
        f"What does {term} refer to?",
        f"What are the defining characteristics of {term}?",
        f"Conceptual definition and meaning of {term}",
    ]

    # Generate query embeddings.
    query_embeddings = np.asarray(
        embedder.embed(queries),
        dtype=float,
    )

    # Generate evidence embeddings.
    evidence_texts = [
        item["text"]
        for item in normalized_items
    ]

    text_embeddings = np.asarray(
        embedder.embed(evidence_texts),
        dtype=float,
    )

    # Calculate similarity for every query-evidence pair.
    similarity_matrix = cosine_similarity(
        query_embeddings,
        text_embeddings,
    )

    # Average the five similarity scores per passage.
    similarities = similarity_matrix.mean(axis=0)

    scored_items = []

    for index, item in enumerate(
        normalized_items
    ):
        scored_item = item.copy()

        scored_item["score"] = float(
            similarities[index]
        )

        scored_items.append(
            scored_item
        )

    scored_items.sort(
        key=lambda item: item["score"],
        reverse=True,
    )

    selected = []
    article_counts = {}

    for item in scored_items:
        article_id = (
            item.get("pmid")
            or item.get("pmcid")
            or item.get("title")
            or id(item)
        )

        count = article_counts.get(article_id, 0)

        if count >= 2:
            continue

        selected.append(item)
        article_counts[article_id] = count + 1

        if len(selected) >= top_k:
            break

    for item in selected:
        item["context_text"] = item["text"]

    return selected
