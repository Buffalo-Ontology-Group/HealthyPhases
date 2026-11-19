"""The cost model: arithmetic on hand-made inputs, and properties of the committed data.

Everything is offline. Run with ``make test-pmc-cost``.
"""

import copy
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cost_model as cm  # noqa: E402


@pytest.fixture(scope="module")
def committed():
    return cm.load()


def profile_with(input_tokens=1000.0, output_tokens=100.0, cached=0.0, calls=1.0):
    prompts = {p: input_tokens for p in cm.PROMPTS}
    return cm.Profile(
        model_id="openai/gpt-4.1-mini",
        input_tokens=prompts,
        cached_input_tokens={p: cached for p in cm.PROMPTS},
        output_tokens={p: output_tokens for p in cm.PROMPTS},
        reasoning_tokens={p: 0.0 for p in cm.PROMPTS},
        calls_per_extraction=calls,
        failed_share=0.0,
        item_ratio=1.0,
        items_per_chunk={p: 1.0 for p in cm.PROMPTS},
        calibrated_chunks=1,
    )


# --- arithmetic --------------------------------------------------------------


def test_call_cost_prices_input_cached_input_and_output_separately():
    price = {"input": 2.0, "cached_input": 0.5, "output": 10.0}
    profile = profile_with(input_tokens=1000, cached=400, output_tokens=100)
    # 600 fresh * 2 + 400 cached * 0.5 + 100 out * 10, per 1M tokens.
    assert cm.call_cost(profile, "solitude_what", price) == pytest.approx(
        (600 * 2 + 400 * 0.5 + 100 * 10) / 1e6
    )


def test_call_cost_counts_retried_calls():
    price = {"input": 1.0, "cached_input": 1.0, "output": 1.0}
    once = cm.call_cost(profile_with(calls=1.0), "solitude_what", price)
    assert cm.call_cost(
        profile_with(calls=1.25), "solitude_what", price
    ) == pytest.approx(once * 1.25)


def test_openrouter_prices_carry_the_credit_fee(committed):
    sonnet = cm.model_price(committed, "openrouter/claude-sonnet-4.6")
    listed = committed.prices["models"]["openrouter/claude-sonnet-4.6"]
    fee = 1 + committed.prices["openrouter"]["fee_percent"] / 100
    assert sonnet["input"] == pytest.approx(listed["input"] * fee)
    assert cm.model_price(committed, "openai/gpt-4.1-mini")["input"] == 0.40


def test_variants_are_priced_as_their_model(committed):
    assert cm.model_price(committed, "openai/gpt-6-luna@responses") == cm.model_price(
        committed, "openai/gpt-6-luna"
    )


def test_triples_follow_the_graph_projection():
    # 2 per paper, 5 per chunk, 5 per extraction, 7 per item.
    assert (
        cm.triples_per_paper(chunks=10, extractions=80, items=300)
        == 2 + 50 + 400 + 2100
    )


def test_s3_storage_uses_the_volume_tiers(committed):
    tiers = committed.prices["aws_us_east_1"]["s3_standard_gb_month"]
    assert cm.s3_month(committed, 10 * cm.GB) == pytest.approx(10 * tiers["first_50tb"])
    assert cm.s3_month(committed, 60_000 * cm.GB) == pytest.approx(
        50_000 * tiers["first_50tb"] + 10_000 * tiers["next_450tb"]
    )


def test_quantization_shrinks_ram_by_the_documented_ratio(committed):
    full = cm.qdrant_ram_per_point(committed, 3072)
    binary = cm.qdrant_ram_per_point(committed, 3072, "binary_1bit")
    sizing = committed.prices["qdrant_sizing"]
    overhead = (
        sizing["hnsw_bytes_per_point"] + sizing["id_tracker_bytes_per_point"]
    ) * (1 + sizing["headroom"])
    assert (full - overhead) / (binary - overhead) == pytest.approx(32)


def test_growth_summarises_the_windows():
    inputs = cm.Inputs(
        prices={},
        assumptions={"corpora": {"oa": {"growth": "open_access"}}},
        entrez={
            "per_30_days": [
                {"open_access": 10},
                {"open_access": 30},
                {"open_access": 20},
            ]
        },
        inventory={},
        metadata={},
        lengths={},
        prod={},
        infra={},
        tokens={},
    )
    assert cm.growth(inputs, "oa") == {
        "last": 10,
        "mean": 20,
        "min": 10,
        "max": 30,
        "windows": 3,
    }


