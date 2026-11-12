# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy", "pandas", "pyarrow", "scikit-learn>=1.4", "umap-learn>=0.5.6"]
# ///
"""How well does the clustering agree with itself across seeds?

The same UMAP + HDBSCAN pipeline as ``cluster.py`` runs on the same points with
different seeds (only UMAP is random). Each pair of runs is scored on the points
both runs clustered:

- majority mapping: map every cluster of run B to the cluster of run A that
  holds most of its points; the share of points that land there (and the
  same from A to B);
- the adjusted Rand index.

    uv run scripts/term_clustering/stability.py --variant parts sentences --min-cluster-size 15 --seeds 42 1 2

Reductions are cached under ``cache/`` by ``cluster.py``, so a seed that was
already clustered costs only the HDBSCAN step. Writes ``stability_<stem>.json``.
"""

from __future__ import annotations

import argparse
import json
import sys
from itertools import combinations
from pathlib import Path

import numpy as np
from sklearn.metrics import adjusted_rand_score

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cluster import (  # noqa: E402
    DEFAULT_DIR,
    VARIANTS,
    discover,
    load,
    normalise,
    reduced,
    stem,
)


def mapped_agreement(a: np.ndarray, b: np.ndarray) -> float:
    """Map every cluster of ``b`` to its majority cluster in ``a``; share of
    ``b``'s clustered points (that ``a`` also clustered) landing on that cluster."""
    both = (a >= 0) & (b >= 0)
    if not both.any():
        return float("nan")
    hits = 0
    for label in np.unique(b[both]):
        _values, counts = np.unique(a[both & (b == label)], return_counts=True)
        hits += counts.max()
    return hits / both.sum()


def compare(a: np.ndarray, b: np.ndarray) -> dict[str, float]:
    both = (a >= 0) & (b >= 0)
    return {
        "clustered_in_both": float(both.mean()),
        "b_to_a": float(mapped_agreement(a, b)),
        "a_to_b": float(mapped_agreement(b, a)),
        "ari": float(adjusted_rand_score(a[both], b[both]))
        if both.any()
        else float("nan"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--variant", nargs="+", default=["parts"], choices=VARIANTS)
    parser.add_argument("--min-cluster-size", type=int, default=15)
    parser.add_argument("--umap-dim", type=int, default=10)
    parser.add_argument("--seeds", nargs="+", type=int, default=[42, 1, 2])
    args = parser.parse_args()

    for variant in args.variant:
        corpus = load(variant, args.dir)
        unit = normalise(corpus.vectors)
        runs: dict[int, np.ndarray] = {}
        per_seed = []
        print(f"## {variant}, min_cluster_size {args.min_cluster_size}\n")
        for seed in args.seeds:
            labels = discover(
                reduced(args.dir, corpus, unit, args.umap_dim, seed),
                args.min_cluster_size,
            ).labels_
            runs[seed] = labels
            per_seed.append(
                {
                    "seed": seed,
                    "clusters": len(set(labels.tolist()) - {-1}),
                    "noise": float((labels == -1).mean()),
                }
            )
            print(
                f"seed {seed}: {per_seed[-1]['clusters']} clusters, noise {per_seed[-1]['noise']:.0%}"
            )
        always = np.all(np.vstack(list(runs.values())) >= 0, axis=0).mean()
        print(f"clustered in every run: {always:.0%}")

        pairs = []
        print(
            "\n| run A | run B | clustered in both | B->A majority | A->B majority | ARI |"
        )
        print("|---|---|---|---|---|---|")
        for s1, s2 in combinations(args.seeds, 2):
            scores = compare(runs[s1], runs[s2])
            pairs.append({"a": s1, "b": s2} | scores)
            print(
                f"| {s1} | {s2} | {scores['clustered_in_both']:.0%} | {scores['b_to_a']:.0%} | "
                f"{scores['a_to_b']:.0%} | {scores['ari']:.2f} |"
            )
        print()
        out = (
            args.dir
            / f"stability_{stem(variant, args.min_cluster_size, args.umap_dim)}.json"
        )
        out.write_text(
            json.dumps(
                {
                    "seeds": per_seed,
                    "clustered_in_every_run": float(always),
                    "pairs": pairs,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
