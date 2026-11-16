"""Errors raised while producing a definition.

Messages are shown to users and stored with a run, so they never carry a
secret value: adapters build them from status codes and their own wording,
never from a provider's response body or a request URL.
"""


class ServiceError(Exception):
    """A hosted service (NCBI, OpenAI, OpenRouter) could not be used."""


class DefinitionStepError(Exception):
    """A pipeline step failed; ``step`` is one of the stage names."""

    def __init__(self, step: str, message: str):
        super().__init__(f"Step '{step}' failed: {message}")
        self.step = step
        self.reason = message


class NotConfiguredError(Exception):
    """Required settings are missing; ``missing`` lists them by name."""

    def __init__(self, missing: list[str]):
        super().__init__(
            "psych_defgen is not configured: set "
            + ", ".join(missing)
            + " in the deployment's environment."
        )
        self.missing = list(missing)
