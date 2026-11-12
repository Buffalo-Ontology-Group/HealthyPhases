# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy", "pandas", "pyarrow", "scikit-learn>=1.4", "umap-learn>=0.5.6"]
# ///
"""Do the conditional statements form clusters we could name as terms?

Two variants of the same pipeline (L2-normalise -> UMAP -> HDBSCAN):

- ``sentences``: whole "If X, then Y." sentences, with the vectors already in
  the corpus. This is the baseline: what you get for free.
- ``parts``: the antecedents and consequents on their own (see
  ``embed_parts.py``). Each cluster is a candidate term, and every sentence
  becomes an edge between two clusters, so this variant also reports the
  term graph.

    uv run scripts/term_clustering/cluster.py --variant sentences parts --min-cluster-size 15

Writes, per variant and setting, ``report_<stem>.md``, ``clusters_<stem>.json``
(the headline numbers) and ``assignments_<stem>.parquet`` (cluster label,
membership probability and 2-D map coordinates per point), plus
``edges_<stem>.parquet`` for ``parts``; ``<stem>`` is
``<variant>_mcs<k>_umap<d>``. HDBSCAN leaves points it cannot place as noise
(label -1); the noise share is reported, not hidden. Several
``--min-cluster-size`` values can be given at once: the UMAP reduction is
cached per seed under ``cache/`` and shared between them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.cluster import HDBSCAN
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, CountVectorizer

DEFAULT_DIR = Path(__file__).resolve().parents[2] / "storage/spikes/term_clustering"
VARIANTS = ("sentences", "parts")
# Hedges the prompt asks for; they would otherwise top every keyword list.
HEDGES = {
    "may",
    "might",
    "tend",
    "tends",
    "likely",
    "often",
    "generally",
    "typically",
    "usually",
}


@dataclass
class Corpus:
    variant: str
    ids: list[str]
    texts: list[str]
    vectors: np.ndarray
    extra: pd.DataFrame  # per-point columns worth showing (paper, role counts, ...)


def stem(variant: str, mcs: int, umap_dim: int) -> str:
    return f"{variant}_mcs{mcs}_umap{umap_dim}"


def load(variant: str, directory: Path) -> Corpus:
    if variant == "sentences":
        conditionals = pd.read_parquet(directory / "conditionals.parquet")
        return Corpus(
            variant,
            conditionals["item_id"].tolist(),
            conditionals["text"].tolist(),
            np.load(directory / "sentences.vectors.npy"),
            conditionals[["paper_id", "title"]],
        )
    if variant != "parts":
        raise ValueError(f"unknown variant {variant!r}; expected one of {VARIANTS}")
    table = pd.read_parquet(directory / "parts.parquet")
    extra = [c for c in table.columns if c not in ("key", "text")]
    return Corpus(
        variant,
        table["key"].tolist(),
        table["text"].tolist(),
        np.load(directory / "parts.vectors.npy"),
        table[extra],
    )


def normalise(vectors: np.ndarray) -> np.ndarray:
    vectors = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.where(norms == 0, 1, norms)


def fingerprint(ids: list[str]) -> str:
    return hashlib.sha256("\n".join(ids).encode()).hexdigest()[:12]


def reduced(
    directory: Path,
    corpus: Corpus,
    unit: np.ndarray,
    dim: int,
    seed: int,
    min_dist: float = 0.0,
) -> np.ndarray:
    """UMAP to ``dim`` dimensions (cosine), cached by points, dimension, seed and min_dist."""
    if dim == 0:
        return unit
    cache = (
        directory
        / "cache"
        / f"umap_{corpus.variant}_{fingerprint(corpus.ids)}_d{dim}_md{min_dist}_s{seed}.npy"
    )
    if cache.exists():
        return np.load(cache)
    import umap  # slow import (numba); keep it out of --help

    out = umap.UMAP(
        n_components=dim,
        n_neighbors=15,
        min_dist=min_dist,
        metric="cosine",
        random_state=seed,
    ).fit_transform(unit)
    cache.parent.mkdir(exist_ok=True)
    np.save(cache, out)
    return out


def discover(points: np.ndarray, mcs: int, min_samples: int | None = None) -> HDBSCAN:
    return HDBSCAN(
        min_cluster_size=mcs, min_samples=min_samples, cluster_selection_method="eom"
    ).fit(points)


def class_tfidf(
    texts: list[str], labels: np.ndarray, top: int = 8
) -> dict[int, list[str]]:
    """BERTopic-style c-TF-IDF: one document per cluster, idf against clusters."""
    clusters = sorted(set(labels.tolist()) - {-1})
    if not clusters:
        return {}
    docs = [" ".join(t for t, lab in zip(texts, labels) if lab == c) for c in clusters]
    vectoriser = CountVectorizer(
        stop_words=list(ENGLISH_STOP_WORDS | HEDGES), ngram_range=(1, 2), min_df=1
    )
    # Sparse: at full-corpus scale the bigram vocabulary is in the hundreds of thousands.
    counts = vectoriser.fit_transform(docs).astype(np.float64).tocsr()
    row_sums = np.asarray(counts.sum(axis=1)).ravel()
    col_sums = np.asarray(counts.sum(axis=0)).ravel()
    words_per_class = counts.sum() / len(clusters)
    idf = np.log1p(words_per_class / np.maximum(col_sums, 1))
    vocab = vectoriser.get_feature_names_out()
    out: dict[int, list[str]] = {}
    for k, c in enumerate(clusters):
        row = counts.getrow(k)
        scores = row.data / max(row_sums[k], 1) * idf[row.indices]
        order = np.argsort(-scores, kind="stable")[:top]
        out[c] = [vocab[row.indices[i]] for i in order if scores[i] > 0]
    return out


def handle(keywords: list[str], n: int = 3) -> str:
    """A short name from a cluster's keywords: the top ones, skipping words a bigram already shows."""
    picked: list[str] = []
    for word in keywords:
        if any(word in p.split() or p in word.split() for p in picked):
            continue
        picked.append(word)
        if len(picked) == n:
            break
    return " / ".join(picked)