def test_prod_throughput_keeps_only_recent_large_runs():
    inputs = cm.Inputs(
        prices={},
        assumptions={"throughput": {"runs_since": "2026-09-11", "min_calls": 200}},
        entrez={},
        inventory={},
        metadata={},
        lengths={},
        prod={
            "extraction_runs": [
                {
                    "started_at": "2026-09-01T00:00:00",
                    "succeeded": 1000,
                    "failed": 0,
                    "seconds": 1,
                },
                {
                    "started_at": "2026-10-01T00:00:00",
                    "succeeded": 50,
                    "failed": 0,
                    "seconds": 1,
                },
                {
                    "started_at": "2026-10-02T00:00:00",
                    "succeeded": 990,
                    "failed": 10,
                    "seconds": 3600,
                },
                {
                    "started_at": "2026-10-03T00:00:00",
                    "succeeded": 3000,
                    "failed": 0,
                    "seconds": 3600,
                },
            ]
        },
        infra={},
        tokens={},
    )
    result = cm.prod_throughput(inputs)
    assert result["runs"] == 2
    assert result["calls_per_hour"] == pytest.approx(2000)


def test_render_fills_marked_tables_and_leaves_prose(committed):
    report = "Intro.\n<!-- table:per-paper -->\nold\n<!-- /table:per-paper -->\nOutro."
    out = cm.render(committed, report)
    assert out.startswith("Intro.\n<!-- table:per-paper -->\n| Per paper |")
    assert out.endswith("\n<!-- /table:per-paper -->\nOutro.")


def test_render_fills_a_new_empty_block(committed):
    out = cm.render(committed, "<!-- table:per-paper -->\n<!-- /table:per-paper -->")
    assert "| Per paper |" in out
    assert cm.render(committed, out) == out


def test_render_refuses_an_unknown_table(committed):
    with pytest.raises(KeyError):
        cm.render(committed, "<!-- table:nope -->\nx\n<!-- /table:nope -->")


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (1_234_567, "$1.23M"),
        (45_678, "$46k"),
        (123.4, "$123"),
        (5.678, "$5.68"),
        (0.01234, "$0.0123"),
    ],
)
def test_usd_rounds_to_a_readable_precision(value, text):
    assert cm.usd(value) == text


# --- the committed data ------------------------------------------------------


def test_every_model_price_cites_a_source_and_date(committed):
    for model_id, entry in committed.prices["models"].items():
        assert entry["source"].startswith("https://"), model_id
        assert entry["retrieved"], model_id
    for name, entry in committed.prices["embeddings"].items():
        assert entry["source"].startswith("https://"), name


def test_every_headline_model_is_calibrated(committed):
    for model_id in cm.headline_models(committed):
        profile = cm.profile(committed, model_id)
        assert profile.calibrated_chunks >= 5, model_id
        assert all(profile.input_tokens[p] > 0 for p in cm.PROMPTS), model_id


def test_the_headline_covers_every_declared_model_and_the_replacement(committed):
    headline = {cm.price_id(m) for m in cm.headline_models(committed)}
    declared = {m for m, e in committed.prices["models"].items() if e["declared"]}
    assert declared <= headline
    assert (
        committed.prices["deprecations"]["openai/gpt-4.1-mini"]["replacement"]
        in headline
    )


def test_calibration_spend_stays_under_five_dollars(committed):
    assert cm.calibration_spend(committed)["total"] <= 5.0


def test_doubling_a_price_doubles_that_models_calls(committed):
    model = "openai/gpt-4.1-mini"
    before = cm.paper(committed, model).model_calls
    changed = copy.deepcopy(committed)
    for key in ("input", "cached_input", "output"):
        changed.prices["models"][model][key] *= 2
    assert cm.paper(changed, model).model_calls == pytest.approx(2 * before)


def test_a_second_model_adds_its_calls(committed):
    one = cm.bill(committed, "openai/gpt-4.1-mini", "oa")
    other = cm.bill(committed, "openrouter/claude-haiku-4.5", "oa")
    table = cm.t_two_models(committed, "oa")
    pair = one.backfill_calls + one.backfill_embeddings + other.backfill_calls
    pair += cm.paper(
        committed, "openrouter/claude-haiku-4.5"
    ).embeddings_items * cm.corpus_size(committed, "oa")
    assert cm.usd(pair) in table


def test_every_report_block_is_a_filled_known_table():
    blocks = re.findall(
        r"<!-- table:([a-z0-9-]+) -->\n(.*?)<!-- /table:\1 -->",
        cm.REPORT.read_text(),
        re.S,
    )
    assert blocks
    for name, body in blocks:
        assert name in cm.TABLES, name
        assert body.strip().startswith("|"), f"block {name} is empty"
    assert {name for name, _ in blocks} == set(cm.TABLES)


def test_the_report_tables_match_the_committed_data(committed):
    current = cm.REPORT.read_text()
    assert cm.render(committed, current) == current, (
        "PMC_INGESTION_COST.md is stale: run `uv run scripts/pmc_ingestion_cost/cost_model.py report`"
    )
