import json

import pytest

from psych_defgen.adapters.secondary.openrouter_chat import LanguageModelError
from psych_defgen.application.definition_service import (
    STAGES,
    UPSTREAM,
    DefinitionService,
)
from psych_defgen.domain.errors import DefinitionStepError
from psych_defgen.domain.evidence import INSUFFICIENT_EVIDENCE, NO_DEFINITION_FOUND
from psych_defgen.tests.fakes import (
    FakeDictionary,
    FakeEmbedder,
    FakeLanguageModel,
    FakeLiterature,
    Unavailable,
    abstract_record,
    passage,
)

DEFINITION = (
    "Loneliness is defined as a distressing feeling that accompanies the "
    "perception that social needs are not being met."
)
MODEL = "openai/gpt-4.1-mini"

RESULT_KEYS = {
    "term",
    "apa_entry",
    "definition",
    "definition_method",
    "generated_definition",
    "llm_usage",
    "evidence",
    "pmids",
    "pmid_to_pmcid",
    "embedding_model",
    "counts",
    "upstream",
}


def service(literature, language_model=None, embedder=None):
    return DefinitionService(
        literature=literature,
        embedder=embedder or FakeEmbedder(),
        language_model=language_model or FakeLanguageModel(),
        dictionary=FakeDictionary(),
    )


def define(svc, **kwargs):
    options = {"max_results": 100, "top_k": 10, "model": MODEL}
    options.update(kwargs)
    return svc.define("loneliness", **options)


def full_text_literature():
    body = (
        passage(f"{DEFINITION} [s=0.9]")
        + "\n"
        + passage("Loneliness was more common among adults living alone [s=0.4].")
    )
    return FakeLiterature(
        pmids=["11", "22"],
        pmc={"11": "PMC111"},
        full_texts={"PMC111": ("A full-text article", body)},
        abstracts=[
            abstract_record("22", passage("Loneliness predicted poorer sleep [s=0.3]."))
        ],
        authors={"11": ["Ada Lovelace"], "22": ["Jane Doe"]},
    )


def test_full_text_path_returns_upstreams_result_with_our_additions():
    literature = full_text_literature()
    llm = FakeLanguageModel()

    result = define(service(literature, llm))

    assert set(result) == RESULT_KEYS
    assert result["term"] == "loneliness"
    assert result["apa_entry"]["status"] == "found"
    assert result["definition"] == DEFINITION
    assert result["definition_method"] == "pattern"
    assert result["generated_definition"] == llm.synthesis
    assert result["pmids"] == ["11", "22"]
    assert result["pmid_to_pmcid"] == {"11": "PMC111"}
    assert result["embedding_model"] == "fake-embedding"
    assert result["upstream"] == UPSTREAM == "psych-defgen 0.0.1b1 (a625984)"
    assert result["counts"] == {
        "pubmed_articles": 2,
        "pmc_full_texts": 1,
        "abstract_fallbacks": 1,
        "definition_candidates": 3,
        "retrieval_items": 5,
    }

    top = result["evidence"][0]
    assert top["source"] == "PMC full text"
    assert top["pmcid"] == "PMC111"
    assert top["url"] == "https://pmc.ncbi.nlm.nih.gov/articles/PMC111/"
    assert top["pubmed_url"] == "https://pubmed.ncbi.nlm.nih.gov/11/"
    assert top["authors"] == ["Ada Lovelace"]
    assert top["context_text"] == top["text"]
    assert isinstance(top["score"], float)
    scores = [item["score"] for item in result["evidence"]]
    assert scores == sorted(scores, reverse=True)

    # The pattern found a definition: only the synthesis reached the model.
    assert [call["json_mode"] for call in llm.calls] == [False]
    assert llm.calls[0]["model"] == MODEL
    assert result["llm_usage"]["selection"] is None
    json.dumps(result)  # the caller stores it as JSON


def test_abstract_fallback_when_a_full_text_lacks_the_term():
    literature = FakeLiterature(
        pmids=["11", "22"],
        pmc={"11": "PMC111"},
        full_texts={"PMC111": ("Off topic", passage("Sleep and diet."))},
        abstracts=[
            abstract_record("11", passage(f"{DEFINITION} [s=0.8]")),
            abstract_record("22", passage("Loneliness rose in winter [s=0.2].")),
        ],
    )

    result = define(service(literature))

    assert ("abstracts", ["11", "22"]) in literature.calls
    assert result["counts"]["pmc_full_texts"] == 0
    assert result["counts"]["abstract_fallbacks"] == 2
    assert {item["source"] for item in result["evidence"]} == {"PubMed abstract"}
    assert result["evidence"][0]["authors"] == ["Doe J"]
    assert result["evidence"][0]["pmcid"] is None
    assert result["definition"] == DEFINITION