def central_examples(
    unit: np.ndarray, labels: np.ndarray, label: int, k: int
) -> np.ndarray:
    members = np.flatnonzero(labels == label)
    centroid = unit[members].mean(axis=0)
    similarity = unit[members] @ centroid
    return members[np.argsort(-similarity)[:k]]


def part_papers(conditionals: pd.DataFrame) -> pd.DataFrame:
    """Distinct (half key, paper) pairs, with the paper's title."""
    rows = [
        conditionals[[f"{role}_key", "paper_id", "title"]].rename(
            columns={f"{role}_key": "key"}
        )
        for role in ("antecedent", "consequent")
    ]
    return pd.concat(rows).drop_duplicates(["key", "paper_id"]).reset_index(drop=True)


def cluster_papers(labels_by_key: pd.Series, pairs: pd.DataFrame) -> pd.Series:
    """Distinct papers per cluster, from (key, paper) pairs."""
    labelled = pairs.assign(cluster=pairs["key"].map(labels_by_key))
    labelled = labelled[labelled["cluster"].fillna(-1) >= 0]
    return labelled.groupby("cluster")["paper_id"].nunique()


def aggregate_edges(
    conditionals: pd.DataFrame, key_to_label: dict[str, int]
) -> pd.DataFrame:
    """Every sentence whose two halves both landed in a cluster is an edge.

    Edges are aggregated by (antecedent cluster, consequent cluster) with the
    number of sentences and of distinct papers behind them, strongest first:
    paper support, then sentences. Self-loops are kept and flagged.
    """
    src = conditionals["antecedent_key"].map(key_to_label)
    dst = conditionals["consequent_key"].map(key_to_label)
    edges = conditionals.assign(src=src, dst=dst)
    edges = edges[(edges["src"].fillna(-1) >= 0) & (edges["dst"].fillna(-1) >= 0)]
    columns = ["src", "dst", "n", "papers", "example", "self_loop"]
    if edges.empty:
        return pd.DataFrame(columns=columns)
    agg = (
        edges.astype({"src": int, "dst": int})
        .groupby(["src", "dst"])
        .agg(
            n=("text", "size"),
            papers=("paper_id", "nunique"),
            example=("text", "first"),
        )
        .reset_index()
    )
    agg["self_loop"] = agg["src"] == agg["dst"]
    return agg.sort_values(
        ["papers", "n", "src", "dst"], ascending=[False, False, True, True]
    )[columns].reset_index(drop=True)


