"""Who a Nexus session token belongs to, as Nexus itself answers it.

The app sends the user's Nexus session token. ``require_abi_api_token``
accepts it through the validator Nexus registers, which only says yes or no.
To know who it is, this asks Nexus the way its own endpoints do
(``get_current_user_released``): decode the token, refuse a revoked one, and
read the user it names, with their name and email.

Nexus runs in the same process as the ABI API, so this needs no change
upstream. A token Nexus refuses names nobody. When Nexus cannot answer at all
(its database is down, it fails, or it is missing from the API process), that
is :class:`~phases_v2.ownership.IdentityUnavailable`: the user is told to try
again shortly rather than to sign in again, which would not help.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException

from phases_v2.ownership import Actor, IdentityUnavailable

logger = logging.getLogger(__name__)


async def nexus_user(token: str) -> Actor | None:
    """The user ``token`` belongs to, or ``None`` when Nexus refuses it.

    Raises :class:`IdentityUnavailable` when Nexus cannot say.
    """
    try:
        from naas_abi.apps.nexus.apps.api.app.services.auth.adapters.primary.auth__primary_adapter__dependencies import (
            get_current_user_released,
        )
    except ImportError as missing:
        logger.error(
            "Nexus cannot be imported in the API process, so no user can be "
            "identified and every change is refused. Check that the naas_abi "
            "version installed still provides get_current_user_released.",
            exc_info=True,
        )
        raise IdentityUnavailable() from missing
    try:
        user = await get_current_user_released(token)
    except HTTPException as refused:
        if refused.status_code < 500:
            return None
        logger.error(
            "Nexus answered %s while identifying a session token",
            refused.status_code,
        )
        raise IdentityUnavailable() from refused
    except Exception as failed:
        logger.exception("Nexus could not say who a session token belongs to")
        raise IdentityUnavailable() from failed
    return Actor.user(str(user.id), user.name or "", user.email or "")
