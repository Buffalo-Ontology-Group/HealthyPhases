"""How the app records the name Nexus gives each person who uses it."""

from phases_v2.app.app_users import FRESH_SECONDS, AppUsers
from phases_v2.ownership import NOBODY, SYSTEM, Actor

ADA = Actor.user("user-ada", "Ada Example", "ada@example.org")


class Rows:
    """``app_users`` alone, read the one way ``AppUsers`` reads it."""

    def __init__(self):
        self.users = {}
        self.writes = 0

    def write_rows(self, name, rows):
        assert name == "app_users"
        self.writes += 1
        for row in rows:
            self.users[row["user_id"]] = row

    def query(self, sql):
        assert sql == "SELECT user_id, name, email FROM app_users"
        return [
            {"user_id": r["user_id"], "name": r["name"], "email": r["email"]}
            for r in self.users.values()
        ]


def test_a_visit_writes_only_when_the_name_is_new_or_changed():
    rows = Rows()
    users = AppUsers(rows)

    users.remember(ADA)
    users.remember(ADA)
    assert rows.writes == 1
    users.remember(Actor.user("user-ada", "Ada Renamed", "ada@example.org"))
    assert rows.writes == 2
    assert users.names()["user-ada"] == "Ada Renamed"


def test_the_api_key_and_unknown_callers_are_not_recorded():
    rows = Rows()
    users = AppUsers(rows)

    users.remember(SYSTEM)
    users.remember(NOBODY)
    assert rows.writes == 0


def test_names_are_found_by_id_and_by_email():
    users = AppUsers(Rows())
    users.remember(ADA)

    assert users.names() == {
        "user-ada": "Ada Example",
        "ada@example.org": "Ada Example",
    }


def test_another_process_names_are_read_again_after_a_minute():
    rows = Rows()
    now = [0.0]
    users = AppUsers(rows, clock=lambda: now[0])
    assert users.names() == {}
    AppUsers(rows).remember(ADA)

    assert users.names() == {}
    now[0] += FRESH_SECONDS + 1
    assert users.names()["user-ada"] == "Ada Example"


def test_without_storage_nobody_is_named():
    users = AppUsers(None)
    users.remember(ADA)
    assert users.names() == {}


class Unreadable(Rows):
    def query(self, sql):
        raise RuntimeError("catalog unavailable")


def test_an_unreadable_table_names_nobody_rather_than_failing():
    users = AppUsers(Unreadable())

    assert users.names() == {}
    users.remember(ADA)  # logged, not raised
