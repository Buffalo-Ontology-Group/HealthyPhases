"""Readable, secret-free messages for openai SDK errors.

A provider's error body can echo part of the key (OpenAI masks all but the
last characters), so messages are built from the status code alone.
"""

import openai


def describe_openai_error(service: str, key_name: str, error: Exception) -> str:
    if isinstance(error, openai.APITimeoutError):
        return f"{service} timed out; retry later."
    if isinstance(error, openai.APIConnectionError):
        return f"Could not reach {service}; retry later."
    if isinstance(error, openai.APIStatusError):
        status = error.status_code
        if status in (401, 403):
            return f"{service} refused the request (HTTP {status}); check {key_name}."
        if status == 402:
            return f"{service} reports insufficient credits (HTTP 402)."
        if status == 404:
            return (
                f"{service} does not offer the requested model (HTTP 404); "
                "check the configured model."
            )
        if status == 429:
            return f"{service} is rate limiting requests (HTTP 429); retry later."
        if status >= 500:
            return f"{service} is unavailable (HTTP {status}); retry later."
        return f"{service} rejected the request (HTTP {status})."
    return f"{service} request failed ({type(error).__name__})."
