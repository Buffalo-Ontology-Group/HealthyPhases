"""Who a session token belongs to: a user, nobody, or "Nexus cannot say"."""

from __future__ import annotations

import asyncio
import logging
import sys
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import HTTPException

from phases_v2.app.adapters.secondary.NexusUsers import nexus_user
from phases_v2.ownership import Actor, IdentityUnavailable

NEXUS = (
    "naas_abi.apps.nexus.apps.api.app.services.auth.adapters.primary."
    "auth__primary_adapter__dependencies"
)


def nexus_answering(answer):
    """Nexus's dependencies module, whose lookup returns or raises ``answer``."""
    module = ModuleType(NEXUS)

    async def get_current_user_released(token):
        if isinstance(answer, BaseException):
            raise answer
        return answer

    module.get_current_user_released = get_current_user_released
    return module


def identify(monkeypatch, answer):
    monkeypatch.setitem(sys.modules, NEXUS, nexus_answering(answer))
    return asyncio.run(nexus_user("a-session"))


def test_a_user_nexus_knows_is_that_user(monkeypatch):
    user = SimpleNamespace(id=7, name="Ada Example", email="ada@example.org")
    assert identify(monkeypatch, user) == Actor.user(
        "7", "Ada Example", "ada@example.org"
    )


@pytest.mark.parametrize("status", [401, 403])
def test_a_token_nexus_refuses_names_nobody(monkeypatch, status):
    assert identify(monkeypatch, HTTPException(status_code=status)) is None


@pytest.mark.parametrize(
    "failure",
    [HTTPException(status_code=500), RuntimeError("database is down")],
)
def test_when_nexus_fails_it_cannot_say(monkeypatch, failure, caplog):
    with caplog.at_level(logging.ERROR), pytest.raises(IdentityUnavailable) as raised:
        identify(monkeypatch, failure)
    assert "Try again" in str(raised.value)
    assert caplog.records


def test_a_missing_nexus_is_an_error_not_nobody(monkeypatch, caplog):
    # A None entry makes the import fail, as after an upgrade that moved it.
    monkeypatch.setitem(sys.modules, NEXUS, None)
    with caplog.at_level(logging.ERROR), pytest.raises(IdentityUnavailable):
        asyncio.run(nexus_user("a-session"))
    assert [r.levelno for r in caplog.records] == [logging.ERROR]
    assert "cannot be imported" in caplog.text
