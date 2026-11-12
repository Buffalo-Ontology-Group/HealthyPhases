# /// script
# requires-python = ">=3.12"
# dependencies = ["openai>=1.40", "tiktoken>=0.7", "numpy", "pandas", "pyarrow"]
# ///
"""Embed the distinct antecedents and consequents of the selected conditionals.

Same model and dimension as the corpus (``text-embedding-3-large`` at 3072), so
a half can later be compared with whole sentences or with ontology labels.

    uv run scripts/term_clustering/embed_parts.py --estimate      # count tokens, no call
    OPENAI_API_KEY=... uv run scripts/term_clustering/embed_parts.py

Reads ``conditionals.parquet``; writes ``parts.parquet`` (one row per distinct
half: key, display text, how often it is an antecedent and a consequent, and in
how many papers) and ``parts.vectors.npy`` with rows aligned. Re-running embeds
only new halves; ``--reuse DIR`` also takes vectors from another selection's
directory. Before any call the exact token count and its price are printed, and
the run stops if the price is above ``--max-cost``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

DEFAULT_DIR = Path(__file__).resolve().parents[2] / "storage/spikes/term_clustering"
MODEL = "text-embedding-3-large"
DIMENSION = 3072
# USD per million input tokens for MODEL.
PRICE_PER_MILLION = 0.13
BATCH = 256


def distinct_parts(conditionals: pd.DataFrame) -> pd.DataFrame:
    ante = conditionals[["antecedent_key", "antecedent", "paper_id"]].rename(
        columns={"antecedent_key": "key", "antecedent": "text"}
    )
    cons = conditionals[["consequent_key", "consequent", "paper_id"]].rename(
        columns={"consequent_key": "key", "consequent": "text"}
    )
    ante_n = ante.groupby("key").size().rename("as_antecedent")
    cons_n = cons.groupby("key").size().rename("as_consequent")
    both = pd.concat([ante, cons])
    papers = both.groupby("key")["paper_id"].nunique().rename("papers")
    # One display text per key: the first spelling seen is as good as any.
    text = both.drop_duplicates("key").set_index("key")["text"]
    parts = pd.concat([text, ante_n, cons_n, papers], axis=1).fillna(
        {"as_antecedent": 0, "as_consequent": 0}
    )
    parts[["as_antecedent", "as_consequent"]] = parts[
        ["as_antecedent", "as_consequent"]
    ].astype(int)
    return (
        parts.rename_axis("key").reset_index().sort_values("key").reset_index(drop=True)
    )


def count_tokens(texts: list[str]) -> int:
    import tiktoken

    encoding = tiktoken.encoding_for_model(MODEL)
    return sum(
        len(tokens) for tokens in encoding.encode_batch(texts, disallowed_special=())
    )


def embed(texts: list[str]) -> np.ndarray:
    from openai import OpenAI

    client = OpenAI(max_retries=5)
    out = np.empty((len(texts), DIMENSION), dtype=np.float32)
    for start in range(0, len(texts), BATCH):
        batch = texts[start : start + BATCH]
        response = client.embeddings.create(
            model=MODEL, input=batch, dimensions=DIMENSION
        )
        for i, item in enumerate(response.data):
            out[start + i] = item.embedding
        print(
            f"\rembedded {min(start + BATCH, len(texts))}/{len(texts)}",
            end="",
            file=sys.stderr,
            flush=True,
        )
    print(file=sys.stderr)
    return out


def load_known(directory: Path) -> dict[str, np.ndarray]:
    parts_path, vec_path = directory / "parts.parquet", directory / "parts.vectors.npy"
    if not (parts_path.exists() and vec_path.exists()):
        return {}
    return dict(zip(pd.read_parquet(parts_path)["key"], np.load(vec_path)))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument(
        "--reuse",
        type=Path,
        nargs="*",
        default=[],
        help="other directories with parts vectors",
    )
    parser.add_argument(
        "--estimate", action="store_true", help="count tokens and price, then stop"
    )
    parser.add_argument(
        "--max-cost",
        type=float,
        default=20.0,
        help="USD; stop before embedding above this",
    )
    args = parser.parse_args()

    parts = distinct_parts(pd.read_parquet(args.dir / "conditionals.parquet"))
    print(f"distinct halves: {len(parts)}")

    known: dict[str, np.ndarray] = {}
    for directory in [*args.reuse, args.dir]:
        known.update(load_known(directory))
    missing = parts.loc[~parts["key"].isin(known)]
    tokens = count_tokens(missing["text"].tolist()) if len(missing) else 0
    cost = tokens / 1e6 * PRICE_PER_MILLION
    print(f"already embedded: {len(parts) - len(missing)}, to embed: {len(missing)}")
    print(
        f"{tokens} tokens -> ${cost:.2f} at {MODEL} pricing (${PRICE_PER_MILLION}/1M)"
    )
    estimate = {
        "halves": len(parts),
        "to_embed": len(missing),
        "tokens": tokens,
        "usd": round(cost, 4),
    }

    if args.estimate:
        return
    if cost > args.max_cost:
        sys.exit(
            f"estimated ${cost:.2f} is above --max-cost {args.max_cost}; not embedding"
        )
    if len(missing):
        if not os.environ.get("OPENAI_API_KEY"):
            sys.exit("OPENAI_API_KEY is not set")
        known.update(zip(missing["key"], embed(missing["text"].tolist())))

    matrix = (
        np.vstack([known[k] for k in parts["key"]])
        if len(parts)
        else np.zeros((0, DIMENSION), np.float32)
    )
    parts.to_parquet(args.dir / "parts.parquet", index=False)
    np.save(args.dir / "parts.vectors.npy", matrix)
    # Every run that embedded something is kept, so the total spent survives re-runs.
    log_path = args.dir / "embedding.json"
    log = json.loads(log_path.read_text()) if log_path.exists() else {"runs": []}
    if estimate["to_embed"]:
        log["runs"].append(
            estimate | {"model": MODEL, "reused_from": [str(p) for p in args.reuse]}
        )
    log["tokens"] = sum(run["tokens"] for run in log["runs"])
    log["usd"] = round(sum(run["usd"] for run in log["runs"]), 4)
    log_path.write_text(json.dumps(log, indent=2))
    print(f"wrote parts.parquet and parts.vectors.npy ({matrix.shape})")


if __name__ == "__main__":
    main()
