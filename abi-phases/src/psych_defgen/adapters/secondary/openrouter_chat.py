"""The ``LanguageModel`` port on OpenRouter, through the openai SDK.

The request is upstream's ``openrouter`` branch of ``llm_provider.py``:
model and messages, ``response_format`` in JSON mode, a 300 s timeout, and
the usage shape of ``generate_llm_response(..., return_usage=True)``.
"""

import openai
from openai import OpenAI

from psych_defgen.adapters.secondary.openai_errors import describe_openai_error
from psych_defgen.application.ports import LlmResponse
from psych_defgen.domain.errors import ServiceError

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class LanguageModelError(ServiceError):
    pass


class OpenRouterChat:
    provider = "openrouter"

    def __init__(self, *, api_key: str | None = None, client=None):
        if client is None:
            if not api_key:
                # Never fall back to the SDK's OPENAI_API_KEY lookup: that key
                # must not be sent to OpenRouter.
                raise ValueError("OpenRouter needs OPENROUTER_API_KEY.")
            client = OpenAI(
                api_key=api_key,
                base_url=OPENROUTER_BASE_URL,
                timeout=300.0,
            )
        self.client = client

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        json_mode: bool = False,
    ) -> LlmResponse:
        kwargs = {
            "model": model,
            "messages": messages,
        }

        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            response = self.client.chat.completions.create(**kwargs)
        except openai.OpenAIError as error:
            raise LanguageModelError(
                describe_openai_error("OpenRouter", "OPENROUTER_API_KEY", error)
                + f" Model: {model}."
            ) from None

        if not getattr(response, "choices", None):
            raise LanguageModelError(
                f"OpenRouter returned no answer for model {model}; retry later."
            )

        content = (response.choices[0].message.content or "").strip()

        usage = response.usage
        input_tokens = usage.prompt_tokens if usage else None
        output_tokens = usage.completion_tokens if usage else None

        total_tokens = (
            input_tokens + output_tokens
            if input_tokens is not None and output_tokens is not None
            else None
        )

        return {
            "text": content,
            "usage": {
                "provider": self.provider,
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
            },
        }
