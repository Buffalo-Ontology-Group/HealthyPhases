"""Tests for the pure logic of the term-clustering scripts.

Run with ``make test-terms`` (see the README for the plain command).
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import declared  # noqa: E402
from cluster import (  # noqa: E402
    aggregate_edges,
    class_tfidf,
    cluster_papers,
    handle,
    paper_spread,
    part_papers,
)
from compare_runs import (  # noqa: E402
    best_matches,
    classify,
    shared_overlap,
    with_fan_in,
)
from embed_parts import distinct_parts  # noqa: E402
from hierarchy import build_tree, merge_groups, relabel  # noqa: E402
from report import DOMAIN, MEASUREMENT, METHODOLOGICAL, term_kind  # noqa: E402
from split_conditionals import (  # noqa: E402
    ANOTHER_MODEL,
    ANOTHER_VERSION,
    OUTSIDE_PAPERS,
    UNPARSED,
    normalize_part,
    select,
    split_sentence,
    summarise,
)
from stability import compare, mapped_agreement  # noqa: E402

# ---- the split ----


def test_split_at_last_then_keeps_commas_in_the_antecedent():
    assert split_sentence("If A, B and C, then D.") == ("A, B and C", "D")


def test_split_accepts_dash_and_comma_after_then():
    assert split_sentence("If an ending is near—then goals shift.") == (
        "an ending is near",
        "goals shift",
    )
    assert split_sentence(
        "If parents overprotect, then, in combination, risk rises."
    ) == (
        "parents overprotect",
        "in combination, risk rises",
    )


def test_split_is_case_insensitive_and_spans_lines():
    assert split_sentence(
        "if older adults\nlive alone, THEN they report more loneliness."
    ) == (
        "older adults\nlive alone",
        "they report more loneliness",
    )


def test_split_keeps_a_closing_quote_that_holds_the_comma():
    assert split_sentence(
        "If adults feel 'emancipated innocence,' then they transcend conventions."
    ) == (
        "adults feel 'emancipated innocence'",
        "they transcend conventions",
    )


def test_split_without_a_comma_cuts_at_the_first_then():
    assert split_sentence(
        "If firefighters are older (45+) then they first rest and then work."
    ) == (
        "firefighters are older (45+)",
        "they first rest and then work",
    )


def test_split_prefers_the_comma_over_a_later_and_then():
    assert split_sentence(
        "If children meet novelty, then children first explore and then play."
    ) == (
        "children meet novelty",
        "children first explore and then play",
    )


def test_split_rejects_sentences_not_in_the_form():
    assert split_sentence("Solitude allows reflection.") is None
    assert split_sentence("When adults are alone, then they rest.") is None
    assert (
        split_sentence("If silence occurs in a context, power determines its meaning.")
        is None
    )
    assert split_sentence("If A, then .") is None


def test_normalize_part_folds_case_spacing_and_trailing_period():
    assert normalize_part("  Older  Adults. ") == "older adults"


# ---- the selection ----

CURRENT, OLDER = "conditional_statement_new", "conditional_statement_old"
MINI, OTHER = "provider/model-a", "provider/model-b"


def _items(*rows):
    columns = [
        "item_id",
        "paper_id",
        "chunk_id",
        "prompt_id",
        "prompt_name",
        "model_id",
        "text",
    ]
    return pd.DataFrame([dict(zip(columns, r)) for r in rows])


ITEMS = _items(
    ("1", "p1", "c1", CURRENT, "conditional_statement", MINI, "If a, then b."),
    ("2", "p2", "c2", CURRENT, "conditional_statement", MINI, "If A , then c."),
    ("3", "p1", "c1", OLDER, "conditional_statement", MINI, "If a, then b."),
    ("4", "p1", "c1", CURRENT, "conditional_statement", OTHER, "If a, then b."),
    ("5", "p2", "c2", CURRENT, "conditional_statement", MINI, "Not a conditional."),
    ("6", "p2", "c2", "solitude_how_x", "solitude_how", MINI, "Solitude helps."),
    (
        "7",
        "p3",
        "c3",
        "logical_sentences_y",
        "logical_sentences",
        OTHER,
        "If d, then e.",
    ),
)


def test_selection_keeps_the_given_version_and_model_and_says_why_others_are_out():
    good, reason = select(ITEMS, [CURRENT], [MINI])
    assert list(good["item_id"]) == ["1", "2"]
    assert list(good["row"]) == [0, 1]
    assert dict(zip(ITEMS.loc[reason.index, "item_id"], reason)) == {
        "1": "", "2": "", "3": ANOTHER_VERSION, "4": ANOTHER_MODEL, "5": UNPARSED, "7": ANOTHER_VERSION,
    }  # fmt: skip


def test_selection_ignores_other_prompts_and_accepts_the_old_prompt_name():
    good, reason = select(ITEMS, None, None)
    assert "6" not in set(ITEMS.loc[reason.index, "item_id"])
    assert set(good["item_id"]) == {"1", "2", "3", "4", "7"}


def test_selection_gives_one_reason_per_item_in_a_fixed_order():
    # item 7 is another version, another model and outside the papers: the paper set wins.
    _, reason = select(ITEMS, [CURRENT], [MINI], papers={"p1", "p2"})
    assert reason[6] == OUTSIDE_PAPERS
    assert reason[3] == ANOTHER_MODEL


def test_selection_summary_counts_distinct_halves_and_papers():
    good, reason = select(ITEMS, [CURRENT], [MINI])
    s = summarise(ITEMS, good, reason)
    assert s["conditional_items"] == 6
    assert s["parsed_conditionals"] == 2
    assert s["left_out"] == {ANOTHER_MODEL: 1, ANOTHER_VERSION: 2, UNPARSED: 1}
    assert s["distinct_antecedents"] == 1  # "a" and "A " share a key
    assert s["distinct_consequents"] == 2
    assert s["papers"] == 2
    kept = {
        (r["prompt_id"], r["model_id"]): r["kept"] for r in s["by_version_and_model"]
    }
    assert kept[(CURRENT, MINI)] == 2 and kept[(OLDER, MINI)] == 0


def test_the_default_version_is_the_first_declared_and_resolved_from_the_code():
    versions = declared.declared_versions()
    assert declared.current_version() == versions[0]
    assert all(v.startswith(f"{declared.CONDITIONAL_PROMPT}_") for v in versions)
    assert len(set(versions)) == len(versions) > 1
    assert declared.default_model().count("/") == 1


def test_module_constant_reads_annotated_and_plain_assignments(tmp_path):
    source = tmp_path / "m.py"
    source.write_text("A = ('x', 1)\nB: str = 'y'\ndef f():\n    C = 2\n")
    assert declared.module_constant(source, "A") == ("x", 1)
    assert declared.module_constant(source, "B") == "y"
    with pytest.raises(LookupError):
        declared.module_constant(source, "C")


# ---- parts ----


def test_distinct_parts_counts_roles_and_papers_per_half():
    conditionals = pd.DataFrame({
        "antecedent_key": ["a", "a", "b"], "antecedent": ["A", "a", "b"],
        "consequent_key": ["b", "c", "c"], "consequent": ["b", "c", "c"],
        "paper_id": ["p1", "p2", "p1"],
    })  # fmt: skip
    parts = distinct_parts(conditionals).set_index("key")
    assert parts.loc["a", ["as_antecedent", "as_consequent", "papers"]].tolist() == [
        2,
        0,
        2,
    ]
    assert parts.loc["b", ["as_antecedent", "as_consequent", "papers"]].tolist() == [
        1,
        1,
        1,
    ]
    assert parts.loc["c", ["as_antecedent", "as_consequent", "papers"]].tolist() == [
        0,
        2,
        2,
    ]
    assert parts.loc["a", "text"] == "A"


# ---- keywords ----


def test_class_tfidf_ranks_words_specific_to_a_cluster():
    texts = [
        "older adults feel lonely", "older adults report loneliness", "lonely older adults",
        "children play outside", "children play with peers", "outdoor play for children",
        "unclustered remark about weather",
    ]  # fmt: skip
    labels = np.array([0, 0, 0, 1, 1, 1, -1])
    kw = class_tfidf(texts, labels, top=3)
    assert set(kw) == {0, 1}
    assert "older adults" in kw[0] or "older" in kw[0]
    assert "children" in kw[1]
    assert "weather" not in kw[0] + kw[1]


def test_class_tfidf_drops_stop_words_and_hedges():
    texts = [
        "they may tend to rest",
        "they might tend to rest",
        "work hard",
        "work long",
    ]
    kw = class_tfidf(texts, np.array([0, 0, 1, 1]))
    assert not {"may", "might", "tend", "they", "to"} & set(kw[0])


def test_class_tfidf_without_clusters_is_empty():
    assert class_tfidf(["a b"], np.array([-1])) == {}


def test_handle_skips_words_a_bigram_already_shows():
    assert (
        handle(["older adults", "older", "adults", "loneliness", "living"])
        == "older adults / loneliness / living"
    )


# ---- merges and hierarchy ----


def test_merge_groups_is_transitive_and_keeps_the_smallest_id():
    # three centroids: 0 ~ 1, 1 ~ 2, 0 not ~ 2 directly; 3 is far from all
    C = np.array([[1.0, 0.0], [0.96, 0.28], [0.85, 0.53], [0.0, 1.0]])
    C = C / np.linalg.norm(C, axis=1, keepdims=True)
    assert C[0] @ C[2] < 0.95
    group = merge_groups([10, 11, 12, 13], C, threshold=0.95)
    assert group[10] == group[11] == group[12] == 10
    assert group[13] == 13


def test_merge_groups_with_a_strict_threshold_merges_nothing():
    assert merge_groups([1, 2, 3], np.eye(3), threshold=0.99) == {1: 1, 2: 2, 3: 3}


def test_relabel_moves_points_to_their_group_and_keeps_noise():
    assert relabel(np.array([3, 4, -1, 5]), {3: 3, 4: 3, 5: 5}).tolist() == [
        3,
        3,
        -1,
        5,
    ]


def test_build_tree_groups_close_terms_under_one_parent():
    unit = np.array(
        [[1.0, 0.0, 0.0], [0.99, 0.14, 0.0], [0.0, 0.0, 1.0], [0.0, 0.14, 0.99]]
    )
    unit = unit / np.linalg.norm(unit, axis=1, keepdims=True)
    labels = np.array([0, 1, 2, 3])
    texts = ["alone time", "time alone", "peer play", "play peers"]
    rows = build_tree(unit, labels, texts, [0.25])
    parents = [r for r in rows if r["level"] == 1]
    assert sorted(sorted(r["clusters"]) for r in parents) == [[0, 1], [2, 3]]
    leaves = {r["clusters"][0]: r["parent"] for r in rows if r["level"] == 0}
    assert leaves[0] == leaves[1] != leaves[2] == leaves[3]


# ---- the term graph ----


def _conditionals():
    return pd.DataFrame({
        "antecedent_key": ["alone", "solitude", "alone", "alone", "noise", "alone"],
        "consequent_key": ["calm", "calm", "calm", "lonely", "calm", "alone again"],
        "paper_id": ["p1", "p2", "p1", "p1", "p3", "p4"],
        "title": ["T1", "T2", "T1", "T1", "T3", "T4"],
        "text": [f"If s{i}, then t{i}." for i in range(6)],
    })  # fmt: skip


KEY_TO_LABEL = {
    "alone": 0,
    "solitude": 0,
    "calm": 1,
    "lonely": 2,
    "noise": -1,
    "alone again": 0,
}


def test_edges_aggregate_sentences_and_count_distinct_papers():
    edges = aggregate_edges(_conditionals(), KEY_TO_LABEL)
    first = edges.iloc[0]
    assert (first["src"], first["dst"], first["n"], first["papers"]) == (0, 1, 3, 2)
    assert first["example"] == "If s0, then t0."
    assert (
        len(edges) == 3
    )  # 0->1, 0->2, and the self-loop 0->0; the noise sentence is dropped


def test_edges_flag_self_loops_and_rank_by_papers_before_sentences():
    edges = aggregate_edges(_conditionals(), KEY_TO_LABEL)
    loop = edges[edges["self_loop"]]
    assert loop[["src", "dst", "n"]].values.tolist() == [[0, 0, 1]]
    assert edges["papers"].is_monotonic_decreasing


def test_edges_with_no_clustered_sentence_are_empty():
    edges = aggregate_edges(_conditionals(), {})
    assert edges.empty and "papers" in edges.columns


def test_cluster_papers_counts_each_paper_once_per_cluster():
    pairs = part_papers(_conditionals())
    papers = cluster_papers(pd.Series(KEY_TO_LABEL), pairs)
    assert papers.to_dict() == {0: 3, 1: 3, 2: 1}  # cluster 0: p1, p2, p4


def test_paper_spread_bins():
    assert paper_spread(pd.Series([1, 1, 2, 3, 5, 6, 40])) == {
        "1": 2,
        "2": 1,
        "3-5": 2,
        "6+": 2,
    }


# ---- stability ----


def test_identical_runs_agree_fully_whatever_the_label_names():
    a = np.array([0, 0, 1, 1, -1, 2])
    b = np.array([5, 5, 7, 7, 7, 9])
    assert mapped_agreement(a, b) == 1.0
    scores = compare(a, b)
    assert scores["ari"] == pytest.approx(1.0) and scores[
        "clustered_in_both"
    ] == pytest.approx(5 / 6)


def test_a_split_cluster_maps_fully_one_way_but_not_the_other():
    a = np.array([0, 0, 0, 0])
    b = np.array([1, 1, 2, 2])
    assert mapped_agreement(a, b) == 1.0  # each half of the split maps onto the whole
    assert mapped_agreement(b, a) == 0.5  # the whole maps onto one half


# ---- comparing runs ----


def test_best_matches_and_status_thresholds():
    A = np.array([[1.0, 0.0], [0.0, 1.0]])
    B = np.array([[0.8, 0.6], [1.0, 0.0]])
    best, sims = best_matches(A, B)
    assert best.tolist() == [1, 0] and sims.tolist() == pytest.approx([1.0, 0.6])
    assert [classify(s, 0.9, 0.8) for s in (0.95, 0.85, 0.5)] == [
        "held",
        "near",
        "not found",
    ]


def test_a_near_match_shared_by_several_earlier_terms_is_a_broader_term():
    statuses, fan_in = with_fan_in(
        ["near", "near", "held", "near", "not found"], [7, 7, 7, 8, 9]
    )
    assert statuses == [
        "into a broader term",
        "into a broader term",
        "held",
        "near",
        "not found",
    ]
    assert fan_in == [3, 3, 3, 1, 1]


def test_shared_overlap_uses_only_halves_both_runs_clustered():
    earlier = pd.DataFrame({"id": ["a", "b", "c", "d"], "term": [0, 0, 0, 1]})
    later = pd.DataFrame({"id": ["a", "b", "c", "e"], "term": [5, 6, -1, 5]})
    assert shared_overlap(earlier, later, {0: 5, 1: 5}) == {0: 0.5, 1: None}


# ---- methodological flag ----


def test_methodological_terms_are_flagged_by_their_halves():
    method = [
        "the model fit indices are acceptable",
        "the regression coefficients are significant",
        "CFI exceeds .95",
    ]
    domain = [
        "older adults spend more time alone",
        "they report lower loneliness",
        "the model of solitude holds",
    ]
    assert term_kind(method)[0] == METHODOLOGICAL
    assert term_kind(domain)[0] == DOMAIN
    assert term_kind([])[0] == DOMAIN


def test_qualitative_method_and_design_count_as_methodological():
    texts = [
        "researchers use thematic analysis",
        "the interviews are semi-structured",
        "the findings may not be generalizable",
    ]
    assert term_kind(texts)[0] == METHODOLOGICAL


def test_instruments_and_scores_are_measurement_unless_the_method_dominates():
    measurement = [
        "the UCLA scale item is endorsed",
        "the PHQ-9 score is above 10",
        "older adults feel lonely",
    ]
    kind, method, measure = term_kind(measurement)
    assert kind == MEASUREMENT and measure == pytest.approx(2 / 3) and method == 0
    reliability = [
        "the scale reliability is high",
        "Cronbach's alpha of the subscale is .9",
    ]
    assert term_kind(reliability)[0] == METHODOLOGICAL
