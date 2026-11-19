"""The calibration's budget, with no provider and no dataset.

Run with ``make test-pmc-cost``.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from calibrate import (  # noqa: E402
    PREAMBLE_TOKENS,
    Ledger,
    OverBudget,
    check_cap,
    metered_call,
    worst_case_usd,
)

PRICE = {"input": 2.0, "cached_input": 0.5, "output": 10.0}


def reply(input_tokens=1000, output_tokens=100, cost=None):
    return SimpleNamespace(
        usage_metadata={"input_tokens": input_tokens, "output_tokens": output_tokens},
        response_metadata={
            "token_usage": {"cost": cost},
            "finish_reason": "tool_calls",
        },
    )


@pytest.mark.parametrize(
    "failure", [TimeoutError("read timed out"), ConnectionError("reset")]
)
def test_a_failed_request_is_charged_its_worst_case(failure):
    ledger = Ledger(cap=1.0)
    calls = []

    def invoke():
        raise failure

    with pytest.raises(type(failure)):
        metered_call(invoke, ledger=ledger, worst_case=0.25, price=PRICE, calls=calls)
    assert ledger.spent == pytest.approx(0.25)
    assert ledger.reserved == pytest.approx(0.0)
    assert calls == [
        {
            "ok": False,
            "error": type(failure).__name__,
            "charged_usd": 0.25,
            "seconds": calls[0]["seconds"],
        }
    ]


def test_a_request_over_budget_is_never_sent():
    ledger = Ledger(cap=0.30)
    ledger.settle(0.0, 0.10)
    sent = []
    with pytest.raises(OverBudget):
        metered_call(
            lambda: sent.append(1),
            ledger=ledger,
            worst_case=0.25,
            price=PRICE,
            calls=[],
        )
    assert sent == []
    assert ledger.spent == pytest.approx(0.10)
    assert ledger.reserved == pytest.approx(0.0)


def test_a_reply_is_charged_the_higher_of_its_computed_and_reported_cost():
    ledger = Ledger(cap=1.0)
    calls = []
    metered_call(
        lambda: reply(1000, 100),
        ledger=ledger,
        worst_case=0.5,
        price=PRICE,
        calls=calls,
    )
    computed = (1000 * 2.0 + 100 * 10.0) / 1e6
    assert ledger.spent == pytest.approx(computed)
    metered_call(
        lambda: reply(1000, 100, cost=0.01),
        ledger=ledger,
        worst_case=0.5,
        price=PRICE,
        calls=calls,
    )
    assert ledger.spent == pytest.approx(computed + 0.01)
    assert ledger.reserved == pytest.approx(0.0)
    assert [c["charged_usd"] for c in calls] == pytest.approx([computed, 0.01])


def test_reservations_count_against_the_cap_while_requests_are_in_flight():
    ledger = Ledger(cap=1.0)
    ledger.reserve(0.6)
    with pytest.raises(OverBudget):
        ledger.reserve(0.5)
    ledger.settle(0.6, 0.1)
    ledger.reserve(0.5)


def test_the_worst_case_covers_the_tool_schema_and_a_vendor_preamble():
    # 1,200 offline tokens already include the tool schema.
    assert worst_case_usd(1200, 8000, PRICE) == pytest.approx(
        ((1200 * 1.5 + PREAMBLE_TOKENS) * 2.0 + 8000 * 10.0) / 1e6
    )


def test_every_run_shares_one_five_dollar_budget():
    check_cap(0.39, 4.61)
    with pytest.raises(SystemExit, match="at most 0.39"):
        check_cap(0.40, 4.61)
    with pytest.raises(SystemExit):
        check_cap(5.01, 0.0)
    with pytest.raises(SystemExit):
        check_cap(-1.0, 0.0)
