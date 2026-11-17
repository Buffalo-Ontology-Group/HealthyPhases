"""Build the definition service from the module configuration."""

from pydantic import SecretStr

from psych_defgen.application.definition_service import DefinitionService
from psych_defgen.application.runs import DefinitionRuns, RunSettings
from psych_defgen.domain.errors import NotConfiguredError

#: Required settings, by the environment variable that feeds each one.
REQUIRED_SETTINGS = (
    ("NCBI_EMAIL", "ncbi_email"),
    ("OPENROUTER_API_KEY", "openrouter_api_key"),
    ("OPENAI_API_KEY", "openai_api_key"),
)


def _value(secret: SecretStr | None) -> str:
    return secret.get_secret_value().strip() if secret is not None else ""


def missing_settings(configuration) -> list[str]:
    """Names of the required settings that are unset or empty."""
    return [
        name
        for name, field in REQUIRED_SETTINGS
        if not _value(getattr(configuration, field, None))
    ]


def definition_service(configuration) -> DefinitionService:
    """The service on NCBI, OpenAI embeddings, OpenRouter and the APA site.

    Raises ``NotConfiguredError`` naming the missing settings.
    """
    missing = missing_settings(configuration)
    if missing:
        raise NotConfiguredError(missing)

    from psych_defgen.adapters.secondary.apa_dictionary import ApaDictionary
    from psych_defgen.adapters.secondary.ncbi_entrez import NcbiEntrez
    from psych_defgen.adapters.secondary.openai_embeddings import OpenAIEmbedder
    from psych_defgen.adapters.secondary.openrouter_chat import OpenRouterChat

    return DefinitionService(
        literature=NcbiEntrez(
            email=_value(configuration.ncbi_email),
            api_key=_value(configuration.ncbi_api_key) or None,
        ),
        embedder=OpenAIEmbedder(
            api_key=_value(configuration.openai_api_key),
            model=configuration.embedding_model,
        ),
        language_model=OpenRouterChat(
            api_key=_value(configuration.openrouter_api_key),
        ),
        dictionary=ApaDictionary(),
    )


def run_settings(configuration) -> RunSettings:
    """What requests may ask for, and the required keys that are unset."""
    return RunSettings(
        models=tuple(configuration.llm_models),
        default_model=configuration.llm_model,
        embedding_model=configuration.embedding_model,
        missing=tuple(missing_settings(configuration)),
    )


def definition_runs(dataset, configuration) -> DefinitionRuns:
    """Definition runs stored in ``dataset`` (a ``DatasetService``)."""
    from psych_defgen.adapters.secondary.definition_run_store import (
        DefinitionRunStore,
    )

    return DefinitionRuns(DefinitionRunStore(dataset), run_settings(configuration))
