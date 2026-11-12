# /// script
# requires-python = ">=3.12"
# dependencies = ["qdrant-client>=1.10", "numpy", "pyarrow"]
# ///
"""Copy extracted-item vectors and payloads out of Qdrant into local files.

Writes, per collection, ``<name>.vectors.npy`` (float32, one row per point) and
``<name>.meta.parquet`` (the flat payload plus the item text), rows aligned.
Only reads: it scrolls the collection and writes nothing back.

    QDRANT_HOST=localhost QDRANT_PORT=6333 uv run scripts/term_clustering/dump_vectors.py

The address comes from ``--host``/``--port`` or ``QDRANT_HOST``/``QDRANT_PORT``
(``QDRANT_API_KEY`` if the server needs one). There is no default host: point it
at a tunnel or at the store you mean to read. By default only the conditional
statements are copied, every version and every model, so the selection step
can report what it leaves out; ``--prompt-names all`` copies everything.

Idempotent: an existing dump is skipped unless ``--force`` is given.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from qdrant_client import QdrantClient, models

COLLECTIONS = ("phases_v2_extracted_items",)
# The prompt was renamed once; both names are the same extraction.
CONDITIONAL_PROMPTS = ("conditional_statement", "logical_sentences")
DEFAULT_OUT = Path(__file__).resolve().parents[2] / "storage/spikes/term_clustering"
SCROLL_BATCH = 512


def connect(host: str, port: int, grpc_port: int, api_key: str | None) -> QdrantClient:
    # gRPC moves 3072-d vectors far cheaper than JSON; fall back if it is not exposed.
    common = {
        "host": host,
        "port": port,
        "api_key": api_key,
        "https": False,
        "timeout": 120,
    }
    try:
        client = QdrantClient(grpc_port=grpc_port, prefer_grpc=True, **common)
        client.get_collections()
        return client
    except Exception as exc:  # noqa: BLE001 - any transport failure means "try HTTP"
        print(
            f"gRPC unavailable ({exc.__class__.__name__}), using HTTP", file=sys.stderr
        )
        return QdrantClient(prefer_grpc=False, **common)


def flatten(payload: dict) -> dict:
    """The sink stores metadata at the top level and ``{"text": ...}`` under ``payload``."""
    row = {k: v for k, v in payload.items() if k != "payload"}
    inner = payload.get("payload") or {}
    row["text"] = inner.get("text", "")
    # Lists/dicts become JSON strings so the parquet schema stays flat.
    return {
        k: (json.dumps(v) if isinstance(v, (list, dict)) else v) for k, v in row.items()
    }


def prompt_filter(names: list[str]) -> models.Filter | None:
    if names == ["all"]:
        return None
    return models.Filter(
        must=[
            models.FieldCondition(key="prompt_name", match=models.MatchAny(any=names))
        ]
    )


def dump(
    client: QdrantClient, name: str, out: Path, where: models.Filter | None, force: bool
) -> None:
    vec_path, meta_path = out / f"{name}.vectors.npy", out / f"{name}.meta.parquet"
    if vec_path.exists() and meta_path.exists() and not force:
        print(f"{name}: already dumped, skipping (use --force to redo)")
        return

    total = client.count(name, count_filter=where, exact=True).count
    dimension = client.get_collection(name).config.params.vectors.size
    out.mkdir(parents=True, exist_ok=True)
    # Written straight to disk: the full collection does not fit twice in memory.
    partial = out / f"{name}.vectors.npy.partial"
    vectors = np.lib.format.open_memmap(
        partial, mode="w+", dtype=np.float32, shape=(total, dimension)
    )
    rows: list[dict] = []
    offset = None
    while True:
        points, offset = client.scroll(
            name,
            scroll_filter=where,
            limit=SCROLL_BATCH,
            offset=offset,
            with_payload=True,
            with_vectors=True,
        )
        for p in points:
            if len(rows) >= total:
                raise RuntimeError(
                    f"{name}: more points than the {total} counted; the collection is changing"
                )
            vectors[len(rows)] = np.asarray(p.vector, dtype=np.float32)
            rows.append({"point_id": str(p.id)} | flatten(p.payload or {}))
        print(f"\r{name}: {len(rows)}/{total}", end="", file=sys.stderr, flush=True)
        if offset is None:
            break
    print(file=sys.stderr)

    if len(rows) != total:
        raise RuntimeError(
            f"{name}: scrolled {len(rows)} points but the collection reports {total}"
        )

    vectors.flush()
    del vectors
    partial.replace(vec_path)
    pq.write_table(pa.Table.from_pylist(rows), meta_path)
    print(f"{name}: wrote {len(rows)} rows -> {vec_path.name}, {meta_path.name}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--host", default=os.environ.get("QDRANT_HOST"), help="default: $QDRANT_HOST"
    )
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("QDRANT_PORT") or 6333)
    )
    parser.add_argument(
        "--grpc-port", type=int, default=int(os.environ.get("QDRANT_GRPC_PORT") or 6334)
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--collections", nargs="+", default=list(COLLECTIONS))
    parser.add_argument(
        "--prompt-names",
        nargs="+",
        default=list(CONDITIONAL_PROMPTS),
        help="copy only items of these prompts; 'all' copies every point",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if not args.host:
        parser.error("no Qdrant host: pass --host or set QDRANT_HOST")

    client = connect(
        args.host, args.port, args.grpc_port, os.environ.get("QDRANT_API_KEY") or None
    )
    where = prompt_filter(args.prompt_names)
    for name in args.collections:
        # The chunks collection has no prompt; filter only the items.
        dump(client, name, args.out, where if "items" in name else None, args.force)


if __name__ == "__main__":
    main()
