"""Who owns what, and who may change it: the rules on their own."""

import pytest

from phases_v2.ownership import (
    EVERYONE,
    MINE,
    NOBODY,
    SYSTEM,
    Actor,
    collection_refusal,
    creation_refusal,
    display_name,
    keep,
    owner_view,
    prompt_refusal,
)

ADA = Actor.user("user-ada", "Ada Example", "ada@example.org")
BEA = Actor.user("user-bea", "", "bea@example.org")
NAMES = {"user-ada": "Ada Example", "user-bea": "bea@example.org"}


def test_a_user_is_named_the_way_nexus_names_them():
    assert display_name("Ada Example", "ada@example.org", "u1") == "Ada Example"
    assert display_name("  ", "bea@example.org", "u2") == "bea@example.org"
    assert display_name(None, None, "u3") == "u3"
    assert BEA.name == "bea@example.org"
    with pytest.raises(ValueError):
        Actor.user("")


def test_mine_keeps_the_callers_own_and_everyone_keeps_all():
    rows = [{"owner_id": "user-ada"}, {"owner_id": "user-bea"}, {"owner_id": None}]

    assert keep(rows, ADA, MINE) == [{"owner_id": "user-ada"}]
    assert keep(rows, ADA, EVERYONE) == rows
    # The API key creates shared items, so those are its own.
    assert keep(rows, SYSTEM, MINE) == [{"owner_id": None}]
    assert keep(rows, NOBODY, MINE) == []
    with pytest.raises(ValueError):
        keep(rows, ADA, "theirs")


def test_runs_queued_by_email_before_owners_count_as_the_users():
    assert keep([{"requested_by": "ada@example.org"}], ADA, MINE, "requested_by")


def test_an_owner_is_you_a_name_or_nobody():
    assert owner_view("user-ada", ADA, NAMES)["owned_by_you"] is True
    assert owner_view("user-ada", BEA, NAMES)["owner_name"] == "Ada Example"
    assert owner_view("user-gone", BEA, NAMES)["owner_name"] == "another user"
    shared = owner_view(None, ADA, NAMES)
    assert (shared["shared"], shared["owner_name"], shared["owned_by_you"]) == (
        True,
        None,
        False,
    )


def test_only_the_owner_saves_a_version_and_shared_prompts_are_read_only():
    assert prompt_refusal(ADA, "Claims", "user-ada", "Ada Example") is None
    theirs = prompt_refusal(BEA, "Claims", "user-ada", "Ada Example")
    assert "belongs to Ada Example" in theirs and "Duplicate prompt" in theirs
    assert "Choose another name" in theirs
    shared = prompt_refusal(ADA, "Claims", None, None)
    assert "shared prompt" in shared and "read-only" in shared
    assert prompt_refusal(SYSTEM, "Claims", None, None) is None
    assert prompt_refusal(SYSTEM, "Claims", "user-ada", "Ada Example") is None
    assert "could not tell who you are" in prompt_refusal(NOBODY, "Claims", None, None)


def test_only_the_owner_changes_a_collection_and_shared_ones_stay_open():
    assert collection_refusal(ADA, "Sleep", "user-ada", "Ada Example") is None
    assert collection_refusal(BEA, "Sleep", None, None) is None
    theirs = collection_refusal(BEA, "Sleep", "user-ada", "Ada Example")
    assert "belongs to Ada Example" in theirs and "run on it" in theirs
    assert collection_refusal(SYSTEM, "Sleep", "user-ada", "Ada Example") is None
    assert collection_refusal(NOBODY, "Sleep", None, None)


def test_anyone_known_creates():
    assert creation_refusal(ADA) is None
    assert creation_refusal(SYSTEM) is None
    assert creation_refusal(NOBODY)
