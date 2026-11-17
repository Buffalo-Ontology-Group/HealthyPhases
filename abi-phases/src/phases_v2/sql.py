"""Helpers for building the module's SQL.

The dataset service takes SQL as a string with no parameter binding, so every
value this module puts into a query goes through :func:`literal` first. Ids are
hex or slugs by construction, but caller-supplied paper ids are not, and a
query builder that is only safe for well-behaved input is not safe.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from typing import Any, TypeVar

T = TypeVar("T")

#: How many values or rows one statement names at most.
BATCH = 500


def literal(value: str) -> str:
    """A single-quoted SQL string literal."""
    return "'" + str(value).replace("'", "''") + "'"


def in_list(values: list[str]) -> str:
    """A parenthesised list for ``IN (...)``; never empty."""
    if not values:
        # An empty IN () is a syntax error, and IN (NULL) matches nothing,
        # which is the intended meaning of "restricted to no ids".
        return "(NULL)"
    return "(" + ", ".join(literal(value) for value in values) + ")"


def batches(items: Sequence[T], size: int = BATCH) -> Iterator[Sequence[T]]:
    """``items`` in runs of at most ``size``, so no statement grows unbounded."""
    for start in range(0, len(items), size):
        yield items[start : start + size]


def any_row(columns: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> str:
    """A condition matching the rows equal to one of ``rows`` on ``columns``:
    ``(a = 'x' AND b = 1) OR (...)``.

    Integers are written as numbers, ``None`` as ``IS NULL`` and everything
    else as a quoted literal. With no rows it is ``FALSE``, which matches
    nothing.
    """
    if not rows:
        return "FALSE"

    def equals(column: str, v: Any) -> str:
        if v is None:
            return f"{column} IS NULL"
        if isinstance(v, int) and not isinstance(v, bool):
            return f"{column} = {v}"
        return f"{column} = {literal(v)}"

    return " OR ".join(
        "(" + " AND ".join(equals(c, row[c]) for c in columns) + ")" for row in rows
    )
