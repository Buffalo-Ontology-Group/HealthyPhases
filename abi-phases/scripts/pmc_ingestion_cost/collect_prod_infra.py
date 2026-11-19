# /// script
# requires-python = ">=3.12"
# ///
"""Read-only storage and timing measurements of the production stack.

Run from ``abi-phases/`` on a machine with ``ssh phases`` access::

    uv run scripts/pmc_ingestion_cost/collect_prod_infra.py \\
        > scripts/pmc_ingestion_cost/measurements/prod_infra.json

Everything here only reads: Qdrant's collection info (GET), a SPARQL
``SELECT`` on Fuseki, ``du`` of the volumes, ``docker stats`` and two
``SELECT``s on Dagster's event log inside a ``READ ONLY`` transaction. No
password is passed or printed: Qdrant and Fuseki's query endpoint are open
on the host, and ``psql`` uses the container's local socket.
"""

from __future__ import annotations

import csv
import io
import json
import subprocess
import sys
from datetime import UTC, datetime

HOST = "phases"
COLLECTIONS = ("phases_v2_chunks", "phases_v2_extracted_items")
GRAPH_COUNT = "SELECT ?g (COUNT(*) AS ?n) WHERE { GRAPH ?g { ?s ?p ?o } } GROUP BY ?g"
STEP_SQL = """
BEGIN READ ONLY;
WITH s AS (
  SELECT run_id, step_key,
         min(timestamp) FILTER (WHERE dagster_event_type = 'STEP_START') AS t0,
         max(timestamp) FILTER (WHERE dagster_event_type IN ('STEP_SUCCESS', 'STEP_FAILURE')) AS t1
  FROM event_logs
  WHERE step_key IN ('ingest_papers_op', 'chunk_papers_op', 'run_extraction_op',
                     'project_vectors_op', 'project_graph_op')
  GROUP BY 1, 2),
m AS (
  SELECT run_id, step_key, event::json->>'user_message' AS message FROM event_logs
  WHERE step_key IN ('chunk_papers_op', 'project_vectors_op', 'project_graph_op')
    AND dagster_event_type IS NULL
    AND (event::json->>'user_message' LIKE 'papers_chunked=%'
         OR event::json->>'user_message' LIKE 'embedded=%'
         OR event::json->>'user_message' LIKE 'projected=%'))
SELECT left(s.run_id, 8) AS run, s.step_key AS step, s.t0::timestamp(0) AS started,
       round(extract(epoch FROM s.t1 - s.t0)) AS seconds, m.message
FROM s LEFT JOIN m USING (run_id, step_key)
WHERE s.t1 IS NOT NULL ORDER BY s.t0;
ROLLBACK;
"""


def ssh(command: str, stdin: str | None = None) -> str:
    result = subprocess.run(
        ["ssh", HOST, command],
        input=stdin,
        capture_output=True,
        text=True,
        check=True,
        timeout=600,
    )
    return result.stdout


def du_bytes(output: str) -> dict[str, int]:
    """``du -sb`` lines as {path: bytes}."""
    sizes = {}
    for line in output.splitlines():
        size, _, path = line.partition("\t")
        if size.strip().isdigit():
            sizes[path.strip()] = int(size)
    return sizes


def qdrant() -> dict:
    out = {}
    for name in COLLECTIONS:
        info = json.loads(ssh(f"curl -s localhost:6333/collections/{name}"))["result"]
        out[name] = {
            "points": info["points_count"],
            "segments": info["segments_count"],
            "vector_size": info["config"]["params"]["vectors"]["size"],
            "on_disk_payload": info["config"]["params"].get("on_disk_payload"),
            "hnsw": info["config"]["hnsw_config"],
            "quantization": info["config"].get("quantization_config"),
        }
    sizes = du_bytes(
        ssh(
            "docker exec abi-phases-qdrant-1 sh -c "
            "'du -sb /qdrant/storage/collections/*'"
        )
    )
    # Separately: du counts a path once per invocation, so the total asked
    # for alongside its own subdirectories would come back nearly empty.
    sizes |= du_bytes(
        ssh("docker exec abi-phases-qdrant-1 sh -c 'du -sb /qdrant/storage'")
    )
    for name in COLLECTIONS:
        out[name]["disk_bytes"] = sizes[f"/qdrant/storage/collections/{name}"]
    out["storage_bytes"] = sizes["/qdrant/storage"]
    return out


