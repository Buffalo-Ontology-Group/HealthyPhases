# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy", "pandas", "pyarrow", "scipy", "scikit-learn>=1.4"]
# ///
"""Merge near-duplicate clusters and build a broader/narrower tree over them.

HDBSCAN finds density, not genus. Two things a term hierarchy needs are missing
from its output: near-identical clusters that should be one term ("decrease in
high-arousal affect" twice), and the broader terms that group narrower ones.
Both come from the geometry of cluster centroids:

- merges: centroid pairs at or above ``--merge-threshold`` cosine similarity,
  closed under transitivity, become one term;
- tree: average-linkage agglomeration over the merged centroids, cut at
  ``--levels`` cosine distances, gives parent nodes with two or more children.

    uv run scripts/term_clustering/hierarchy.py --variant parts --min-cluster-size 15

Reads ``assignments_<stem>.parquet`` from ``cluster.py``; writes
``merges_<stem>.parquet`` (cluster -> merged term), ``terms_<stem>.parquet``
(point -> merged term), ``hierarchy_<stem>.parquet`` and a report. These are
proposals: nothing is renamed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import pdist

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cluster import (  # noqa: E402
    DEFAULT_DIR,
    VARIANTS,
    class_tfidf,
    handle,
    load,
    normalise,
    stem,
)


def centroids(unit: np.ndarray, labels: np.ndarray) -> tuple[list[int], np.ndarray]:
    ids = sorted(set(labels.tolist()) - {-1})
    if not ids:
        return [], np.zeros((0, unit.shape[1]), dtype=unit.dtype)
    return ids, normalise(np.vstack([unit[labels == c].mean(axis=0) for c in ids]))


def merge_groups(ids: list[int], C: np.ndarray, threshold: float) -> dict[int, int]:
    """Cluster id -> group representative (smallest id), closed under transitivity."""
    parent = {c: c for c in ids}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    sims = C @ C.T
    for i, j in zip(*np.nonzero(np.triu(sims >= threshold, k=1))):
        a, b = find(ids[i]), find(ids[j])
        if a != b:
            parent[max(a, b)] = min(a, b)
    return {c: find(c) for c in ids}


def relabel(labels: np.ndarray, group: dict[int, int]) -> np.ndarray:
    out = labels.copy()
    for c, g in group.items():
        out[labels == c] = g
    return out


def build_tree(
    unit: np.ndarray, merged: np.ndarray, texts: list[str], levels: list[float]
) -> list[dict]:
    gids, G = centroids(unit, merged)
    level0 = class_tfidf(texts, merged)
    rows = [
        {"node": f"L0:{g}", "level": 0, "parent": None, "clusters": [g], "size": int((merged == g).sum()),
         "keywords": level0.get(g, [])}
        for g in gids
    ]  # fmt: skip
    if len(gids) < 2:
        return rows
    Z = linkage(pdist(G, metric="cosine"), method="average")
    by_node = {r["node"]: r for r in rows}
    top_of = {g: f"L0:{g}" for g in gids}
    for level, cut in enumerate(levels, start=1):
        assignment = fcluster(Z, t=cut, criterion="distance")
        parents: dict[int, list[int]] = {}
        for g, a in zip(gids, assignment):
            parents.setdefault(int(a), []).append(g)
        node_labels = relabel(merged, {g: int(a) for g, a in zip(gids, assignment)})
        keywords = class_tfidf(texts, node_labels)
        for a, members in parents.items():
            if len({top_of[g] for g in members}) < 2:
                continue  # a parent over one existing node adds no information
            node = f"L{level}:{a}"
            row = {"node": node, "level": level, "parent": None, "clusters": members,
                   "size": int((node_labels == a).sum()), "keywords": keywords.get(a, [])}  # fmt: skip
            rows.append(row)
            by_node[node] = row
            for g in members:
                child = by_node[top_of[g]]
                if child["parent"] is None and child["level"] < level:
                    child["parent"] = node
                top_of[g] = node
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--variant", default="parts", choices=VARIANTS)
    parser.add_argument("--min-cluster-size", type=int, default=15)
    parser.add_argument("--umap-dim", type=int, default=10)
    parser.add_argument(
        "--merge-threshold",
        type=float,
        default=0.90,
        help="cosine similarity between centroids",
    )
    parser.add_argument(
        "--levels",
        nargs="+",
        type=float,
        default=[0.25, 0.40],
        help="cosine distances at which to cut the tree",
    )
    args = parser.parse_args()

    name = stem(args.variant, args.min_cluster_size, args.umap_dim)
    corpus = load(args.variant, args.dir)
    unit = normalise(corpus.vectors)
    assigned = pd.read_parquet(args.dir / f"assignments_{name}.parquet").set_index("id")
    labels = assigned.loc[corpus.ids, "cluster"].to_numpy()
    texts = corpus.texts
    ids, C = centroids(unit, labels)
    keywords = class_tfidf(texts, labels)

    # ---- merges ----
    group = merge_groups(ids, C, args.merge_threshold)
    groups: dict[int, list[int]] = {}
    for c, g in group.items():
        groups.setdefault(g, []).append(c)
    merged_groups = {g: members for g, members in groups.items() if len(members) > 1}
    merged = relabel(labels, group)
    sims = C @ C.T
    index = {c: i for i, c in enumerate(ids)}
    pd.DataFrame(
        [
            {
                "cluster": c,
                "term": g,
                "similarity_to_term": float(sims[index[c], index[g]]),
            }
            for c, g in group.items()
        ]
    ).to_parquet(args.dir / f"merges_{name}.parquet", index=False)
    pd.DataFrame({"id": corpus.ids, "cluster": labels, "term": merged}).to_parquet(
        args.dir / f"terms_{name}.parquet", index=False
    )

    # ---- tree over merged centroids ----
    rows = build_tree(unit, merged, texts, args.levels)
    pd.DataFrame(rows).to_parquet(args.dir / f"hierarchy_{name}.parquet", index=False)
    term_keywords = {r["clusters"][0]: r["keywords"] for r in rows if r["level"] == 0}

    # ---- report ----
    absorbed = sum(len(m) - 1 for m in merged_groups.values())
    lines = [
        f"# Merges and hierarchy: {args.variant}",
        "",
        f"- clusters: {len(ids)}; merge threshold {args.merge_threshold}: {len(merged_groups)} groups absorbing "
        f"{absorbed} clusters -> {len(ids) - absorbed} terms",
        f"- tree cuts at cosine distance {args.levels}: "
        + ", ".join(
            f"level {lv}: {sum(1 for r in rows if r['level'] == lv)} parents"
            for lv in range(1, len(args.levels) + 1)
        ),
        "",
        "## Merges",
        "",
    ]
    for g, members in sorted(merged_groups.items(), key=lambda kv: -len(kv[1])):
        lines.append(
            f"- **T{g} {handle(term_keywords.get(g, []))}** <- "
            + "; ".join(f"C{c} ({handle(keywords.get(c, []))})" for c in members)
        )
    for level in range(1, len(args.levels) + 1):
        lines += ["", f"## Level {level} parents (largest first)", ""]
        for r in sorted(
            (r for r in rows if r["level"] == level), key=lambda r: -r["size"]
        ):
            children = " · ".join(
                f"T{c} {handle(term_keywords.get(c, []), 2)}" for c in r["clusters"]
            )
            lines.append(
                f"- **{handle(r['keywords'])}** ({r['size']} halves, {len(r['clusters'])} terms): {children}"
            )
    report = args.dir / f"report_hierarchy_{name}.md"
    report.write_text("\n".join(lines))
    print("\n".join(lines[:4]))
    print(f"\nfull report: {report}")


if __name__ == "__main__":
    main()