def test_no_pubmed_hits_gives_the_no_definition_sentence_without_model_calls():
    literature = FakeLiterature(pmids=[])
    llm = FakeLanguageModel()
    embedder = FakeEmbedder()

    result = define(service(literature, llm, embedder))

    assert [call[0] for call in literature.calls] == ["search"]
    assert llm.calls == [] and embedder.calls == []
    assert result["definition"] == NO_DEFINITION_FOUND
    assert result["definition_method"] is None
    assert result["generated_definition"] is None
    assert result["evidence"] == [] and result["pmids"] == []
    assert result["llm_usage"] == {
        "provider": "openrouter",
        "model": MODEL,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "selection": None,
        "synthesis": None,
    }
    assert set(result["counts"].values()) == {0}


def no_candidate_literature():
    return FakeLiterature(
        pmids=["33"],
        abstracts=[
            abstract_record(
                "33", passage("Loneliness was examined as a predictor [s=0.5].")
            )
        ],
    )


def test_no_candidates_gives_upstreams_no_definition_sentence():
    llm = FakeLanguageModel(selection='{"definition": null}')

    result = define(service(no_candidate_literature(), llm))

    assert [call["json_mode"] for call in llm.calls] == [True, False]
    assert result["definition"] == NO_DEFINITION_FOUND
    assert result["definition_method"] is None
    assert result["llm_usage"]["selection"]["input_tokens"] == 10


def test_llm_selection_keeps_only_text_found_in_the_evidence():
    quoted = "Loneliness was examined as a predictor [s=0.5]."
    llm = FakeLanguageModel(selection=json.dumps({"definition": quoted}))

    result = define(service(no_candidate_literature(), llm))

    assert result["definition"] == quoted
    assert result["definition_method"] == "llm_selection"

    invented = FakeLanguageModel(
        selection=json.dumps({"definition": "Loneliness is invented here."})
    )
    result = define(service(no_candidate_literature(), invented))
    assert result["definition"] == NO_DEFINITION_FOUND


def test_insufficient_evidence_gives_a_null_generated_definition():
    llm = FakeLanguageModel(synthesis=INSUFFICIENT_EVIDENCE)

    result = define(service(full_text_literature(), llm))

    assert result["generated_definition"] is None
    assert result["llm_usage"]["synthesis"]["output_tokens"] == 20


def test_a_failing_full_text_is_skipped_and_falls_back_to_its_abstract():
    literature = full_text_literature()
    literature.pmc["22"] = "PMC222"
    literature.full_texts["PMC222"] = Unavailable("NCBI full text failed.")

    result = define(service(literature))

    assert ("full_text", "PMC222") in literature.calls
    assert result["counts"]["pmc_full_texts"] == 1
    assert ("abstracts", ["22"]) in literature.calls
    assert result["definition"] == DEFINITION


def test_an_llm_error_surfaces_with_its_step_and_no_secret():
    error = LanguageModelError(
        "OpenRouter refused the request (HTTP 401); check OPENROUTER_API_KEY."
    )
    llm = FakeLanguageModel(error=error)

    with pytest.raises(DefinitionStepError) as raised:
        define(service(full_text_literature(), llm))

    assert raised.value.step == "synthesis"
    assert str(raised.value).startswith("Step 'synthesis' failed: OpenRouter")
    assert raised.value.__cause__ is None

    with pytest.raises(DefinitionStepError) as raised:
        define(service(no_candidate_literature(), llm))
    assert raised.value.step == "selection"


def test_search_and_embedding_errors_name_their_step():
    literature = FakeLiterature(search_error=Unavailable("NCBI search timed out."))
    with pytest.raises(DefinitionStepError) as raised:
        define(service(literature))
    assert raised.value.step == "search"

    embedder = FakeEmbedder(error=Unavailable("OpenAI embeddings timed out."))
    with pytest.raises(DefinitionStepError) as raised:
        define(service(full_text_literature(), embedder=embedder))
    assert raised.value.step == "ranking"


def test_stage_callback_order():
    stages = []

    define(service(full_text_literature()), on_stage=stages.append)

    assert (
        stages
        == list(STAGES)
        == [
            "apa",
            "search",
            "full_text",
            "ranking",
            "selection",
            "synthesis",
        ]
    )


def test_token_sums_cover_selection_and_synthesis():
    llm = FakeLanguageModel(selection_tokens=(10, 5), synthesis_tokens=(100, 20))

    usage = define(service(no_candidate_literature(), llm))["llm_usage"]

    assert (usage["input_tokens"], usage["output_tokens"], usage["total_tokens"]) == (
        110,
        25,
        135,
    )
    assert usage["synthesis"]["total_tokens"] == 120

    unknown = FakeLanguageModel(selection_tokens=(None, None))
    usage = define(service(no_candidate_literature(), unknown))["llm_usage"]
    assert usage["input_tokens"] is None and usage["total_tokens"] is None


def test_search_respects_max_results_and_synthesis_top_k():
    literature = full_text_literature()
    llm = FakeLanguageModel()

    result = define(service(literature, llm), max_results=1, top_k=1)

    assert literature.calls[0] == ("search", "loneliness", 1)
    assert len(result["evidence"]) == 1
    prompt = llm.calls[-1]["messages"][1]["content"]
    assert "Evidence 1:" in prompt and "Evidence 2:" not in prompt
