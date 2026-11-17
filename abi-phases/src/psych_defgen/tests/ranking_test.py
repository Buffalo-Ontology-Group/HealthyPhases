import numpy as np
import pytest

from psych_defgen.application.rag_retrieval import (
    cosine_similarity,
    retrieve_relevant_texts,
)
from psych_defgen.domain.errors import ServiceError
from psych_defgen.tests.fakes import FakeEmbedder, passage


def item(pmid, score, label=""):
    return {
        "text": passage(f"Loneliness item {pmid}{label} [s={score}]."),
        "title": f"Article {pmid}",
        "pmid": pmid,
        "pmcid": None,
        "source": "PubMed abstract",
    }


def test_ranks_by_mean_similarity_to_the_five_queries():
    embedder = FakeEmbedder()
    items = [item("1", 0.2), item("2", 0.9), item("3", 0.5)]

    ranked = retrieve_relevant_texts("loneliness", items, embedder, top_k=10)

    assert [entry["pmid"] for entry in ranked] == ["2", "3", "1"]
    assert [round(entry["score"], 6) for entry in ranked] == [0.9, 0.5, 0.2]
    assert embedder.calls[0] == [
        "What is loneliness?",
        "How is loneliness defined in scientific literature?",
        "What does loneliness refer to?",
        "What are the defining characteristics of loneliness?",
        "Conceptual definition and meaning of loneliness",
    ]
    assert all(entry["context_text"] == entry["text"] for entry in ranked)


def test_keeps_at_most_two_passages_per_article():
    items = [
        item("1", 0.9, "a"),
        item("1", 0.8, "b"),
        item("1", 0.7, "c"),
        item("2", 0.1),
    ]

    ranked = retrieve_relevant_texts("loneliness", items, FakeEmbedder(), top_k=10)

    assert [entry["pmid"] for entry in ranked] == ["1", "1", "2"]
    assert not any("item 1c" in entry["text"] for entry in ranked)


def test_stops_at_top_k():
    items = [item(str(n), 0.1 * n) for n in range(1, 6)]

    ranked = retrieve_relevant_texts("loneliness", items, FakeEmbedder(), top_k=2)

    assert [entry["pmid"] for entry in ranked] == ["5", "4"]


def test_drops_short_texts_and_skips_embedding_when_nothing_is_left():
    embedder = FakeEmbedder()

    ranked = retrieve_relevant_texts(
        "loneliness", [{"text": "Too short.", "pmid": "1"}], embedder
    )

    assert ranked == []
    assert embedder.calls == []


def test_embedding_failures_propagate():
    embedder = FakeEmbedder(error=ServiceError("OpenAI embeddings timed out."))

    with pytest.raises(ServiceError):
        retrieve_relevant_texts("loneliness", [item("1", 0.5)], embedder)


def test_cosine_similarity_matches_scikit_learn_for_zero_vectors():
    similarity = cosine_similarity([[1.0, 0.0], [0.0, 0.0]], [[3.0, 4.0]])

    assert np.allclose(similarity, [[0.6], [0.0]])
