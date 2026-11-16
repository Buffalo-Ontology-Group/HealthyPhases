"""Evidence-based definitions of psychological constructs.

A vendored copy of psych-defgen (see UPSTREAM.md) whose model calls all go to
hosted services: NCBI E-utilities, OpenAI embeddings, OpenRouter and the APA
Dictionary website. Nothing runs a local model or a browser.

Configuration example:

    module: psych_defgen
    enabled: true
    config:
      ncbi_email: "{{ secret.NCBI_EMAIL }}"
      ncbi_api_key: "{{ secret.NCBI_API_KEY }}"
      openrouter_api_key: "{{ secret.OPENROUTER_API_KEY }}"
      openai_api_key: "{{ secret.OPENAI_API_KEY }}"
      llm_model: "openai/gpt-4.1-mini"
      llm_models: ["openai/gpt-4.1-mini"]
      embedding_model: "text-embedding-3-small"
"""

from naas_abi_core.module.Module import (
    BaseModule,
    ModuleConfiguration,
    ModuleDependencies,
)
from pydantic import Field, SecretStr, model_validator

DEFAULT_LLM_MODEL = "openai/gpt-4.1-mini"


class PsychDefgenConfiguration(ModuleConfiguration):
    #: A missing ``{{ secret.NAME }}`` renders as an empty string; an empty
    #: value counts as unset (see ``factory.missing_settings``).
    ncbi_email: SecretStr | None = None
    #: Optional: NCBI allows 10 requests/s with a key, 3 without.
    ncbi_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None
    #: For embeddings.
    openai_api_key: SecretStr | None = None
    llm_model: str = DEFAULT_LLM_MODEL
    #: The models users may choose from; ``llm_model`` must be one of them.
    llm_models: list[str] = Field(default_factory=list)
    embedding_model: str = "text-embedding-3-small"

    @model_validator(mode="after")
    def _default_model_is_offered(self) -> "PsychDefgenConfiguration":
        if not self.llm_model.strip():
            raise ValueError("psych_defgen: llm_model cannot be empty")
        if not self.embedding_model.strip():
            raise ValueError("psych_defgen: embedding_model cannot be empty")
        if not self.llm_models:
            self.llm_models = [self.llm_model]
        if self.llm_model not in self.llm_models:
            raise ValueError(
                f"psych_defgen: llm_model {self.llm_model!r} must be one of "
                f"llm_models {self.llm_models!r}"
            )
        return self


class ABIModule(BaseModule[PsychDefgenConfiguration]):
    Configuration = PsychDefgenConfiguration
    dependencies: ModuleDependencies = ModuleDependencies(modules=[], services=[])

    def on_initialized(self) -> None:
        super().on_initialized()
        from naas_abi_core import logger

        from psych_defgen.factory import missing_settings

        missing = missing_settings(self._configuration)
        if missing:
            logger.warning(
                "psych_defgen is not configured: set "
                + ", ".join(missing)
                + " to produce definitions."
            )
