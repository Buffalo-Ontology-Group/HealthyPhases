"""Who a Nexus session token belongs to, as Nexus itself answers it.

The app sends the user's Nexus session token. ``require_abi_api_token``
accepts it through the validator Nexus registers, which only says yes or no.
To know who it is, this asks Nexus the way its own endpoints do
(``get_current_user_released``): decode the token, refuse a revoked one, and
read the user it names, with their name and email.

Nexus runs in the same process as the ABI API, so this needs no change
upstream. Where Nexus is not loaded (scripts, tests), nobody can be
identified, and every change a user would make is refused.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException

from phases_v2.ownership import Actor

logger = logging.getLogger(__name__)


async def nexus_user(token: str) -> Actor | None:
    """The user ``token`` belongs to, or ``None`` when Nexus does not know it."""
    try:
        from naas_abi.apps.nexus.apps.api.app.services.auth.adapters.primary.auth__primary_adapter__dependencies import (
            get_current_user_released,
        )
    except ImportError:
        logger.warning("Nexus is not loaded here, so its users cannot be told apart")
        return None
    try:
        user = await get_current_user_released(token)
    except HTTPException:
        return None
    except Exception:
        logger.exception("Nexus could not say who a session token belongs to")
        return None
    return Actor.user(str(user.id), user.name or "", user.email or "")
