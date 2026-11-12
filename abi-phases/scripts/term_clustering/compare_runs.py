# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy", "pandas", "pyarrow", "scikit-learn>=1.4"]
# ///
"""Which terms of an earlier run held in a later one, and which are new?

Both runs are selection directories that went through ``embed_parts.py``,
``cluster.py`` and ``hierarchy.py`` (``parts`` variant). Terms are the merged
clusters. Every term of each run is matched to the other run's term whose
centroid is closest (cosine similarity, in the original embedding space; both
runs use the same embedding model, so their spaces are the same):

- an earlier term *held* when its best match is at or above ``--held``, is
  *near* down to ``--near``, and is *not found* below that; a near term whose
  match is also the best match of other earlier terms went *into a broader
  term* (the later run, at a coarser setting, joined several earlier terms);
- a later term is *new* when no earlier term is at or above ``--near``.

Where both runs contain the same half (same normalised text), the share of an
earlier term's halves that sit in its matched term is reported too.

    uv run scripts/term_clustering/compare_runs.py --earlier storage/spikes/term_clustering/baseline26 \\
        --earlier-mcs 15 --later storage/spikes/term_clustering --later-mcs 40

Writes ``compare_<earlier stem>_vs_<later stem>.json`` into the later directory.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cluster import class_tfidf, handle, load, normalise, stem  # noqa: E402
from hierarchy import centroids  # noqa: E402

HELD, NEAR, BROADER, NOT_FOUND, NEW = (
    "held",
    "near",
    "into a broader term",
    "not found",
    "new",
)


def classify(similarity: float, held: float, near: float) -> str:
    return HELD if similarity >= held else NEAR if similarity >= near else NOT_FOUND


def with_fan_in(
    statuses: list[str], matches: list[int | None]
) -> tuple[list[str], list[int]]:
    """How many earlier terms share each one's match; a shared near match is a broader term."""
    shared = pd.Series([m for m in matches if m is not None]).value_counts().to_dict()
    fan_in = [int(shared.get(m, 0)) if m is not None else 0 for m in matches]
    return [
        BROADER if s == NEAR and f >= 2 else s for s, f in zip(statuses, fan_in)
    ], fan_in


