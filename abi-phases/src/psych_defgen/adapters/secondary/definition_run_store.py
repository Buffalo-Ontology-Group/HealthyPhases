"""Definition runs in the DuckLake dataset ``psych_defgen.definition_runs``.

One row per run. ``result`` is the definition as JSON text, read only when a
single run is fetched. Every change re-reads the row and writes it back
against the catalog snapshot it was read in, so two writers (the job and a
sensor) never overwrite each other: the later one retries on the new state.
"""

from collections.abc import Callable
from time import sleep
from typing import Any

from naas_abi_core.services.dataset.DatasetPort import (
    ColumnSpec,
    DatasetAlreadyExistsError,
    DatasetNotFoundError,
    DatasetSnapshotConflictError,
    DatasetSpec,
)

from psych_defgen.domain.errors import RunNotFoundError

NAMESPACE = "psych_defgen"
TABLE = "definition_runs"

COLUMNS = (
    ("run_id", "string"),
    ("term", "string"),
    ("max_results", "integer"),
    ("top_k", "integer"),
    ("model", "string"),
    ("embedding_model", "string"),
    ("status", "string"),
    ("stage", "string"),
    ("error", "string"),
    ("created_at", "string"),
    ("started_at", "string"),
    ("finished_at", "string"),
    ("result", "string"),
)
SUMMARY_COLUMNS = ", ".join(name for name, _ in COLUMNS if name != "result")

SPEC = DatasetSpec(
    name=TABLE,
    namespace=NAMESPACE,
    columns=tuple(ColumnSpec(name=name, type=kind) for name, kind in COLUMNS),
    primary_key=("run_id",),
)

#: Conditional writes retry on a newer catalog snapshot. Snapshots are
#: catalog-wide, so other modules' commits count too (#635). ATTEMPTS is the
#: default (about 1.4 s of waiting in all); callers that must not lose a
#: write pass more (60 attempts wait about 50 s).
ATTEMPTS = 8
MAX_PAUSE = 1.0


def literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


class DefinitionRunStore:
    def __init__(self, dataset, *, pause: Callable[[float], None] = sleep):
        self.dataset = dataset
        self.pause = pause

    def ensure(self) -> None:
        """Create the table unless it exists; safe to call on every start."""
        try:
            self.dataset.describe(TABLE, namespace=NAMESPACE)
        except DatasetNotFoundError:
            try:
                self.dataset.create(SPEC)
            except DatasetAlreadyExistsError:
                pass

    def insert(self, row: dict[str, Any]) -> None:
        self.dataset.write(TABLE, [row], namespace=NAMESPACE, mode="append")

    def get(self, run_id: str) -> dict[str, Any]:
        rows = self._query(f"SELECT * FROM {TABLE} WHERE run_id = {literal(run_id)}")
        if not rows:
            raise RunNotFoundError(run_id)
        return rows[0]

    def page(self, *, offset: int, limit: int) -> tuple[int, list[dict[str, Any]]]:
        """Runs newest first, without ``result``, and the total count."""
        # One query, so the count and the page come from the same snapshot.
        rows = self._query(
            "SELECT counts.total, page.* "
            f"FROM (SELECT COUNT(*) AS total FROM {TABLE}) counts "
            f"LEFT JOIN (SELECT {SUMMARY_COLUMNS} FROM {TABLE} "
            f"ORDER BY created_at DESC, run_id DESC "
            f"LIMIT {int(limit)} OFFSET {int(offset)}) page ON TRUE "
            "ORDER BY page.created_at DESC, page.run_id DESC"
        )
        total = int(rows[0]["total"]) if rows else 0
        return total, [
            {key: value for key, value in row.items() if key != "total"}
            for row in rows
            if row["run_id"] is not None
        ]

    def with_status(self, *statuses: str) -> list[dict[str, Any]]:
        """Runs in these statuses, oldest first, without ``result``."""
        wanted = ", ".join(literal(status) for status in statuses)
        return self._query(
            f"SELECT {SUMMARY_COLUMNS} FROM {TABLE} WHERE status IN ({wanted}) "
            "ORDER BY created_at, run_id"
        )

    def update(
        self,
        run_id: str,
        change: Callable[[dict[str, Any]], dict[str, Any] | None],
        *,
        attempts: int = ATTEMPTS,
    ) -> dict[str, Any]:
        """Apply ``change`` to the current row and write it back.

        ``change`` returns the new row, or ``None`` to leave it as it is.
        Returns the row as stored afterwards. After ``attempts`` snapshot
        conflicts, the last ``DatasetSnapshotConflictError`` is raised.
        """
        for attempt in range(attempts):
            snapshot = self.dataset.describe(TABLE, namespace=NAMESPACE).snapshot_id
            current = self.get(run_id)
            updated = change(dict(current))
            if updated is None or updated == current:
                return current
            try:
                self.dataset.write(
                    TABLE,
                    [updated],
                    namespace=NAMESPACE,
                    mode="upsert",
                    snapshot_id=snapshot,
                )
                return updated
            except DatasetSnapshotConflictError:
                if attempt == attempts - 1:
                    raise
                self.pause(min(0.05 * (attempt + 1), MAX_PAUSE))
        raise AssertionError("unreachable")  # pragma: no cover

    def _query(self, sql: str) -> list[dict[str, Any]]:
        return self.dataset.query(sql, namespace=NAMESPACE).rows
