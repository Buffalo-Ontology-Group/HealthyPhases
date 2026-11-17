"""Opt-in: one small real definition through NCBI, OpenAI, OpenRouter and APA.

    PSYCH_DEFGEN_NETWORK_TESTS=1 NCBI_EMAIL=… OPENROUTER_API_KEY=… \
    OPENAI_API_KEY=… uv run pytest src/psych_defgen/tests/network_test.py

``NCBI_API_KEY`` is optional. It costs a few cents of OpenRouter and OpenAI
usage and stores nothing.
"""

import json
import os

import pytest
from naas_abi_core.engine.engine_configuration.EngineConfiguration import GlobalConfig

from psych_defgen import DEFAULT_LLM_MODEL, PsychDefgenConfiguration
from psych_defgen.application.definition_service import STAGES
from psych_defgen.factory import definition_service, missing_settings

pytestmark = pytest.mark.skipif(
    os.environ.get("PSYCH_DEFGEN_NETWORK_TESTS") != "1",
    reason="Opt-in hosted-services check (PSYCH_DEFGEN_NETWORK_TESTS=1)",
)

SECRET_ENV = {
    "ncbi_email": "NCBI_EMAIL",
    "ncbi_api_key": "NCBI_API_KEY",
    "openrouter_api_key": "OPENROUTER_API_KEY",
    "openai_api_key": "OPENAI_API_KEY",
}


def test_defines_loneliness_with_hosted_services():
    configuration = PsychDefgenConfiguration(
        global_config=GlobalConfig.model_construct(),
        **{field: os.environ.get(name, "") for field, name in SECRET_ENV.items()},
    )
    assert missing_settings(configuration) == [], "set the missing keys"
    stages = []

    result = definition_service(configuration).define(
        "loneliness",
        max_results=5,
        top_k=3,
        model=DEFAULT_LLM_MODEL,
        on_stage=stages.append,
    )

    assert stages == list(STAGES)
    assert result["term"] == "loneliness"
    assert result["apa_entry"]["status"] in {
        "found",
        "not_found",
        "unavailable",
        "request_error",
    }
    assert 0 < len(result["pmids"]) <= 5
    assert 0 < len(result["evidence"]) <= 3
    assert all(isinstance(item["score"], float) for item in result["evidence"])
    assert result["definition"]
    assert result["definition_method"] in {"pattern", "llm_selection", None}
    assert result["llm_usage"]["provider"] == "openrouter"
    assert result["llm_usage"]["synthesis"]["total_tokens"] > 0
    assert result["embedding_model"] == "text-embedding-3-small"
    assert result["counts"]["pubmed_articles"] == len(result["pmids"])
    assert result["upstream"] == "psych-defgen 0.0.1b1 (a625984)"

    stored = json.dumps(result)
    for name in SECRET_ENV.values():
        value = os.environ.get(name, "").strip()
        if value:
            assert value not in stored, f"{name} leaked into the result"

    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "apa_entry",
                    "definition",
                    "definition_method",
                    "generated_definition",
                    "llm_usage",
                    "counts",
                )
            },
            indent=2,
        )
    )
