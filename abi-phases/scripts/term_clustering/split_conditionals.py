# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy", "pandas", "pyarrow"]
# ///
"""Select the conditional statements and split each "If X, then Y." in two.

The prompt forces exactly that form, so the split is a regex. The two halves are
embedded and clustered separately: an antecedent and a consequent are each a
candidate term, and the sentence is an edge between them.

    uv run scripts/term_clustering/split_conditionals.py

By default the selection is the current version of the ``conditional_statement``
prompt and the default extraction model, both read from the module's code (see
``declared.py``). ``--versions`` and ``--models`` widen it; every conditional
item that is left out is counted under one reason: another version, another
model, or not in the "If X, then Y." form.

Reads the dump written by ``dump_vectors.py`` (and ``papers.parquet`` from
``paper_titles.py`` when present) from ``--dump-dir``; writes into ``--dir``:

- ``conditionals.parquet``: one row per parsed sentence, with its halves and
  their normalised keys, the paper id and title;
- ``sentences.vectors.npy``: the corpus vectors of those sentences, rows aligned;
- ``selection.json``: the counts printed below.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import declared  # noqa: E402

DEFAULT_DIR = Path(__file__).resolve().parents[2] / "storage/spikes/term_clustering"
ITEMS = "phases_v2_extracted_items"
# The prompt was renamed once; both names are the same extraction.
CONDITIONAL_PROMPTS = ("conditional_statement", "logical_sentences")
# Greedy first group: split on the LAST ", then" so antecedents containing
# their own commas ("If A, B and C, then D") stay whole. A few items close the
# antecedent with a dash instead of a comma, put the comma inside a closing
# quote ("'innocence,' then"; the quote stays with the antecedent), or follow
# "then" with a comma.
FORM = re.compile(
    r"^\s*If\s+(.*)[,—–]([\'\"’”]?)\s*then,?\s+(.*?)\s*\.?\s*$",
    re.IGNORECASE | re.DOTALL,
)
# Only when there is no comma before any "then" ("If X (45+) then Y"). The
# split is at the FIRST "then": a consequent often goes on with "and then".
FORM_NO_COMMA = re.compile(
    r"^\s*If\s+(.*?)\s+then,?\s+(.*?)\s*\.?\s*$", re.IGNORECASE | re.DOTALL
)


def split_sentence(text: str) -> tuple[str, str] | None:
    """The antecedent and the consequent of "If X, then Y.", or None."""
    if m := FORM.match(text):
        antecedent, consequent = m.group(1).strip() + m.group(2), m.group(3).strip()
    elif m := FORM_NO_COMMA.match(text):
        antecedent, consequent = m.group(1).strip(), m.group(2).strip()
    else:
        return None
    return (antecedent, consequent) if antecedent and consequent else None


ANOTHER_VERSION = "another version"
ANOTHER_MODEL = "another model"
UNPARSED = "unparsed"
OUTSIDE_PAPERS = "outside the paper set"
COLUMNS = [
    "row", "item_id", "paper_id", "chunk_id", "prompt_id", "model_id",
    "text", "antecedent", "consequent", "antecedent_key", "consequent_key",
]  # fmt: skip


def normalize_part(text: str) -> str:
    """Key used to deduplicate parts before embedding; the display text is kept as-is."""
    return re.sub(r"\s+", " ", text).strip().rstrip(".").casefold()


def is_conditional(items: pd.DataFrame) -> pd.Series:
    prompt_name = items["prompt_name"].fillna("")
    prompt_id = items["prompt_id"].fillna("")
    return prompt_name.isin(CONDITIONAL_PROMPTS) | prompt_id.str.startswith(
        CONDITIONAL_PROMPTS
    )


def select(
    items: pd.DataFrame,
    versions: list[str] | None,
    models: list[str] | None,
    papers: set[str] | None = None,
) -> tuple[pd.DataFrame, pd.Series]:
    """Pick the conditionals to cluster and say why each other one is left out.

    ``versions``/``models``/``papers`` set to ``None`` accept everything. Each
    conditional item gets one reason, the first that applies, in the order
    paper set, version, model, form. Returns the parsed rows (``row`` is the
    index into ``items``) and the reason per conditional item ("" = kept).
    """
    conditional = items.loc[is_conditional(items)].copy()
    conditional["row"] = conditional.index
    reason = pd.Series("", index=conditional.index, dtype=object)

    def leave_out(mask: pd.Series, why: str) -> None:
        reason[(reason == "") & mask] = why

    if papers is not None:
        leave_out(~conditional["paper_id"].isin(papers), OUTSIDE_PAPERS)
    if versions is not None:
        leave_out(~conditional["prompt_id"].isin(versions), ANOTHER_VERSION)
    if models is not None:
        leave_out(~conditional["model_id"].isin(models), ANOTHER_MODEL)

    parsed = conditional["text"].fillna("").map(split_sentence)
    ok = parsed.notna()
    conditional["antecedent"] = parsed.map(lambda p: p[0] if p else None)
    conditional["consequent"] = parsed.map(lambda p: p[1] if p else None)
    leave_out(~ok, UNPARSED)

    good = conditional.loc[reason == ""].copy()
    good["antecedent_key"] = good["antecedent"].map(normalize_part)
    good["consequent_key"] = good["consequent"].map(normalize_part)
    return good[COLUMNS].reset_index(drop=True), reason


def summarise(items: pd.DataFrame, good: pd.DataFrame, reason: pd.Series) -> dict:
    conditional = items.loc[reason.index]
    left_out = reason[reason != ""]
    by_version_model = (
        conditional.assign(kept=reason == "")
        .groupby(["prompt_id", "model_id"], dropna=False)
        .agg(items=("kept", "size"), kept=("kept", "sum"))
        .reset_index()
    )
    both = set(good["antecedent_key"]) & set(good["consequent_key"])
    return {
        "items_in_dump": len(items),
        "conditional_items": len(conditional),
        "parsed_conditionals": len(good),
        "left_out": {
            why: int((left_out == why).sum()) for why in sorted(set(left_out))
        },
        "unparsed_examples": conditional.loc[reason == UNPARSED, "text"]
        .head(8)
        .tolist(),
        "distinct_antecedents": int(good["antecedent_key"].nunique()),
        "distinct_consequents": int(good["consequent_key"].nunique()),
        "distinct_halves": int(
            pd.concat([good["antecedent_key"], good["consequent_key"]]).nunique()
        ),
        "halves_in_both_roles": len(both),
        "papers": int(good["paper_id"].nunique()),
        "chunks": int(good["chunk_id"].nunique()),
        "by_version_and_model": [
            {
                k: (int(v) if isinstance(v, (int, np.integer)) else v)
                for k, v in row.items()
            }
            for row in by_version_model.to_dict("records")
        ],
    }


def resolve(
    values: list[str], default: list[str], everything: dict[str, list[str] | None]
) -> list[str] | None:
    if not values:
        return default
    if len(values) == 1 and values[0] in everything:
        return everything[values[0]]
    return values


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=DEFAULT_DIR,
        help="where this selection's outputs go",
    )
    parser.add_argument(
        "--dump-dir", type=Path, default=None, help="where the dump is (default: --dir)"
    )
    parser.add_argument(
        "--versions",
        nargs="*",
        default=[],
        help="prompt ids to keep; 'declared' keeps every declared version, 'all' any version "
        "(default: the current declared version)",
    )
    parser.add_argument(
        "--models",
        nargs="*",
        default=[],
        help="model ids to keep; 'all' keeps every model (default: the declared default)",
    )
    parser.add_argument(
        "--discovered-before",
        default=None,
        help="keep only papers discovered before this date (needs papers.parquet), e.g. to rebuild an earlier run",
    )
    args = parser.parse_args()
    dump_dir = args.dump_dir or args.dir

    versions = resolve(
        args.versions,
        [declared.current_version()],
        {"declared": declared.declared_versions(), "all": None},
    )
    models = resolve(args.models, [declared.default_model()], {"all": None})

    items = pd.read_parquet(dump_dir / f"{ITEMS}.meta.parquet")
    papers_path = dump_dir / "papers.parquet"
    titles = pd.read_parquet(papers_path) if papers_path.exists() else None
    papers = None
    if args.discovered_before:
        if titles is None:
            sys.exit(f"--discovered-before needs {papers_path} (see paper_titles.py)")
        cutoff = pd.Timestamp(args.discovered_before)
        papers = set(
            titles.loc[pd.to_datetime(titles["discovered_at"]) < cutoff, "paper_id"]
        )

    good, reason = select(items, versions, models, papers)
    if titles is not None:
        good = good.merge(titles[["paper_id", "title"]], on="paper_id", how="left")
    else:
        good["title"] = None
    good["title"] = good["title"].fillna(good["paper_id"].str[:12])

    summary = {
        "versions": versions or "all",
        "models": models or "all",
        "discovered_before": args.discovered_before,
    } | summarise(items, good, reason)

    print(f"versions kept:            {', '.join(versions) if versions else 'all'}")
    print(f"models kept:              {', '.join(models) if models else 'all'}")
    if papers is not None:
        print(f"papers discovered before {args.discovered_before}: {len(papers)}")
    print(f"items in dump:            {summary['items_in_dump']}")
    print(f"conditional items:        {summary['conditional_items']}")
    for why, n in summary["left_out"].items():
        print(f"  left out, {why + ':':<22}{n}")
    print(f"parsed conditionals:      {summary['parsed_conditionals']}")
    for text in summary["unparsed_examples"]:
        print(f"    unparsed: {text[:140]}")
    print(f"distinct antecedents:     {summary['distinct_antecedents']}")
    print(f"distinct consequents:     {summary['distinct_consequents']}")
    print(
        f"distinct halves:          {summary['distinct_halves']} ({summary['halves_in_both_roles']} in both roles)"
    )
    print(f"papers:                   {summary['papers']} ({summary['chunks']} chunks)")

    args.dir.mkdir(parents=True, exist_ok=True)
    vectors = np.load(dump_dir / f"{ITEMS}.vectors.npy", mmap_mode="r")
    np.save(
        args.dir / "sentences.vectors.npy",
        np.asarray(vectors[good["row"].to_numpy()], dtype=np.float32),
    )
    good.to_parquet(args.dir / "conditionals.parquet", index=False)
    (args.dir / "selection.json").write_text(json.dumps(summary, indent=2, default=str))
    print(
        f"wrote conditionals.parquet, sentences.vectors.npy and selection.json to {args.dir}"
    )


if __name__ == "__main__":
    main()
