"""The people who used the app, with the name Nexus gives each of them.

Items and runs record their owner or requester by Nexus user id. Lists name
those people the way Nexus does (name, else email, else id), so each person's
name is recorded in ``app_users`` when they use the app, from the app or the
Assistant, and read back from there. A name changed in Nexus shows from that
person's next visit on.

Only the API process writes here, so it keeps what it read and wrote in
memory: a visit writes only when the name or email changed, and a listing
reads the table again at most once a minute.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime

from phases_v2.ownership import Actor
from phases_v2.ports import RowStore

logger = logging.getLogger(__name__)

#: How long a read of the table is used before reading it again.
FRESH_SECONDS = 60.0


class AppUsers:
    def __init__(self, rows: RowStore | None, clock=time.monotonic):
        self._rows = rows
        self._clock = clock
        self._lock = threading.Lock()
        self._known: dict[str, tuple[str, str]] | None = None
        self._read_at = 0.0

    def remember(self, actor: Actor) -> None:
        """Record how Nexus names ``actor``, when it is a user and it changed.

        A failure is logged and not raised: naming people must never stop
        them from using the app.
        """
        if self._rows is None or not actor.user_id:
            return
        entry = (actor.name, actor.email)
        try:
            if self._read().get(actor.user_id) == entry:
                return
            self._rows.write_rows(
                "app_users",
                [
                    {
                        "user_id": actor.user_id,
                        "name": actor.name,
                        "email": actor.email,
                        "seen_at": datetime.now(UTC),
                    }
                ],
            )
        except Exception:
            logger.exception("Could not record a user's name")
            return
        with self._lock:
            if self._known is not None:
                self._known[actor.user_id] = entry

    def names(self) -> dict[str, str]:
        """Each recorded user's name, by user id and by email.

        By email too because runs the Assistant queued before owners were
        recorded name their requester by email. When the table cannot be read,
        the failure is logged and nobody is named, so lists still answer, with
        owners shown as "another user".
        """
        try:
            known = self._read()
        except Exception:
            logger.exception("Could not read the names of the app's users")
            return {}
        found: dict[str, str] = {}
        for user_id, (name, email) in known.items():
            found[user_id] = name
            if email:
                found.setdefault(email, name)
        return found

    def _read(self) -> dict[str, tuple[str, str]]:
        with self._lock:
            if (
                self._known is not None
                and self._clock() - self._read_at < FRESH_SECONDS
            ):
                return dict(self._known)
        if self._rows is None:
            return {}
        rows = self._rows.query("SELECT user_id, name, email FROM app_users")
        known = {r["user_id"]: (r["name"] or "", r["email"] or "") for r in rows}
        with self._lock:
            self._known = known
            self._read_at = self._clock()
        return dict(known)
