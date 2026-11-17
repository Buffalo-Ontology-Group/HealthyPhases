import pytest
from naas_abi_core.engine.engine_configuration.EngineConfiguration import GlobalConfig
from pydantic import ValidationError

from psych_defgen import PsychDefgenConfiguration
from psych_defgen.application.definition_service import DefinitionService
from psych_defgen.domain.errors import NotConfiguredError
from psych_defgen.factory import definition_service, missing_settings, with_secrets

SECRET_VALUES = {
    "ncbi_email": "researcher@example.org",
    "openrouter_api_key": "sk-or-v1-secret-value",
    "openai_api_key": "sk-proj-secret-value",
}


def validated(**values):
    # global_config is what the engine passes every module; it plays no part here.
    return PsychDefgenConfiguration(
        global_config=GlobalConfig.model_construct(), **values
    )


def test_missing_settings_names_every_unset_or_empty_key():
    assert missing_settings(validated()) == [
        "NCBI_EMAIL",
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
    ]
    # An empty value is missing too.
    config = validated(ncbi_email="", openrouter_api_key="  ", openai_api_key="sk")
    assert missing_settings(config) == ["NCBI_EMAIL", "OPENROUTER_API_KEY"]


def test_the_ncbi_key_is_optional():
    assert missing_settings(validated(**SECRET_VALUES)) == []


def test_definition_service_names_missing_keys():
    with pytest.raises(NotConfiguredError) as raised:
        definition_service(validated(ncbi_email="researcher@example.org"))

    assert raised.value.missing == ["OPENROUTER_API_KEY", "OPENAI_API_KEY"]
    assert "OPENROUTER_API_KEY, OPENAI_API_KEY" in str(raised.value)


def test_definition_service_wires_the_hosted_adapters():
    config = validated(**SECRET_VALUES, embedding_model="text-embedding-3-large")

    service = definition_service(config)

    assert isinstance(service, DefinitionService)
    assert service.embedder.model == "text-embedding-3-large"
    assert service.language_model.provider == "openrouter"
    assert service.literature.interval == 0.37  # no NCBI key: 3 requests/s


def test_secrets_never_show_in_the_configuration_repr():
    text = repr(validated(**SECRET_VALUES)) + str(validated(**SECRET_VALUES))

    for value in SECRET_VALUES.values():
        assert value not in text


def test_llm_models_default_to_llm_model():
    config = validated(llm_model="anthropic/claude-test")
    assert config.llm_models == ["anthropic/claude-test"]
    assert validated().llm_models == ["openai/gpt-4.1-mini"]
    assert validated().embedding_model == "text-embedding-3-small"


def test_llm_model_must_be_offered():
    with pytest.raises(ValidationError, match="must be one of llm_models"):
        validated(llm_model="a/b", llm_models=["c/d"])

    config = validated(llm_model="c/d", llm_models=["a/b", "c/d"])
    assert config.llm_models == ["a/b", "c/d"]


@pytest.mark.parametrize("name", ["config.yaml", "config.local.yaml"])
def test_deployment_configs_enable_the_module(name):
    import re
    from pathlib import Path

    import yaml

    text = (Path(__file__).resolve().parents[3] / name).read_text()
    # An unset secret renders as "": the configuration must still validate.
    rendered = re.sub(r"\{\{\s*secret\.\w+\s*\}\}", "", text)
    modules = yaml.safe_load(rendered)["modules"]
    (entry,) = [m for m in modules if m.get("module") == "psych_defgen"]

    assert entry["enabled"] is True
    config = validated(**entry["config"])
    assert config.llm_model in config.llm_models
    assert missing_settings(config) == [
        "NCBI_EMAIL",
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
    ]


def lookup_in(values):
    calls = []

    def lookup(name):
        calls.append(name)
        return values.get(name)

    lookup.calls = calls
    return lookup


def test_with_secrets_reads_unset_keys_by_name():
    lookup = lookup_in(
        {
            "NCBI_EMAIL": " researcher@example.org ",
            "OPENROUTER_API_KEY": "sk-or-v1-secret-value",
            "OPENAI_API_KEY": "sk-proj-secret-value",
        }
    )

    config = with_secrets(validated(ncbi_email=""), lookup)

    assert lookup.calls == [
        "NCBI_EMAIL",
        "NCBI_API_KEY",
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
    ]
    assert config.ncbi_email.get_secret_value() == "researcher@example.org"
    assert config.ncbi_api_key is None  # absent: anonymous NCBI access
    assert missing_settings(config) == []


def test_with_secrets_keeps_values_set_in_the_configuration():
    lookup = lookup_in({"OPENAI_API_KEY": "from-the-secret-store"})

    config = with_secrets(validated(**SECRET_VALUES), lookup)

    assert config.openai_api_key.get_secret_value() == "sk-proj-secret-value"
    assert lookup.calls == ["NCBI_API_KEY"]


def test_with_secrets_leaves_absent_or_empty_names_missing():
    config = with_secrets(validated(), lookup_in({"NCBI_EMAIL": "  "}))
    assert missing_settings(config) == [
        "NCBI_EMAIL",
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
    ]


def test_a_secret_store_that_fails_leaves_the_key_unset(caplog_messages):
    def broken(name):
        raise RuntimeError(f"vault down, token sk-hidden for {name}")

    config = with_secrets(validated(), broken)

    assert missing_settings(config) == [
        "NCBI_EMAIL",
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
    ]
    joined = " ".join(caplog_messages)
    assert "could not read NCBI_EMAIL" in joined and "RuntimeError" in joined
    assert "sk-hidden" not in joined
