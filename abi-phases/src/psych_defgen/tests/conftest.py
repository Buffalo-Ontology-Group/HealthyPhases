import itertools

import pytest
from naas_abi_core.engine.engine_configuration.EngineConfiguration import GlobalConfig
from naas_abi_core.services.dataset.adapters.secondary.DatasetSecondaryAdapterDuckLake import (
    DatasetSecondaryAdapterDuckLake,
)
from naas_abi_core.services.dataset.DatasetService import DatasetService

from psych_defgen import PsychDefgenConfiguration
from psych_defgen.adapters.secondary.definition_run_store import DefinitionRunStore
from psych_defgen.application.definition_service import STAGES
from psych_defgen.application.runs import DefinitionRuns
from psych_defgen.factory import run_settings

#: Distinctive values, so a test can check that none leaks into a response.
SECRETS = {
    "ncbi_email": "researcher-secret@example.org",
    "ncbi_api_key": "ncbi-secret-key-value",
    "openrouter_api_key": "sk-or-v1-secret-value",
    "openai_api_key": "sk-proj-secret-value",
}
MODELS = ["openai/gpt-4.1-mini", "anthropic/claude-sonnet-4"]


def configuration(**values):
    """A validated module configuration: every key set, two models offered."""
    settings = {**SECRETS, "llm_models": MODELS, **values}
    return PsychDefgenConfiguration(
        global_config=GlobalConfig.model_construct(), **settings
    )


class Clock:
    """2026-11-16T10:00:00Z, then one second later on every call."""

    def __init__(self):
        self.seconds = itertools.count()

    def __call__(self):
        second = next(self.seconds)
        return f"2026-11-16T10:{second // 60:02d}:{second % 60:02d}Z"


def ids():
    counter = itertools.count(1)
    return lambda: f"run{next(counter):04d}"


@pytest.fixture
def dataset(tmp_path):
    return DatasetService(
        DatasetSecondaryAdapterDuckLake(
            catalog=f"sqlite:{tmp_path}/catalog.sqlite",
            data_path=f"{tmp_path}/data/",
        )
    )


@pytest.fixture
def caplog_messages():
    """Messages the module logs during a test, tracebacks included."""
    from naas_abi_core import logger

    messages = []
    handler = logger.add(lambda message: messages.append(str(message)))
    yield messages
    logger.remove(handler)


@pytest.fixture
def store(dataset):
    store = DefinitionRunStore(dataset, pause=lambda _: None)
    store.ensure()
    return store


def make_runs(store, **config):
    return DefinitionRuns(
        store, run_settings(configuration(**config)), clock=Clock(), new_id=ids()
    )


@pytest.fixture
def runs(store):
    return make_runs(store)


RESULT = {
    "term": "loneliness",
    "apa_entry": {"term": "loneliness", "status": "found"},
    "definition": "Loneliness is defined as a distressing feeling.",
    "definition_method": "pattern",
    "generated_definition": "Loneliness is the perceived lack of connection.",
    "llm_usage": None,
    "evidence": [{"text": "Loneliness is defined as…", "score": 0.61}],
    "pmids": ["1"],
    "pmid_to_pmcid": {"1": "PMC1"},
    "embedding_model": "text-embedding-3-small",
    "counts": {"pubmed_articles": 1},
    "upstream": "psych-defgen 0.0.1b1 (a625984)",
}


class FakeDefiner:
    """``define()`` that reports every stage, then returns ``RESULT`` or
    raises ``error`` when it reaches ``fail_at``."""

    def __init__(self, *, error=None, fail_at="synthesis", seen=None):
        self.error = error
        self.fail_at = fail_at
        self.calls = []
        self.seen = seen  # called at each stage, to look at the stored run

    def define(self, term, *, max_results, top_k, model, on_stage=None):
        self.calls.append((term, max_results, top_k, model))
        for stage in STAGES:
            on_stage(stage)
            if self.seen:
                self.seen(stage)
            if self.error is not None and stage == self.fail_at:
                raise self.error
        return {**RESULT, "term": term}
