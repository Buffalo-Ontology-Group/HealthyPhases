"""Who owns what, and who may change it.

Prompts (a name with all its versions), collections and saved run
configurations record their owner: the Nexus user who created them. A run
records who requested it. What has no owner is *shared*: the declared
("built in") prompts, everything that existed before owners were recorded, and
whatever the ABI API key creates.

Everyone sees everything, and anyone may open, try, run and duplicate it. Only
the owner changes what they own:

- only a prompt's owner saves a new version under its name; a shared prompt is
  read-only to every user, who changes it by duplicating it;
- only a collection's owner renames or describes it, adds or removes members,
  uploads PDFs into it or archives it; a shared collection stays editable by
  every user.

The ABI API key acts as no user (:data:`SYSTEM`): it keeps every right it had
before owners existed. A request whose user cannot be told acts as
:data:`NOBODY`, which may read and change nothing.

Every refusal is a :class:`NotAllowed` carrying a message for the user, which
the HTTP API answers with a 403.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

#: Owner filters for lists: the caller's own items, or everyone's.
MINE = "mine"
EVERYONE = "all"
OWNER_FILTERS = (MINE, EVERYONE)

#: How an owner is named when the app has never seen them.
UNKNOWN_OWNER = "another user"


class NotAllowed(PermissionError):
    """The caller may not do this. The message says why and what to do instead."""


class IdentityUnavailable(RuntimeError):
    """Who is asking cannot be told right now: the sign-in service failed.

    Not the caller's doing, so not a refusal: the HTTP API answers a 503
    asking them to try again shortly.
    """

    def __init__(self, message: str = "") -> None:
        super().__init__(
            message
            or "We could not check who you are just now, so nothing was done. "
            "Try again in a few moments."
        )


@dataclass(frozen=True)
class Actor:
    """Who is asking: a Nexus user, the ABI API key, or nobody known."""

    #: The Nexus user id: stable, and what owners and requesters record.
    user_id: str | None = None
    #: How Nexus names the user: their name, else their email, else their id.
    name: str = ""
    email: str = ""
    #: The ABI API key: acts as no user and keeps every right.
    system: bool = False

    @classmethod
    def user(cls, user_id: str, name: str = "", email: str = "") -> Actor:
        if not user_id:
            raise ValueError("A user needs an id.")
        return cls(
            user_id=user_id,
            name=display_name(name, email, user_id),
            email=email or "",
        )

    @property
    def known(self) -> bool:
        """A user, or the API key: someone whose writes can be attributed."""
        return self.system or bool(self.user_id)

    @property
    def owner_id(self) -> str | None:
        """What a creation by this caller records as its owner (None: shared)."""
        return self.user_id

    def identities(self) -> set[str | None]:
        """The values a record names this caller by.

        Runs queued by the Assistant before owners were recorded name their
        requester by email, so a user's runs match their email too. The API key
        names nobody, so its runs are the ones without a requester.
        """
        if not self.user_id:
            return {None} if self.system else set()
        return {self.user_id, *([self.email] if self.email else [])}


#: The ABI API key: what it creates is shared, and it may change anything.
SYSTEM = Actor(system=True)
#: A request whose user could not be told: reads only.
NOBODY = Actor()


def display_name(name: str | None, email: str | None, user_id: str | None) -> str:
    """The way Nexus names a user: name, else email, else id."""
    return (name or "").strip() or (email or "").strip() or (user_id or "")


def check_filter(owner: str) -> str:
    if owner not in OWNER_FILTERS:
        raise ValueError("owner must be 'mine' or 'all'.")
    return owner


def is_mine(actor: Actor, owner_id: str | None) -> bool:
    """Whether ``owner_id`` is the caller. Shared items are the API key's own."""
    if actor.user_id:
        return owner_id is not None and owner_id in actor.identities()
    return actor.system and owner_id is None


def keep(
    rows: Iterable[dict[str, Any]], actor: Actor, owner: str, field: str = "owner_id"
) -> list[dict[str, Any]]:
    """``rows`` under the owner filter: all of them, or the caller's own."""
    check_filter(owner)
    rows = list(rows)
    if owner == EVERYONE:
        return rows
    return [row for row in rows if is_mine(actor, row.get(field))]


def owner_view(
    owner_id: str | None, actor: Actor, names: Mapping[str, str]
) -> dict[str, Any]:
    """What a listing says about an item's owner.

    ``owner_name`` is how Nexus names them, or ``None`` for a shared item;
    the app shows "You" for the caller's own items.
    """
    return {
        "owner_id": owner_id,
        "owner_name": None
        if owner_id is None
        else names.get(owner_id) or UNKNOWN_OWNER,
        "owned_by_you": owner_id is not None and is_mine(actor, owner_id),
        "shared": owner_id is None,
    }


def requester_view(
    requested_by: str | None, actor: Actor, names: Mapping[str, str]
) -> dict[str, Any]:
    """What a run says about who requested it.

    A user's run records their id, and runs the Assistant queued before
    requesters were recorded by id name them by email: both read as the
    person's name from ``names``. A run the API key queued keeps the label its
    script gave, which is shown as it is; so is an email nobody recorded in
    ``app_users`` yet. ``requested_by_name`` is ``None`` for a run that names
    no requester.
    """
    if requested_by is None:
        name = None
    else:
        name = names.get(requested_by) or requested_by
    return {
        "requested_by_name": name,
        "requested_by_you": requested_by is not None and is_mine(actor, requested_by),
    }


def _unknown_caller() -> str:
    return (
        "We could not tell who you are, so nothing was changed. Reload Nexus and "
        "sign in again."
    )


def prompt_refusal(
    actor: Actor, name: str, owner_id: str | None, owner_name: str | None
) -> str | None:
    """Why the caller may not save a version under the existing name ``name``.

    ``None`` when they may: they own it, or they are the API key.
    """
    if actor.system:
        return None
    if not actor.user_id:
        return _unknown_caller()
    if owner_id is None:
        return (
            f"“{name}” is a shared prompt, and shared prompts are read-only. "
            "Use Duplicate prompt to get your own copy, then change that."
        )
    if not is_mine(actor, owner_id):
        return (
            f"The prompt “{name}” belongs to {owner_name or UNKNOWN_OWNER}, and only "
            "its owner saves new versions of it. Choose another name, or use "
            "Duplicate prompt to get your own copy."
        )
    return None


def collection_refusal(
    actor: Actor, name: str, owner_id: str | None, owner_name: str | None
) -> str | None:
    """Why the caller may not change the collection ``name``.

    ``None`` when they may: they own it, it is shared, or they are the API key.
    """
    if actor.system:
        return None
    if not actor.user_id:
        return _unknown_caller()
    if owner_id is None or is_mine(actor, owner_id):
        return None
    return (
        f"The collection “{name}” belongs to {owner_name or UNKNOWN_OWNER}, and only "
        "its owner changes it. You can still run on it, see its results and search "
        "within it. To change what it holds, create a collection of your own."
    )


def creation_refusal(actor: Actor) -> str | None:
    """Why the caller may not create anything: only when they are unknown."""
    return None if actor.known else _unknown_caller()
