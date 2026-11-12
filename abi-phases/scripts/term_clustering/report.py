# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy", "pandas", "pyarrow", "scikit-learn>=1.4"]
# ///
"""Assemble the candidate-terms report from the outputs of the other steps.

    uv run scripts/term_clustering/report.py --min-cluster-size 40 --sentences-min-cluster-size 40 \\
        --compare compare_parts_mcs15_umap10_vs_parts_mcs40_umap10.json --notes notes.md

Reads ``selection.json``, ``embedding.json``, ``clusters_*.json``,
``stability_*.json``, the ``parts`` assignments, merges, terms and hierarchy,
and optionally a comparison from ``compare_runs.py`` and a Markdown file of
notes placed after the title. Writes into ``--dir``:

- ``candidate_terms.csv`` and ``candidate_terms.md``: one row per merged part
  cluster (a term), most papers first;
- ``term_graph.csv``: edges between two terms, with sentence and paper counts;
- ``report.md``: everything, for reading.

A term is flagged ``methodological`` when most of its halves talk about the
study rather than its subject (model fit, regression, reliability, significance
and the like). The flag is a word-list heuristic for review, not a judgement.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from statistics import median

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cluster import (  # noqa: E402
    DEFAULT_DIR,
    aggregate_edges,
    central_examples,
    class_tfidf,
    handle,
    load,
    normalise,
    part_papers,
    stem,
)

DOMAIN, METHODOLOGICAL, MEASUREMENT = "domain", "methodological", "measurement"
# Words about how a study was done or analysed, not about what it found:
# statistics, design and sampling, qualitative method, procedure.
METHOD_TERMS = re.compile(
    r"\b("
    r"model fit|fit ind\w*|goodness[- ]of[- ]fit|rmsea|cfi|tli|srmr|chi[- ]?squared?|aic|bic|"
    r"regression\w*|coefficients?|correlat\w*|covariates?|controll?(ed|ing)? for|adjust(ed|ing)? for|"
    r"multilevel|hierarchical linear|structural equation|sem|latent (class|profile|growth|variable)\w*|"
    r"factor (loadings?|analys[ie]s|structure|model)|loadings?|invarian\w*|reliabilit\w*|cronbach\w*|"
    r"internal consistency|validity|imputation|imputed|missing data|attrition|outliers?|"
    r"sample sizes?|statistical(ly)?|significan(t|tly|ce)|p[- ]values?|confidence intervals?|"
    r"effect sizes?|odds ratios?|hazard ratios?|standard (error|deviation)s?|variances?|"
    r"estimates?|estimated|estimation|parameters?|analys[ie]s|analy[sz]ed|robust(ness)?|"
    r"sensitivity analys[ie]s|bootstrap\w*|cross[- ]sectional|longitudinal design|study design|"
    r"generali[sz]ab\w*|limitations?|findings|researchers?|"
    r"qualitative|quantitative|thematic\w*|themes?|cod(ed|ing)|codes|interviews?|interviewed|"
    r"semi[- ]structured|phenomenolog\w*|grounded theory|"
    r"informed consent|consent|ethic(s|al) (approval|committee)|recruit(ed|ment)|respondents?|"
    r"response rate|surveys?|surveyed|excluded|exclusion criteria|inclusion criteria|"
    r"retrospective(ly)?|recall bias|self[- ]reports?"
    r")\b",
    re.IGNORECASE,
)
# Words about instruments and their scores: information about a construct,
# not the construct itself.
MEASUREMENT_TERMS = re.compile(
    r"\b(scales?|subscales?|items?|scores?|scored|scoring|questionnaires?|inventory|instruments?|"
    r"measures?|measured|measurement|cut[- ]?offs?|psychometric\w*|indicates?|indicating)\b",
    re.IGNORECASE,
)
KIND_SHARE = 0.5


def share_matching(texts: list[str], pattern: re.Pattern) -> float:
    if not texts:
        return 0.0
    return sum(1 for t in texts if pattern.search(t)) / len(texts)


def term_kind(
    texts: list[str], threshold: float = KIND_SHARE
) -> tuple[str, float, float]:
    """The kind of a term from its halves, with the two shares behind it.

    Methodological wins over measurement: a term about a scale's reliability is
    about the method.
    """
    method = share_matching(texts, METHOD_TERMS)
    measure = share_matching(texts, MEASUREMENT_TERMS)
    if method >= threshold:
        return METHODOLOGICAL, method, measure
    if measure >= threshold:
        return MEASUREMENT, method, measure
    return DOMAIN, method, measure


def read_json(path: Path) -> dict | None:
    return json.loads(path.read_text()) if path.exists() else None


def pct(x: float) -> str:
    return f"{x:.0%}"


def member_papers(key: str, papers_of: dict[str, list[str]], limit: int = 2) -> str:
    titles = papers_of.get(key, [])
    shown = "; ".join(titles[:limit])
    return shown + (f" (+{len(titles) - limit} more)" if len(titles) > limit else "")


def candidate_terms(
    directory: Path, name: str, examples: int
) -> tuple[pd.DataFrame, pd.DataFrame, np.ndarray]:
    corpus = load("parts", directory)
    unit = normalise(corpus.vectors)
    terms = (
        pd.read_parquet(directory / f"terms_{name}.parquet")
        .set_index("id")
        .loc[corpus.ids]
    )
    merged = terms["term"].to_numpy()
    merges = pd.read_parquet(directory / f"merges_{name}.parquet")
    hierarchy = pd.read_parquet(directory / f"hierarchy_{name}.parquet")
    conditionals = pd.read_parquet(directory / "conditionals.parquet")
    pairs = part_papers(conditionals)
    papers_of = pairs.groupby("key")["title"].agg(list).to_dict()
    pairs = pairs.assign(term=pairs["key"].map(dict(zip(corpus.ids, merged))))
    papers = pairs[pairs["term"] >= 0].groupby("term")["paper_id"].nunique()
    keywords = class_tfidf(corpus.texts, merged)
    parent_of = {
        int(row.clusters[0]): row.parent
        for row in hierarchy[hierarchy["level"] == 0].itertuples()
        if row.parent
    }
    parent_handle = {
        row.node: handle(list(row.keywords))
        for row in hierarchy.itertuples()
        if row.level > 0
    }
    texts = np.array(corpus.texts, dtype=object)
    extra = corpus.extra
    rows = []
    for term in sorted(set(merged.tolist()) - {-1}):
        members = np.flatnonzero(merged == term)
        central = central_examples(unit, merged, term, examples)
        kind, method_share, measure_share = term_kind(texts[members].tolist())
        parent = parent_of.get(term)
        row = {
            "term": f"T{term}",
            "handle": handle(keywords.get(term, [])),
            "kind": kind,
            "method_word_share": round(method_share, 2),
            "measurement_word_share": round(measure_share, 2),
            "size": len(members),
            "papers": int(papers.get(term, 0)),
            "as_antecedent": int(extra["as_antecedent"].to_numpy()[members].sum()),
            "as_consequent": int(extra["as_consequent"].to_numpy()[members].sum()),
            "merged_from": " ".join(
                f"C{c}" for c in sorted(merges.loc[merges["term"] == term, "cluster"])
            ),
            "parent": f"{parent} {parent_handle.get(parent, '')}".strip()
            if parent
            else "",
            "keywords": ", ".join(keywords.get(term, [])),
        }
        for k, i in enumerate(central, start=1):
            row[f"member_{k}"] = corpus.texts[i]
            row[f"member_{k}_papers"] = member_papers(corpus.ids[i], papers_of)
        rows.append(row)
    table = (
        pd.DataFrame(rows)
        .sort_values(["papers", "size"], ascending=False)
        .reset_index(drop=True)
    )
    return table, conditionals, merged


def term_graph(
    conditionals: pd.DataFrame, ids: list[str], merged: np.ndarray, table: pd.DataFrame
) -> pd.DataFrame:
    edges = aggregate_edges(conditionals, dict(zip(ids, merged.tolist())))
    handles = dict(zip(table["term"], table["handle"]))
    kinds = dict(zip(table["term"], table["kind"]))
    edges = edges[~edges["self_loop"]].copy()
    for end in ("src", "dst"):
        edges[end] = "T" + edges[end].astype(int).astype(str)
        edges[f"{end}_handle"] = edges[end].map(handles)
        edges[f"{end}_kind"] = edges[end].map(kinds)
    return edges.drop(columns="self_loop").rename(
        columns={"src": "antecedent_term", "dst": "consequent_term", "src_handle": "antecedent",
                 "dst_handle": "consequent", "src_kind": "antecedent_kind", "dst_kind": "consequent_kind"}
    )  # fmt: skip


def md_cell(text: object) -> str:
    return str(text).replace("|", "/").replace("\n", " ")


def selection_section(selection: dict, embedding: dict | None) -> list[str]:
    s = selection
    lines = [
        "## Corpus",
        "",
        f"- versions kept: {', '.join(s['versions']) if isinstance(s['versions'], list) else s['versions']}; "
        f"models kept: {', '.join(s['models']) if isinstance(s['models'], list) else s['models']}",
        f"- conditional items in the store: {s['conditional_items']:,}",
        f"- parsed conditionals in the selection: {s['parsed_conditionals']:,}, from {s['papers']} papers "
        f"({s['chunks']:,} chunks)",
        f"- distinct antecedents: {s['distinct_antecedents']:,}; distinct consequents: {s['distinct_consequents']:,}; "
        f"distinct halves: {s['distinct_halves']:,} ({s['halves_in_both_roles']:,} used in both roles)",
        "- left out: " + "; ".join(f"{why}: {n:,}" for why, n in s["left_out"].items()),
        "",
        "| prompt version | model | items | kept |",
        "|---|---|---|---|",
    ]
    for row in sorted(s["by_version_and_model"], key=lambda r: -r["items"]):
        lines.append(
            f"| `{row['prompt_id']}` | {row['model_id']} | {row['items']:,} | {row['kept']:,} |"
        )
    if s["unparsed_examples"]:
        lines += ["", 'Not in the "If X, then Y." form, for example:', ""]
        lines += [f"- {md_cell(t[:200])}" for t in s["unparsed_examples"][:5]]
    if embedding:
        lines += [
            "",
            f"Embedding the distinct halves: {embedding['tokens']:,} tokens, ${embedding['usd']:.2f} "
            f"(text-embedding-3-large at 3072 dimensions, $0.13 per million tokens).",
        ]
    return lines + [""]


def clusters_section(
    directory: Path, chosen: dict[str, int], umap_dim: int
) -> list[str]:
    lines = [
        "## Clusters",
        "",
        "| variant | min_cluster_size | points | clusters | clustered | noise | median size | "
        "papers per cluster: 1 / 2 / 3-5 / 6+ |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for variant in ("sentences", "parts"):
        found = sorted(
            directory.glob(f"clusters_{variant}_mcs*_umap{umap_dim}.json"),
            key=lambda p: p.stat().st_mtime,
        )
        rows = sorted(
            (json.loads(p.read_text()) for p in found),
            key=lambda r: r["min_cluster_size"],
        )
        for r in rows:
            spread = r["paper_spread"]
            mark = " **(chosen)**" if r["min_cluster_size"] == chosen[variant] else ""
            lines.append(
                f"| {variant} | {r['min_cluster_size']}{mark} | {r['points']:,} | {r['clusters']} | "
                f"{pct(r['clustered_share'])} | {pct(1 - r['clustered_share'])} | {r['size_median']:.0f} | "
                f"{spread['1']} / {spread['2']} / {spread['3-5']} / {spread['6+']} |"
            )
    return lines + [""]


def candidates_section(table: pd.DataFrame, limit: int | None = None) -> list[str]:
    lines = [
        "| term | handle | kind | halves | papers | central members (paper) | parent |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in table.head(limit).itertuples() if limit else table.itertuples():
        members = "<br>".join(
            f"{md_cell(getattr(row, f'member_{k}'))} <sub>({md_cell(getattr(row, f'member_{k}_papers'))})</sub>"
            for k in range(1, 4)
            if isinstance(getattr(row, f"member_{k}", None), str)
        )
        lines.append(
            f"| {row.term} | {md_cell(row.handle)} | {row.kind} | {row.size} | {row.papers} | {members} "
            f"| {md_cell(row.parent)} |"
        )
    return lines


def hierarchy_section(directory: Path, name: str, table: pd.DataFrame) -> list[str]:
    merges = pd.read_parquet(directory / f"merges_{name}.parquet")
    hierarchy = pd.read_parquet(directory / f"hierarchy_{name}.parquet")
    handles = dict(zip(table["term"], table["handle"]))
    groups = merges.groupby("term")["cluster"].agg(list)
    groups = groups[groups.str.len() > 1]
    lines = [
        "## Merges and hierarchy",
        "",
        f"{len(merges)} clusters, {len(groups)} merge groups absorbing {int((groups.str.len() - 1).sum())} clusters, "
        f"{len(table)} terms.",
        "",
        "### Merges (centroids at or above the merge threshold)",
        "",
    ]
    for term, clusters in groups.items():
        sims = merges.set_index("cluster").loc[clusters, "similarity_to_term"]
        lines.append(
            f"- **T{term} {md_cell(handles.get(f'T{term}', ''))}** <- "
            + ", ".join(f"C{c} ({s:.2f})" for c, s in zip(clusters, sims))
        )
    for level in sorted(set(hierarchy["level"]) - {0}):
        nodes = hierarchy[hierarchy["level"] == level].sort_values(
            "size", ascending=False
        )
        lines += ["", f"### Level {level} parents ({len(nodes)})", ""]
        for node in nodes.itertuples():
            children = ", ".join(
                f"T{c} {md_cell(handles.get(f'T{c}', ''))}" for c in node.clusters
            )
            lines.append(
                f"- **{md_cell(handle(list(node.keywords)))}** ({node.size:,} halves): {children}"
            )
    return lines + [""]


def graph_section(graph: pd.DataFrame, min_papers: int, limit: int) -> list[str]:
    strong = graph[graph["papers"] >= min_papers]
    lines = [
        "## Term graph",
        "",
        f"Edges between two different terms supported by {min_papers} or more papers: {len(strong):,} "
        f"(of {len(graph):,} edges between two terms; {int((graph['papers'] >= 3).sum()):,} have 3 or more papers). "
        f"Strongest first; the first {min(limit, len(strong))} are shown, all are in `term_graph.csv`.",
        "",
        "| papers | sentences | antecedent term | consequent term | example |",
        "|---|---|---|---|---|",
    ]
    for e in strong.head(limit).itertuples():
        flag = lambda kind: f" *({kind})*" if kind != DOMAIN else ""  # noqa: E731
        lines.append(
            f"| {e.papers} | {e.n} | {e.antecedent_term} {md_cell(e.antecedent)}{flag(e.antecedent_kind)} "
            f"| {e.consequent_term} {md_cell(e.consequent)}{flag(e.consequent_kind)} | {md_cell(e.example)} |"
        )
    return lines + [""]


def stability_section(
    directory: Path, chosen: dict[str, int], umap_dim: int
) -> list[str]:
    lines = [
        "## Stability across seeds",
        "",
        "Same points and settings, three UMAP seeds. Majority mapping: each cluster of run B is mapped to the "
        "cluster of run A holding most of its points, and the share of points landing there is reported "
        "(both directions). ARI is the adjusted Rand index. Both are computed on the points both runs clustered.",
        "",
        "| variant | run A | run B | clustered in both | B->A | A->B | ARI |",
        "|---|---|---|---|---|---|---|",
    ]
    notes = []
    for variant in ("sentences", "parts"):
        data = read_json(
            directory / f"stability_{stem(variant, chosen[variant], umap_dim)}.json"
        )
        if not data:
            continue
        for p in data["pairs"]:
            lines.append(
                f"| {variant} | {p['a']} | {p['b']} | {pct(p['clustered_in_both'])} | {pct(p['b_to_a'])} | "
                f"{pct(p['a_to_b'])} | {p['ari']:.2f} |"
            )
        seeds = "; ".join(
            f"seed {s['seed']}: {s['clusters']} clusters, {pct(s['noise'])} noise"
            for s in data["seeds"]
        )
        notes.append(
            f"- {variant}: {seeds}; clustered in every run: {pct(data['clustered_in_every_run'])}"
        )
    return lines + [""] + notes + [""]


def comparison_section(data: dict, table: pd.DataFrame, limit: int) -> list[str]:
    c = data["counts"]
    held, near = data["held"], data["near"]
    shared = [
        e["shared_halves_in_match"]
        for e in data["earlier"]
        if e["shared_halves_in_match"] is not None
    ]
    lines = [
        "## Comparison with the 26-paper run",
        "",
        f"Terms are matched by centroid similarity: *held* at {held} or above, *near* from {near}, below that "
        "*not found* (earlier terms) or *new* (current terms). A near match that is also the closest match of "
        "other earlier terms is counted as *into a broader term*: the current run, with more data and a larger "
        "minimum cluster size, joined them. *Shared halves* is the share of an earlier term's halves, among those "
        "both runs contain and cluster, that sit in the matched term.",
        "",
        f"- earlier terms: {c['earlier_terms']}: held {c['earlier']['held']}, into a broader term "
        f"{c['earlier']['into a broader term']}, near {c['earlier']['near']}, not found {c['earlier']['not found']}",
        f"- earlier terms with halves in both runs: {len(shared)}; median share of those halves in the matched "
        f"term: {pct(median(shared)) if shared else 'n/a'}",
        f"- current terms: {c['later_terms']}: held {c['later']['held']}, near {c['later']['near']}, "
        f"new {c['later']['new']}",
        "",
        "### Earlier terms, most papers first",
        "",
        "| earlier term | papers then | status | similarity | current term | papers now | shared halves in it |",
        "|---|---|---|---|---|---|---|",
    ]
    for e in sorted(data["earlier"], key=lambda e: (-e["papers"], -e["size"]))[:limit]:
        shared = (
            ""
            if e["shared_halves_in_match"] is None
            else pct(e["shared_halves_in_match"])
        )
        lines.append(
            f"| {md_cell(e['handle'])} | {e['papers']} | {e['status']} | {e['similarity']:.2f} | "
            f"T{e['match']} {md_cell(e['match_handle'])} | {e['match_papers']} | {shared} |"
        )
    lost = [e for e in data["earlier"] if e["status"] == "not found"]
    if lost:
        lines += ["", "### Earlier terms not found", ""]
        lines += [
            f"- {md_cell(e['handle'])} ({e['papers']} papers; closest now T{e['match']} "
            f"{md_cell(e['match_handle'])}, {e['similarity']:.2f})"
            for e in sorted(lost, key=lambda e: -e["papers"])
        ]
    kinds = dict(zip(table["term"], table["kind"]))
    new = sorted(
        (e for e in data["later"] if e["status"] == "new"),
        key=lambda e: (-e["papers"], -e["size"]),
    )
    lines += [
        "",
        f"### New terms, most papers first ({len(new)}; the first {min(limit, len(new))})",
        "",
    ]
    lines += [
        f"- T{e['term']} {md_cell(e['handle'])} ({e['papers']} papers, {e['size']} halves"
        + (
            f", {kinds[f'T{e["term"]}']}"
            if kinds.get(f"T{e['term']}", DOMAIN) != DOMAIN
            else ""
        )
        + ")"
        for e in new[:limit]
    ]
    return lines + [""]


def kind_lines(rows: pd.DataFrame, share_column: str) -> list[str]:
    return [
        f"- {r.term} {md_cell(r.handle)} ({r.papers} papers, {r.size} halves, "
        f"{getattr(r, share_column):.0%} of halves): {md_cell(r.member_1)}"
        for r in rows.itertuples()
    ]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument(
        "--min-cluster-size",
        type=int,
        required=True,
        help="the parts setting to report",
    )
    parser.add_argument("--sentences-min-cluster-size", type=int, default=None)
    parser.add_argument("--umap-dim", type=int, default=10)
    parser.add_argument("--examples", type=int, default=5)
    parser.add_argument("--min-edge-papers", type=int, default=2)
    parser.add_argument(
        "--compare",
        type=Path,
        default=None,
        help="a compare_*.json from compare_runs.py",
    )
    parser.add_argument(
        "--notes", type=Path, default=None, help="Markdown placed after the title"
    )
    parser.add_argument(
        "--title", default="Candidate terms from the conditional statements"
    )
    args = parser.parse_args()

    chosen = {
        "parts": args.min_cluster_size,
        "sentences": args.sentences_min_cluster_size or args.min_cluster_size,
    }
    name = stem("parts", args.min_cluster_size, args.umap_dim)
    table, conditionals, merged = candidate_terms(args.dir, name, args.examples)
    ids = pd.read_parquet(args.dir / "parts.parquet")["key"].tolist()
    graph = term_graph(conditionals, ids, merged, table)
    table.to_csv(args.dir / "candidate_terms.csv", index=False)
    graph.to_csv(args.dir / "term_graph.csv", index=False)

    methodological = table[table["kind"] == METHODOLOGICAL]
    measurement = table[table["kind"] == MEASUREMENT]
    terms_md = [f"# {args.title}: {len(table)} terms", "", *candidates_section(table)]
    (args.dir / "candidate_terms.md").write_text("\n".join(terms_md) + "\n")

    lines = [f"# {args.title}", ""]
    if args.notes:
        lines += [args.notes.read_text().strip(), ""]
    lines += selection_section(
        json.loads((args.dir / "selection.json").read_text()),
        read_json(args.dir / "embedding.json"),
    )
    lines += clusters_section(args.dir, chosen, args.umap_dim)
    lines += [
        "## Candidate terms",
        "",
        f"{len(table)} terms (merged part clusters): {len(table) - len(methodological) - len(measurement)} domain, "
        f"{len(methodological)} methodological, {len(measurement)} measurement. All are in `candidate_terms.csv` and `candidate_terms.md`; the 60 with most papers:",
        "",
        *candidates_section(table, 60),
        "",
    ]
    lines += graph_section(graph, args.min_edge_papers, 80)
    lines += hierarchy_section(args.dir, name, table)
    lines += stability_section(args.dir, chosen, args.umap_dim)
    if args.compare:
        lines += comparison_section(
            json.loads((args.dir / args.compare).read_text()), table, 60
        )
    lines += [
        "## Methodological and measurement terms",
        "",
        "A different kind of term. They are coherent clusters, but they name parts of how the papers studied "
        "their subject, not the subject. A term is *methodological* when at least "
        f"{KIND_SHARE:.0%} of its halves use words about statistics, design, sampling, qualitative method or "
        "procedure, and *measurement* when as many name an instrument, a scale, an item or a score. The flag is "
        "a word list, for review; the shares are in the CSV.",
        "",
        f"### Methodological ({len(methodological)})",
        "",
        *kind_lines(methodological, "method_word_share"),
        "",
        f"### Measurement ({len(measurement)})",
        "",
        *kind_lines(measurement, "measurement_word_share"),
        "",
    ]
    (args.dir / "report.md").write_text("\n".join(lines))
    print(
        f"{len(table)} terms ({len(methodological)} methodological, {len(measurement)} measurement), "
        f"{len(graph)} edges between terms"
    )
    print(
        f"wrote report.md, candidate_terms.csv, candidate_terms.md and term_graph.csv to {args.dir}"
    )


if __name__ == "__main__":
    main()