def paper_spread(papers_per_cluster: pd.Series) -> dict[str, int]:
    bins = {"1": (1, 1), "2": (2, 2), "3-5": (3, 5), "6+": (6, 10**9)}
    return {
        name: int(papers_per_cluster.between(lo, hi).sum())
        for name, (lo, hi) in bins.items()
    }


def run(args: argparse.Namespace, variant: str) -> None:
    corpus = load(variant, args.dir)
    unit = normalise(corpus.vectors)
    t0 = time.time()
    points = reduced(args.dir, corpus, unit, args.umap_dim, args.seed)
    coords = reduced(args.dir, corpus, unit, 2, args.seed, min_dist=0.1)
    reduce_s = time.time() - t0
    conditionals = pd.read_parquet(args.dir / "conditionals.parquet")
    pairs = part_papers(conditionals)
    for mcs in args.min_cluster_size:
        t1 = time.time()
        model = discover(points, mcs, args.min_samples)
        write_outputs(
            args,
            corpus,
            unit,
            model,
            coords,
            conditionals,
            pairs,
            mcs,
            reduce_s,
            time.time() - t1,
        )


def write_outputs(
    args, corpus, unit, model, coords, conditionals, pairs, mcs, reduce_s, cluster_s
) -> None:
    variant = corpus.variant
    labels, probabilities = model.labels_, model.probabilities_
    n = len(labels)
    clusters = sorted(set(labels.tolist()) - {-1})
    sizes = pd.Series(labels[labels >= 0]).value_counts()
    noise = int((labels == -1).sum())
    keywords = class_tfidf(corpus.texts, labels)
    if variant == "parts":
        papers = cluster_papers(pd.Series(labels, index=corpus.ids), pairs)
    else:
        papers = (
            pd.Series(corpus.extra["paper_id"].to_numpy()[labels >= 0])
            .groupby(labels[labels >= 0])
            .nunique()
        )

    name = stem(variant, mcs, args.umap_dim)
    assignments = pd.DataFrame(
        {"id": corpus.ids, "text": corpus.texts, "cluster": labels, "probability": probabilities,
         "x": coords[:, 0], "y": coords[:, 1]}
    ).join(corpus.extra.reset_index(drop=True))  # fmt: skip
    assignments.to_parquet(args.dir / f"assignments_{name}.parquet", index=False)

    summary = {
        "variant": variant,
        "min_cluster_size": mcs,
        "min_samples": args.min_samples or mcs,
        "umap_dim": args.umap_dim,
        "seed": args.seed,
        "points": n,
        "clusters": len(clusters),
        "clustered": n - noise,
        "noise": noise,
        "clustered_share": round((n - noise) / n, 4) if n else 0,
        "size_min": int(sizes.min()) if len(sizes) else 0,
        "size_median": float(sizes.median()) if len(sizes) else 0,
        "size_max": int(sizes.max()) if len(sizes) else 0,
        "paper_spread": paper_spread(papers),
        "reduce_seconds": round(reduce_s),
        "cluster_seconds": round(cluster_s),
    }
    lines = [
        f"# Conditional statements: {variant} clusters",
        "",
        f"- points: {n}",
        f"- UMAP dim: {args.umap_dim or 'none (raw 3072-d)'}; HDBSCAN min_cluster_size={mcs}, "
        f"min_samples={args.min_samples or mcs}; seed={args.seed}",
        f"- clusters: {len(clusters)}",
        f"- clustered: {n - noise} ({(n - noise) / n:.0%}); noise: {noise} ({noise / n:.0%})",
        f"- cluster sizes: min {summary['size_min']}, median {summary['size_median']:.0f}, max {summary['size_max']}; "
        f"ten largest: {sizes.head(10).tolist()}",
        f"- distinct papers per cluster: {summary['paper_spread']}",
        f"- time: reduction {reduce_s:.0f}s (cached after the first run), clustering {cluster_s:.0f}s",
        "",
    ]

    if variant == "parts":
        edges = aggregate_edges(conditionals, dict(zip(corpus.ids, labels.tolist())))
        edges.to_parquet(args.dir / f"edges_{name}.parquet", index=False)
        both = int(edges["n"].sum())
        total = len(conditionals)
        summary |= {
            "sentences": total,
            "sentences_with_both_ends_clustered": both,
            "edges": len(edges),
            "self_loops": int(edges["self_loop"].sum()),
            "edges_2_papers": int(((edges["papers"] >= 2) & ~edges["self_loop"]).sum()),
            "edges_3_papers": int(((edges["papers"] >= 3) & ~edges["self_loop"]).sum()),
        }
        lines += [
            "## Term graph (antecedent cluster -> consequent cluster)",
            "",
            f"- sentences with both ends clustered: {both}/{total} ({both / max(total, 1):.0%})",
            f"- distinct edges: {len(edges)} ({summary['self_loops']} self-loops); between two clusters, "
            f"seen in >=2 papers: {summary['edges_2_papers']}, in >=3: {summary['edges_3_papers']}",
            "",
            "| papers | n | antecedent cluster | consequent cluster | example |",
            "|---|---|---|---|---|",
        ]
        for edge in edges[~edges["self_loop"]].head(args.edges).itertuples():
            example = edge.example.replace("|", "/")
            lines.append(
                f"| {edge.papers} | {edge.n} | C{edge.src}: {handle(keywords.get(edge.src, []))} "
                f"| C{edge.dst}: {handle(keywords.get(edge.dst, []))} | {example} |"
            )
        lines.append("")

    (args.dir / f"clusters_{name}.json").write_text(json.dumps(summary, indent=2))
    lines += ["## Clusters (largest first)", ""]
    for label in sizes.index:
        members = np.flatnonzero(labels == label)
        lines += [
            f"### C{label}: {handle(keywords.get(label, []))} (n={len(members)}, papers={int(papers.get(label, 0))})",
            "",
            f"keywords: {', '.join(keywords.get(label, []))}",
            "",
        ]
        for i in central_examples(unit, labels, label, args.examples):
            detail = ""
            if variant == "parts":
                row = corpus.extra.iloc[i]
                detail = f"  (antecedent {row['as_antecedent']}, consequent {row['as_consequent']}, papers {row['papers']})"
            lines.append(f"- {corpus.texts[i]}{detail}")
        lines.append("")

    report = args.dir / f"report_{name}.md"
    report.write_text("\n".join(lines))
    print("\n".join(lines[:9]))
    print(f"full report: {report}\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--variant", nargs="+", default=list(VARIANTS), choices=VARIANTS
    )
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--min-cluster-size", type=int, nargs="+", default=[15])
    parser.add_argument("--min-samples", type=int, default=None)
    parser.add_argument(
        "--umap-dim", type=int, default=10, help="0 clusters the raw vectors"
    )
    parser.add_argument("--examples", type=int, default=6)
    parser.add_argument("--edges", type=int, default=40)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    for variant in args.variant:
        run(args, variant)


if __name__ == "__main__":
    main()
