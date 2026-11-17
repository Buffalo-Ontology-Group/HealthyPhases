"""Upstream's ``pipeline.run_pipeline`` on hosted services.

The order, filters and result are upstream's. What changed:

- every network call goes through a port;
- progress is logged, and reported to ``on_stage``, instead of printed;
- nothing is written to disk: the caller stores the result;
- the result adds ``embedding_model``, ``counts`` and ``upstream``;
- search, embedding and LLM failures raise ``DefinitionStepError`` naming the
  stage. A full text that cannot be fetched is skipped, and authors that
  cannot be fetched are left as they were, as upstream does.
"""

from collections.abc import Callable
from typing import Any

from naas_abi_core import logger

from psych_defgen.application.ports import (
    Dictionary,
    Embedder,
    LanguageModel,
    Literature,
)
from psych_defgen.application.rag_retrieval import retrieve_relevant_texts
from psych_defgen.domain.chunk_text import chunk_article
from psych_defgen.domain.errors import DefinitionStepError, ServiceError
from psych_defgen.domain.evidence import (
    INSUFFICIENT_EVIDENCE,
    NO_DEFINITION_FOUND,
    create_abstract_evidence_record,
    create_pmc_evidence_record,
)
from psych_defgen.domain.extract_definition_candidates import (
    extract_definition_candidates,
)
from psych_defgen.domain.llm_definition_generator import (
    generate_definition_with_llm,
)
from psych_defgen.domain.llm_definition_selector import select_definition_with_llm
from psych_defgen.domain.models import Article
from psych_defgen.domain.select_definition_sentence import (
    select_best_definition_sentence,
)

UPSTREAM = "psych-defgen 0.0.1b1 (a625984)"

#: Stages, in the order ``on_stage`` receives them.
STAGES = ("apa", "search", "full_text", "ranking", "selection", "synthesis")

StageCallback = Callable[[str], None]


