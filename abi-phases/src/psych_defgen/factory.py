"""Build the definition service from the module configuration."""

from collections.abc import Callable

from naas_abi_core import logger
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


#: Every key, by the secret name it is read from when the configuration
#: leaves it unset. ``NCBI_API_KEY`` is optional.
SECRET_SETTINGS = (
    ("NCBI_EMAIL", "ncbi_email"),
    ("NCBI_API_KEY", "ncbi_api_key"),
    ("OPENROUTER_API_KEY", "openrouter_api_key"),
    ("OPENAI_API_KEY", "openai_api_key"),
)


def _value(secret: SecretStr | None) -> str:
    return secret.get_secret_value().strip() if secret is not None else ""


def with_secrets(configuration, lookup: Callable[[str], str | None]):
    """``configuration`` with each key it leaves unset or empty read by name
    through ``lookup`` (the engine's secret service).

    The module entry in config.yaml holds no ``{{ secret.NAME }}``: the
    engine's template rendering raises on a name absent from the secret
    store, which would stop the whole engine for one optional key. Here an
    absent name, or a lookup that fails, leaves the key unset, and
    ``missing_settings`` reports it.
    """
    update = {}
    for name, field in SECRET_SETTINGS:
        if _value(getattr(configuration, field, None)):
            continue
        try:
            value = lookup(name)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"psych_defgen: could not read {name} from the secret service "
                f"({type(exc).__name__}); it is treated as unset."
            )
            continue
        if isinstance(value, str) and value.strip():
            update[field] = SecretStr(value.strip())
    return configuration.model_copy(update=update) if update else configuration


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