def fuseki() -> dict:
    text = ssh(
        "curl -s --max-time 300 localhost:3030/ds/query -H 'Accept: text/csv' "
        f"--data-urlencode 'query={GRAPH_COUNT}'"
    )
    graphs = {row["g"]: int(row["n"]) for row in csv.DictReader(io.StringIO(text))}
    sizes = du_bytes(
        ssh("docker exec abi-phases-fuseki-1 sh -c 'du -sb /fuseki/databases/ds'")
    )
    return {
        "triples_by_graph": graphs,
        "database_bytes": sizes["/fuseki/databases/ds"],
        "compacted": False,
        "note": "TDB2 keeps superseded blocks until compacted; fuseki-compact has never run",
    }


def storage() -> dict:
    sizes = du_bytes(ssh("cd ~/HealthyPhases/abi-phases && du -sb storage/datasets"))
    sizes |= du_bytes(
        ssh(
            "docker exec abi-phases-minio-1 sh -c 'du -sb /data/abi /data/dagster-logs'"
        )
    )
    databases = {}
    for line in ssh(
        "docker exec abi-phases-postgres-1 psql -U abi -d abi -At -F '\t' -c "
        "'SELECT datname, pg_database_size(datname) FROM pg_database'"
    ).splitlines():
        name, _, size = line.partition("\t")
        databases[name] = int(size)
    return {
        "ducklake_data_dir_bytes": sizes["storage/datasets"],
        "object_store_abi_bytes": sizes["/data/abi"],
        "postgres_database_bytes": databases,
    }


def host() -> dict:
    disk = ssh("df -B1 --output=size,used,avail / | tail -1").split()
    stats = ssh(
        "docker stats --no-stream --format '{{.Name}}\t{{.MemUsage}}' | "
        "grep -E 'qdrant|fuseki|abi-1|dagster|postgres|minio'"
    )
    return {
        "instance_type": ssh(
            "TOKEN=$(curl -s -X PUT http://169.254.169.254/latest/api/token "
            "-H 'X-aws-ec2-metadata-token-ttl-seconds: 60'); "
            'curl -s -H "X-aws-ec2-metadata-token: $TOKEN" '
            "http://169.254.169.254/latest/meta-data/instance-type"
        ).strip(),
        "vcpus": int(ssh("nproc").strip()),
        "memory_bytes": int(ssh("free -b | awk '/Mem:/{print $2}'").strip()),
        "disk_bytes": {
            "size": int(disk[0]),
            "used": int(disk[1]),
            "free": int(disk[2]),
        },
        "container_memory": dict(
            line.split("\t", 1) for line in stats.splitlines() if "\t" in line
        ),
    }


def steps() -> list[dict]:
    text = ssh(
        "docker exec -i abi-phases-postgres-1 psql -U abi -d abi --csv -q", STEP_SQL
    )
    body = "\n".join(
        line for line in text.splitlines() if line not in ("BEGIN", "ROLLBACK")
    )
    return [
        {**row, "seconds": float(row["seconds"])}
        for row in csv.DictReader(io.StringIO(body))
    ]


def main() -> None:
    json.dump(
        {
            "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "host": host(),
            "qdrant": qdrant(),
            "fuseki": fuseki(),
            "storage": storage(),
            "pipeline_steps": steps(),
        },
        sys.stdout,
        indent=1,
    )


if __name__ == "__main__":
    main()