def best_matches(A: np.ndarray, B: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """For each row of ``A``, the index of the most similar row of ``B`` and that similarity."""
    if len(A) == 0 or len(B) == 0:
        return np.full(len(A), -1), np.zeros(len(A))
    sims = A @ B.T
    best = sims.argmax(axis=1)
    return best, sims[np.arange(len(A)), best]


def shared_overlap(
    earlier: pd.DataFrame, later: pd.DataFrame, match: dict[int, int]
) -> dict[int, float | None]:
    """Share of each earlier term's halves, among those the later run clustered, that sit in its match."""
    joined = earlier.merge(later, on="id", suffixes=("_a", "_b"))
    joined = joined[(joined["term_a"] >= 0) & (joined["term_b"] >= 0)]
    out: dict[int, float | None] = {}
    for term, target in match.items():
        rows = joined[joined["term_a"] == term]
        out[term] = float((rows["term_b"] == target).mean()) if len(rows) else None
    return out


def run_terms(directory: Path, mcs: int, umap_dim: int):
    corpus = load("parts", directory)
    unit = normalise(corpus.vectors)
    terms = pd.read_parquet(
        directory / f"terms_{stem('parts', mcs, umap_dim)}.parquet"
    ).set_index("id")
    labels = terms.loc[corpus.ids, "term"].to_numpy()
    ids, C = centroids(unit, labels)
    keywords = class_tfidf(corpus.texts, labels)
    table = pd.DataFrame({"id": corpus.ids, "term": labels})
    papers = (
        pd.read_parquet(directory / "conditionals.parquet")
        .pipe(lambda c: pd.concat([c[["antecedent_key", "paper_id"]].rename(columns={"antecedent_key": "id"}),
                                   c[["consequent_key", "paper_id"]].rename(columns={"consequent_key": "id"})]))
        .merge(table, on="id")
        .query("term >= 0")
        .groupby("term")["paper_id"].nunique()
    )  # fmt: skip
    sizes = table[table["term"] >= 0].groupby("term").size()
    return ids, C, keywords, table, papers, sizes


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--earlier", type=Path, required=True)
    parser.add_argument("--earlier-mcs", type=int, required=True)
    parser.add_argument("--later", type=Path, required=True)
    parser.add_argument("--later-mcs", type=int, required=True)
    parser.add_argument("--umap-dim", type=int, default=10)
    parser.add_argument("--held", type=float, default=0.90)
    parser.add_argument("--near", type=float, default=0.80)
    args = parser.parse_args()

    a_ids, A, a_kw, a_table, a_papers, a_sizes = run_terms(
        args.earlier, args.earlier_mcs, args.umap_dim
    )
    b_ids, B, b_kw, b_table, b_papers, b_sizes = run_terms(
        args.later, args.later_mcs, args.umap_dim
    )

    a_best, a_sim = best_matches(A, B)
    b_best, b_sim = best_matches(B, A)
    match = {a_ids[i]: b_ids[j] for i, j in enumerate(a_best) if j >= 0}
    overlap = shared_overlap(a_table, b_table, match)

    earlier = [
        {
            "term": t, "handle": handle(a_kw.get(t, [])), "size": int(a_sizes.get(t, 0)),
            "papers": int(a_papers.get(t, 0)), "match": int(b_ids[a_best[i]]) if a_best[i] >= 0 else None,
            "match_handle": handle(b_kw.get(b_ids[a_best[i]], [])) if a_best[i] >= 0 else "",
            "match_papers": int(b_papers.get(b_ids[a_best[i]], 0)) if a_best[i] >= 0 else 0,
            "similarity": round(float(a_sim[i]), 4), "shared_halves_in_match": overlap.get(t),
            "status": classify(float(a_sim[i]), args.held, args.near),
        }
        for i, t in enumerate(a_ids)
    ]  # fmt: skip
    statuses, fan_in = with_fan_in(
        [e["status"] for e in earlier], [e["match"] for e in earlier]
    )
    for entry, status, fan in zip(earlier, statuses, fan_in):
        entry["status"], entry["earlier_terms_sharing_match"] = status, fan
    later = [
        {
            "term": t, "handle": handle(b_kw.get(t, [])), "size": int(b_sizes.get(t, 0)),
            "papers": int(b_papers.get(t, 0)), "closest_earlier": int(a_ids[b_best[i]]) if b_best[i] >= 0 else None,
            "closest_earlier_handle": handle(a_kw.get(a_ids[b_best[i]], [])) if b_best[i] >= 0 else "",
            "similarity": round(float(b_sim[i]), 4),
            "status": NEW if float(b_sim[i]) < args.near else classify(float(b_sim[i]), args.held, args.near),
        }
        for i, t in enumerate(b_ids)
    ]  # fmt: skip
    counts = {
        "earlier_terms": len(earlier),
        "later_terms": len(later),
        "earlier": {
            s: sum(1 for e in earlier if e["status"] == s)
            for s in (HELD, BROADER, NEAR, NOT_FOUND)
        },
        "later": {
            s: sum(1 for e in later if e["status"] == s) for s in (HELD, NEAR, NEW)
        },
    }
    out = args.later / (
        f"compare_{stem('parts', args.earlier_mcs, args.umap_dim)}_vs_{stem('parts', args.later_mcs, args.umap_dim)}.json"
    )
    out.write_text(
        json.dumps(
            {
                "held": args.held,
                "near": args.near,
                "counts": counts,
                "earlier": earlier,
                "later": later,
            },
            indent=2,
        )
    )
    print(json.dumps(counts, indent=2))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
