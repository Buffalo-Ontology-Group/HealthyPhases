"""Read-only measurements of the production datasets.

Runs *inside* the API container, where DuckDB with the DuckLake extension is
installed and ``PGPASSWORD`` is already in the environment. It only reads: the
catalog is attached ``READ_ONLY`` and every statement is a ``SELECT``. Pipe it
in on stdin, never copy it under ``/app`` (a bind mount of the checkout)::

    ssh phases 'docker exec -i -e LOG_LEVEL=WARNING -w /app abi-phases-abi-1 \\
        .venv/bin/python - aggregates' < scripts/pmc_ingestion_cost/collect_prod.py \\
        > scripts/pmc_ingestion_cost/measurements/prod_datasets.json

    ssh phases 'docker exec -i -e LOG_LEVEL=WARNING -w /app abi-phases-abi-1 \\
        .venv/bin/python - sample 400 855' < scripts/pmc_ingestion_cost/collect_prod.py \\
        > storage/spikes/pmc_ingestion_cost/prod_sample.jsonl

``aggregates`` prints one JSON document of counts and distributions (no paper
text). ``sample N SEED`` prints N seeded-random chunks, plus up to N
chunks another model also answered, as JSON lines, each with
its text and every current prompt's stored answer and items per model; that
file holds paper text, so it stays under the gitignored ``storage/``.

Standard library and ``duckdb`` only: it runs on the container's interpreter.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime

import duckdb

CATALOG = "ducklake:postgres:postgresql://abi@postgres:5432/ducklake"
DATA_PATH = "storage/datasets/"
REFERENCE_MODEL = "openai/gpt-4.1-mini"

# The current version of each prompt (``phases_v2.prompts.templates.current_prompts``
# on main at the time of measurement). Older versions are left out: they are
# not what a new run would send.
CURRENT_PROMPTS = {
    "conditional_statement_06c299d1b873": "conditional_statement",
    "probabilistic_processes_023d15a2ea4a": "probabilistic_processes",
    "solitude_causes_f240dcd5f927": "solitude_causes",
    "solitude_effects_03b778eb43f1": "solitude_effects",
    "solitude_how_defd55d659be": "solitude_how",
    "solitude_what_bd3c4b6de030": "solitude_what",
    "solitude_when_dffb22ac3cdc": "solitude_when",
    "solitude_where_eadbe78f900c": "solitude_where",
}


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("LOAD ducklake")
    con.execute(f"ATTACH '{CATALOG}' AS lake (READ_ONLY, DATA_PATH '{DATA_PATH}')")
    return con


def rows(con, sql: str) -> list[dict]:
    cursor = con.execute(sql)
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, r)) for r in cursor.fetchall()]


def one(con, sql: str) -> dict:
    return rows(con, sql)[0]


def _prompt_list() -> str:
    return ", ".join(f"'{p}'" for p in CURRENT_PROMPTS)


def distribution(column: str) -> str:
    return (
        f"avg({column}) AS mean, median({column}) AS median, "
        f"quantile_cont({column}, 0.1) AS p10, quantile_cont({column}, 0.9) AS p90, "
        f"min({column}) AS min, max({column}) AS max"
    )


def aggregates(con) -> dict:
    prompts = _prompt_list()
    out: dict = {"measured_at": datetime.now(UTC).isoformat(timespec="seconds")}

    out["papers"] = one(
        con,
        f"SELECT count(*) AS n, sum(size_bytes) AS total_bytes, {distribution('size_bytes')} "
        "FROM lake.phases_v2.papers",
    )
    out["chunks_per_paper"] = one(
        con,
        f"SELECT count(*) AS papers, sum(n) AS chunks, {distribution('n')} FROM "
        "(SELECT paper_id, count(*) AS n FROM lake.phases_v2.chunks GROUP BY 1)",
    )
    out["chunk_chars"] = one(
        con, f"SELECT {distribution('length(text)')} FROM lake.phases_v2.chunks"
    )
    out["chunk_whitespace_tokens"] = one(
        con, f"SELECT {distribution('token_count')} FROM lake.phases_v2.chunks"
    )
    # A paper's text as the chunker saw it: the span its chunks cover.
    out["paper_rendered_chars"] = one(
        con,
        f"SELECT {distribution('chars')} FROM (SELECT paper_id, max(char_end) AS chars "
        "FROM lake.phases_v2.chunks GROUP BY 1)",
    )

    per_prompt = rows(
        con,
        f"""
        SELECT model_id, prompt_id,
               count(*) AS extractions,
               count(*) FILTER (status = 'succeeded') AS succeeded,
               count(*) FILTER (status = 'failed') AS failed,
               count(*) FILTER (error LIKE '%gave up after%') AS gave_up_after_retries,
               avg(item_count) FILTER (status = 'succeeded') AS items_per_chunk,
               avg(length(raw_response)) FILTER (status = 'succeeded') AS answer_chars
        FROM lake.phases_v2.extractions
        WHERE prompt_id IN ({prompts})
        GROUP BY ALL ORDER BY 1, 2
        """,
    )
    # The same, over only the chunks every model in the table has answered,
    # so models are compared on the same text.
    matched = rows(
        con,
        f"""
        WITH both_models AS (
            SELECT chunk_id, prompt_id FROM lake.phases_v2.extractions
            WHERE prompt_id IN ({prompts}) AND status = 'succeeded'
            GROUP BY ALL HAVING count(DISTINCT model_id) > 1
        )
        SELECT e.model_id, e.prompt_id, count(*) AS extractions,
               avg(e.item_count) AS items_per_chunk,
               avg(length(e.raw_response)) AS answer_chars
        FROM lake.phases_v2.extractions e JOIN both_models USING (chunk_id, prompt_id)
        WHERE e.status = 'succeeded'
        GROUP BY ALL ORDER BY 1, 2
        """,
    )
    item_chars = rows(
        con,
        f"""
        SELECT e.model_id, i.prompt_id, count(*) AS items,
               avg(length(i.text)) AS item_chars
        FROM lake.phases_v2.extracted_items i
        JOIN lake.phases_v2.extractions e USING (extraction_id)
        WHERE i.prompt_id IN ({prompts})
        GROUP BY ALL ORDER BY 1, 2
        """,
    )
    for group in (per_prompt, matched, item_chars):
        for row in group:
            row["prompt"] = CURRENT_PROMPTS[row["prompt_id"]]
    out["extractions_by_model_prompt"] = per_prompt
    out["extractions_matched_chunks"] = matched
    out["item_chars_by_model_prompt"] = item_chars

    out["extraction_runs"] = rows(
        con,
        f"""
        SELECT run_id, model_id, prompt_id, started_at, finished_at,
               succeeded, failed, skipped,
               epoch(finished_at - started_at) AS seconds
        FROM lake.phases_v2.extraction_runs
        WHERE prompt_id IN ({prompts}) AND finished_at IS NOT NULL
        ORDER BY started_at
        """,
    )
    for row in out["extraction_runs"]:
        row["prompt"] = CURRENT_PROMPTS[row["prompt_id"]]

    out["table_rows"] = {
        table: one(con, f"SELECT count(*) AS n FROM lake.phases_v2.{table}")["n"]
        for table in (
            "papers",
            "chunks",
            "extractions",
            "extracted_items",
            "probabilistic_relations",
            "projections",
        )
    }
    out["data_file_bytes"] = (
        rows(
            con,
            """
        SELECT table_name, sum(file_size_bytes) AS bytes, count(*) AS files
        FROM ducklake_table_info('lake') GROUP BY ALL ORDER BY 2 DESC
        """,
        )
        if _has_table_info(con)
        else None
    )
    return out


def _has_table_info(con) -> bool:
    try:
        con.execute("SELECT * FROM ducklake_table_info('lake') LIMIT 0")
    except duckdb.Error:
        return False
    return True


def sample(con, size: int, seed: int) -> None:
    """Seeded chunks with every current prompt's stored answers and items."""
    prompts = _prompt_list()
    chunk_ids = [
        r["chunk_id"]
        for r in rows(
            con,
            f"""
            SELECT chunk_id FROM lake.phases_v2.chunks
            ORDER BY hash(chunk_id || '{int(seed)}') LIMIT {int(size)}
            """,
        )
    ]
    random_ids = set(chunk_ids)
    # Chunks a second model also answered, so its answers can be counted too.
    chunk_ids += [
        r["chunk_id"]
        for r in rows(
            con,
            f"""
            SELECT DISTINCT chunk_id FROM lake.phases_v2.extractions
            WHERE model_id <> '{REFERENCE_MODEL}' AND prompt_id IN ({prompts})
            ORDER BY hash(chunk_id || '{int(seed)}') LIMIT {int(size)}
            """,
        )
        if r["chunk_id"] not in random_ids
    ]
    ids = ", ".join(f"'{c}'" for c in chunk_ids)
    chunks = {
        r["chunk_id"]: r
        for r in rows(
            con,
            f"SELECT chunk_id, paper_id, seq, text, token_count FROM lake.phases_v2.chunks "
            f"WHERE chunk_id IN ({ids})",
        )
    }
    answers = rows(
        con,
        f"""
        SELECT chunk_id, model_id, prompt_id, extraction_id, raw_response, item_count
        FROM lake.phases_v2.extractions
        WHERE chunk_id IN ({ids}) AND prompt_id IN ({prompts}) AND status = 'succeeded'
        """,
    )
    items = rows(
        con,
        f"SELECT extraction_id, text FROM lake.phases_v2.extracted_items "
        f"WHERE chunk_id IN ({ids}) AND prompt_id IN ({prompts})",
    )
    texts: dict[str, list[str]] = {}
    for item in items:
        texts.setdefault(item["extraction_id"], []).append(item["text"])
    by_chunk: dict[str, list[dict]] = {}
    for answer in answers:
        by_chunk.setdefault(answer["chunk_id"], []).append(
            {
                "model_id": answer["model_id"],
                "prompt": CURRENT_PROMPTS[answer["prompt_id"]],
                "answer": answer["raw_response"],
                "item_count": answer["item_count"],
                "items": texts.get(answer["extraction_id"], []),
            }
        )
    for chunk_id in chunk_ids:
        chunk = chunks[chunk_id]
        print(
            json.dumps(
                {
                    "chunk_id": chunk_id,
                    # "random": the seeded draw over every chunk; "second_model":
                    # added because another model answered it.
                    "drawn": "random" if chunk_id in random_ids else "second_model",
                    "paper_id": chunk["paper_id"],
                    "seq": chunk["seq"],
                    "text": chunk["text"],
                    "whitespace_tokens": chunk["token_count"],
                    "answers": by_chunk.get(chunk_id, []),
                },
                ensure_ascii=False,
            )
        )


def main(argv: list[str]) -> None:
    con = connect()
    mode = argv[0] if argv else "aggregates"
    if mode == "aggregates":
        print(json.dumps(aggregates(con), indent=1, default=str))
    elif mode == "sample":
        sample(con, int(argv[1]), int(argv[2]))
    else:
        raise SystemExit(f"unknown mode {mode!r}; use 'aggregates' or 'sample N SEED'")


if __name__ == "__main__":
    main(sys.argv[1:])