class DefinitionService:
    def __init__(
        self,
        *,
        literature: Literature,
        embedder: Embedder,
        language_model: LanguageModel,
        dictionary: Dictionary,
    ):
        self.literature = literature
        self.embedder = embedder
        self.language_model = language_model
        self.dictionary = dictionary

    def define(
        self,
        term: str,
        *,
        max_results: int,
        top_k: int,
        model: str,
        on_stage: StageCallback | None = None,
    ) -> dict[str, Any]:
        """Retrieve evidence-based definitions of a psychological construct.

        Search PubMed, retrieve available PMC full text, use PubMed abstracts
        as fallback, extract definition candidates, rank evidence by semantic
        similarity, select the best available definition sentence and
        synthesize a definition from the top-k evidence.
        """

        term = term.strip()
        if not term:
            raise ValueError("The construct term cannot be empty.")

        def stage(name: str) -> None:
            logger.info(f"psych_defgen: {name} for {term!r}")
            if on_stage is not None:
                on_stage(name)

        stage("apa")
        apa_entry = self.dictionary.lookup(term)

        stage("search")
        with _step("search"):
            pmids = [
                str(pmid)
                for pmid in self.literature.search(term, max_results=max_results)
            ]
            logger.info(f"psych_defgen: PubMed articles found: {len(pmids)}")
            pmid_to_pmcid = self.literature.pmc_ids(pmids) if pmids else {}
        logger.info(f"psych_defgen: PMC full-text articles found: {len(pmid_to_pmcid)}")

        stage("full_text")
        definition_candidates = []
        full_text_chunks = []
        processed_pmc_pmids = set()

        for pmid, pmcid in pmid_to_pmcid.items():
            try:
                title, text = self.literature.full_text(pmcid)
            except Exception as error:  # upstream skips any failed full text
                logger.warning(
                    f"psych_defgen: could not retrieve full text for "
                    f"PMID {pmid}, {pmcid}: {_describe(error)}"
                )
                continue

            if not text or term.lower() not in text.lower():
                continue

            processed_pmc_pmids.add(str(pmid))

            for candidate in extract_definition_candidates(text, term):
                definition_candidates.append(
                    create_pmc_evidence_record(candidate, title, pmid, pmcid)
                )

            article = Article(
                pmid=pmid,
                pmcid=pmcid,
                title=title,
                source="PMC full text",
                text=text,
            )

            for chunk in chunk_article(article):
                full_text_chunks.append(
                    create_pmc_evidence_record(
                        chunk.text,
                        chunk.title,
                        chunk.pmid,
                        chunk.pmcid,
                    )
                )

        fallback_pmids = [
            pmid for pmid in pmids if str(pmid) not in processed_pmc_pmids
        ]

        logger.info(
            "psych_defgen: PMC articles successfully processed: "
            f"{len(processed_pmc_pmids)}; PubMed abstracts required as "
            f"fallback: {len(fallback_pmids)}"
        )

        abstract_passages = []
        abstract_definition_candidates = []

        if fallback_pmids:
            with _step("full_text"):
                abstract_records = self.literature.abstracts(fallback_pmids)

            for record in abstract_records:
                abstract_text = record.get("abstract", "").strip()

                if not abstract_text or term.lower() not in abstract_text.lower():
                    continue

                abstract_passages.append(create_abstract_evidence_record(record))

                for candidate in extract_definition_candidates(abstract_text, term):
                    candidate_record = create_abstract_evidence_record(record)
                    candidate_record["text"] = candidate
                    abstract_definition_candidates.append(candidate_record)

        all_definition_candidates = (
            definition_candidates + abstract_definition_candidates
        )

        retrieval_items = (
            all_definition_candidates + full_text_chunks + abstract_passages
        )

        logger.info(
            f"psych_defgen: definition candidates: "
            f"{len(all_definition_candidates)}; PMC full-text chunks: "
            f"{len(full_text_chunks)}; PubMed abstract passages: "
            f"{len(abstract_passages)}; total retrieval items: "
            f"{len(retrieval_items)}"
        )

        stage("ranking")
        with _step("ranking"):
            retrieved = (
                retrieve_relevant_texts(
                    term, retrieval_items, self.embedder, top_k=top_k
                )
                if retrieval_items
                else []
            )

        logger.info(f"psych_defgen: evidence passages retrieved: {len(retrieved)}")

        stage("selection")
        # Retrieve authors only for the final selected evidence.
        selected_pmids = list(
            {str(item["pmid"]) for item in retrieved if item.get("pmid")}
        )

        authors_by_pmid = {}

        if selected_pmids:
            try:
                authors_by_pmid = self.literature.authors(selected_pmids)
            except Exception as error:  # upstream keeps the evidence's authors
                logger.warning(
                    "psych_defgen: could not retrieve article authors: "
                    f"{_describe(error)}"
                )

        for item in retrieved:
            pmid = str(item.get("pmid") or "")
            item["authors"] = authors_by_pmid.get(pmid) or item.get("authors") or []

        def generate(messages, json_mode=False):
            return self.language_model.chat(messages, model=model, json_mode=json_mode)

        def select_with_llm(term, retrieved_items):
            return select_definition_with_llm(term, retrieved_items, generate)

        with _step("selection"):
            best_definition_sentence, definition_method, selection_usage = (
                select_best_definition_sentence(
                    term,
                    all_definition_candidates + retrieved,
                    select_definition_with_llm=select_with_llm,
                )
            )

        stage("synthesis")
        generated_definition = None
        synthesis_usage = None

        if retrieved:
            with _step("synthesis"):
                generation_result = generate_definition_with_llm(
                    term=term,
                    evidence=retrieved,
                    generate=generate,
                    top_k=top_k,
                )

            generated_definition = generation_result["text"]
            synthesis_usage = generation_result["usage"]

            if generated_definition == INSUFFICIENT_EVIDENCE:
                generated_definition = None

        def sum_tokens(field):
            values = [
                usage.get(field)
                for usage in (selection_usage, synthesis_usage)
                if usage is not None
            ]

            if not values:
                return 0

            if any(value is None for value in values):
                return None

            return sum(values)

        llm_usage = {
            "provider": self.language_model.provider,
            "model": model,
            "input_tokens": sum_tokens("input_tokens"),
            "output_tokens": sum_tokens("output_tokens"),
            "total_tokens": sum_tokens("total_tokens"),
            "selection": selection_usage,
            "synthesis": synthesis_usage,
        }

        definition_from_literature = (
            best_definition_sentence.strip()
            if best_definition_sentence
            else NO_DEFINITION_FOUND
        )

        counts = {
            "pubmed_articles": len(pmids),
            "pmc_full_texts": len(processed_pmc_pmids),
            "abstract_fallbacks": len(fallback_pmids),
            "definition_candidates": len(all_definition_candidates),
            "retrieval_items": len(retrieval_items),
        }
        logger.info(f"psych_defgen: finished {term!r}: {counts}")

        return {
            "term": term,
            "apa_entry": apa_entry,
            "definition": definition_from_literature,
            "definition_method": definition_method,
            "generated_definition": generated_definition,
            "llm_usage": llm_usage,
            "evidence": retrieved,
            "pmids": pmids,
            "pmid_to_pmcid": pmid_to_pmcid,
            "embedding_model": self.embedder.model,
            "counts": counts,
            "upstream": UPSTREAM,
        }


def _describe(error: Exception) -> str:
    """An adapter's own message, or only the type of anything else."""
    if isinstance(error, ServiceError):
        return str(error)
    return type(error).__name__


class _step:
    """Turn a hosted-service failure into a ``DefinitionStepError``.

    Only ``ServiceError`` is translated: its message is built by the adapter
    and holds no secret. The original is dropped from the chain, so a logged
    traceback cannot carry a provider's response either.
    """

    def __init__(self, name: str):
        self.name = name

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None and issubclass(exc_type, ServiceError):
            raise DefinitionStepError(self.name, str(exc)) from None
        return False
