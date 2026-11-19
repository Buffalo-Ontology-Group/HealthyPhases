"""Paid calibration: billed tokens per call, per model and prompt, hard-capped.

Runs *inside* the production API container, where the provider keys live in
``/app/.env``. It writes nothing to any dataset and does not start the engine
(an engine start publishes the module's declarations, which is a dataset
write). The keys are read into memory and never printed. Pipe it in on stdin::

    S=scripts/pmc_ingestion_cost
    PRICES=$(uv run $S/cost_model.py calibration-prices)
    SPENT=$(uv run $S/cost_model.py calibration-spent)
    ssh phases "docker exec -i -e LOG_LEVEL=WARNING -w /app abi-phases-abi-1 \\
        .venv/bin/python - --chunks 10 --seed 855 --cap 0.3 --already-spent $SPENT \\
        --prices '$PRICES'" < $S/calibrate.py > $S/measurements/calibration_<name>.json

``PRICES`` is input, cached input and output USD per 1M tokens per model, from
``prices.yaml``; ``SPENT`` is the spend of every committed calibration file.

Each call goes through the same path as a pipeline run: the chat model is
built as the marketplace's model definition builds it (``ChatOpenAI``,
``temperature=0``, ``timeout=120``, OpenRouter by ``base_url``), bound to the
same forced tool by ``LangchainExtractionModel``, read by the adapter's own
answer reader, and driven by ``extraction.domain.attempt`` with the run's
retry policy and answer budget. Two things differ, both for the budget: each
reply's usage is recorded before its answer is read, and the client's own
retries are off (``max_retries=0``), so every HTTP request is one the ledger
saw; the domain's retry policy does the retrying.

The cap is hard:

* ``--cap`` plus ``--already-spent`` may not exceed 5 USD, so every
  calibration together stays within one 5 USD budget;
* before each request, its worst case is reserved: the request's input (our
  offline count, tool schema included, times 1.5 plus 1,000 tokens for a
  vendor's preamble) at full price, plus ``--max-output`` tokens (passed to
  the provider as its output limit) at the output price. A request whose
  reservation would take the spend past ``--cap`` is not sent;
* a request that fails or times out is charged its worst case, since a
  generation cut off client-side may still be billed;
* a successful one is charged the provider's own ``cost`` where it returns
  one (OpenRouter), or tokens times price, whichever is higher.

A record that fails for any other reason is kept as an error, and a run that
stops early still prints its report, with what it spent.

``--chunks-file`` replaces production's chunks with chunks of random PMC
papers (``sample_lengths.py --chunks-out``), copied into the container's
``/tmp`` first; those have no stored answers to compare items with.

Prints one JSON document: per-call usage (no paper text, no answers), the
stored item counts of the reference model on the same chunks, and the spend.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

CATALOG = "ducklake:postgres:postgresql://abi@postgres:5432/ducklake"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
REFERENCE_MODEL = "openai/gpt-4.1-mini"
#: One budget for every calibration run together, in USD.
BUDGET = 5.0
#: Slack on our input count for a vendor's own tool preamble (Claude's is ~760).
PREAMBLE_TOKENS = 1000

#: model_id -> (route, provider model, extra ChatOpenAI arguments). The first
#: five are the catalog's declarations; the rest are OpenAI's announced
#: replacements, built the way the marketplace builds its OpenAI models.
MODELS: dict[str, tuple[str, str, dict]] = {
    "openai/gpt-4.1-mini": ("openai", "gpt-4.1-mini", {}),
    "openai/gpt-5-mini": ("openai", "gpt-5-mini", {}),
    "openrouter/claude-sonnet-4.6": ("openrouter", "anthropic/claude-sonnet-4.6", {}),
    "openrouter/claude-haiku-4.5": ("openrouter", "anthropic/claude-haiku-4.5", {}),
    "openrouter/gemini-3.1-pro-preview": (
        "openrouter",
        "google/gemini-3.1-pro-preview",
        {},
    ),
    # Through Chat Completions, which LangChain's ChatOpenAI and so the
    # adapter use, OpenAI refuses function tools on these two unless
    # reasoning_effort is "none" (HTTP 400, seen in calibration_probe.json).
    # Each is measured both ways: as the adapter can call it today, and at its
    # default effort over the Responses API.
    "openai/gpt-6-luna@effort-none": (
        "openai",
        "gpt-6-luna",
        {"reasoning_effort": "none"},
    ),
    "openai/gpt-6-luna@responses": (
        "openai",
        "gpt-6-luna",
        {"use_responses_api": True},
    ),
    "openai/gpt-6.1-terra@effort-none": (
        "openai",
        "gpt-6.1-terra",
        {"reasoning_effort": "none"},
    ),
    "openai/gpt-6.1-terra@responses": (
        "openai",
        "gpt-6.1-terra",
        {"use_responses_api": True},
    ),
}
KEYS = {"openai": "OPENAI_API_KEY", "openrouter": "OPENROUTER_API_KEY"}


# --- the budget (pure: no provider, no dataset) ------------------------------


class OverBudget(Exception):
    """The next request's worst case would take the spend past the cap."""


class Ledger:
    """Spend so far plus what in-flight requests have reserved."""

    def __init__(self, cap: float):
        self.cap = cap
        self.spent = 0.0
        self.reserved = 0.0
        self._lock = threading.Lock()

    def reserve(self, amount: float) -> None:
        with self._lock:
            if self.spent + self.reserved + amount > self.cap:
                raise OverBudget(
                    f"spent {self.spent:.4f} + reserved {self.reserved:.4f} "
                    f"+ {amount:.4f} would pass the cap {self.cap:.2f}"
                )
            self.reserved += amount

    def settle(self, reserved: float, cost: float) -> None:
        with self._lock:
            self.reserved -= reserved
            self.spent += cost


def check_cap(cap: float, already_spent: float, budget: float = BUDGET) -> None:
    """Refuse a run that could take every calibration together past ``budget``."""
    if cap < 0 or already_spent < 0:
        raise SystemExit("--cap and --already-spent must not be negative")
    if cap + already_spent > budget + 1e-9:
        raise SystemExit(
            f"--cap {cap:.2f} plus {already_spent:.2f} already spent would pass the "
            f"{budget:.2f} USD calibration budget; at most {budget - already_spent:.2f} is left"
        )


def worst_case_usd(offline_input_tokens: int, max_output: int, price: dict) -> float:
    """The most one request can cost: its input with slack, and a full output."""
    input_tokens = offline_input_tokens * 1.5 + PREAMBLE_TOKENS
    return (input_tokens * price["input"] + max_output * price["output"]) / 1e6


def usage_of(response) -> dict:
    """Billed usage from one provider reply, as LangChain and the raw API report it."""
    meta = getattr(response, "usage_metadata", None) or {}
    raw = (getattr(response, "response_metadata", None) or {}).get("token_usage") or {}
    details_in = meta.get("input_token_details") or {}
    details_out = meta.get("output_token_details") or {}
    raw_in = raw.get("prompt_tokens_details") or {}
    raw_out = raw.get("completion_tokens_details") or {}
    return {
        "input_tokens": meta.get("input_tokens", raw.get("prompt_tokens", 0)),
        "cached_input_tokens": details_in.get(
            "cache_read", raw_in.get("cached_tokens", 0)
        )
        or 0,
        "output_tokens": meta.get("output_tokens", raw.get("completion_tokens", 0)),
        "reasoning_tokens": details_out.get(
            "reasoning", raw_out.get("reasoning_tokens", 0)
        )
        or 0,
        "provider_cost_usd": raw.get("cost"),
        "finish_reason": (getattr(response, "response_metadata", None) or {}).get(
            "finish_reason"
        ),
    }


def priced(usage: dict, price: dict) -> float:
    cached = usage["cached_input_tokens"] or 0
    fresh = max(usage["input_tokens"] - cached, 0)
    return (
        fresh * price["input"]
        + cached * (price.get("cached_input") or price["input"])
        + usage["output_tokens"] * price["output"]
    ) / 1e6


def metered_call(
    invoke: Callable[[], Any],
    *,
    ledger: Ledger,
    worst_case: float,
    price: dict,
    calls: list[dict],
    clock: Callable[[], float] = time.monotonic,
) -> Any:
    """Send one request under the budget and record what it cost.

    Reserves ``worst_case`` first (raising :class:`OverBudget` without sending
    anything), then settles at the reply's cost, or at ``worst_case`` when the
    request fails or times out: a cut-off generation may still be billed.
    """
    ledger.reserve(worst_case)
    started = clock()
    try:
        response = invoke()
    except Exception as failure:
        ledger.settle(worst_case, worst_case)
        calls.append(
            {
                "ok": False,
                "error": type(failure).__name__,
                "charged_usd": worst_case,
                "seconds": clock() - started,
            }
        )
        raise
    usage = usage_of(response)
    computed = priced(usage, price)
    cost = max(computed, usage["provider_cost_usd"] or 0.0)
    ledger.settle(worst_case, cost)
    calls.append(
        {
            "ok": True,
            **usage,
            "computed_cost_usd": computed,
            "charged_usd": cost,
            "seconds": clock() - started,
        }
    )
    return response


# --- the provider path (needs the API container's environment) ---------------


def chat_model(
    route: str, provider_model: str, extra: dict, keys: dict, max_output: int
):
    from langchain_openai import ChatOpenAI
    from pydantic import SecretStr

    # max_retries=0: the domain's RetryPolicy retries, through the ledger.
    common = dict(model=provider_model, timeout=120, max_retries=0, **extra)
    # The marketplace sets temperature=0 for every model; a reasoning model
    # that refuses it is retried without it (see ``temperature_dropped``).
    if route == "openrouter":
        return ChatOpenAI(
            **common,
            temperature=0,
            max_tokens=max_output,
            api_key=SecretStr(keys[KEYS[route]]),
            base_url=OPENROUTER_BASE_URL,
            extra_body={"usage": {"include": True}},
        )
    return ChatOpenAI(
        **common,
        temperature=0,
        max_completion_tokens=max_output,
        api_key=SecretStr(keys[KEYS[route]]),
    )


class MeteredModel:
    """The extraction adapter, with every request metered by :func:`metered_call`.

    Binding, tool schema and answer reading are ``LangchainExtractionModel``'s
    own; only the request itself goes through the ledger.
    """

    def __init__(self, chat, output_key, *, ledger, price, worst_case, calls):
        from phases_v2.extraction.adapters.secondary.LangchainExtractionModel import (
            LangchainExtractionModel,
        )

        self._adapter = LangchainExtractionModel(chat, output_key)
        self._ledger = ledger
        self._price = price
        self._worst_case = worst_case
        self._calls = calls

    def complete(self, prompt: str) -> str:
        from phases_v2.extraction.adapters.secondary.LangchainExtractionModel import (
            _read_answer,
            is_transient,
        )
        from phases_v2.extraction.interfaces import ModelFailed

        adapter = self._adapter
        try:
            response = metered_call(
                lambda: adapter._chat_model.invoke(prompt),
                ledger=self._ledger,
                worst_case=self._worst_case,
                price=self._price,
                calls=self._calls,
            )
        except OverBudget:
            raise
        except Exception as failure:  # One provider failure costs one chunk.
            raise ModelFailed(
                f"extraction tool call failed: {failure}",
                transient=is_transient(failure),
            ) from failure
        return _read_answer(response, adapter._schema, adapter._output_key)


def sample_chunks(seed: int, size: int) -> list[dict]:
    """The first ``size`` chunks of the seeded draw ``collect_prod.py sample`` uses."""
    import duckdb

    from phases_v2.prompts.templates import current_prompts

    con = duckdb.connect()
    con.execute("LOAD ducklake")
    con.execute(
        f"ATTACH '{CATALOG}' AS lake (READ_ONLY, DATA_PATH 'storage/datasets/')"
    )
    chunks = con.execute(
        f"""
        SELECT chunk_id, text FROM lake.phases_v2.chunks
        ORDER BY hash(chunk_id || '{int(seed)}') LIMIT {int(size)}
        """
    ).fetchall()
    ids = ", ".join(f"'{c}'" for c, _ in chunks)
    names = {p.prompt_id: p.name for p in current_prompts()}
    reference = {
        (chunk_id, names[prompt_id]): count
        for chunk_id, prompt_id, count in con.execute(
            f"""
            SELECT chunk_id, prompt_id, item_count FROM lake.phases_v2.extractions
            WHERE chunk_id IN ({ids}) AND model_id = '{REFERENCE_MODEL}'
              AND status = 'succeeded'
            """
        ).fetchall()
        if prompt_id in names
    }
    return [
        {
            "chunk_id": chunk_id,
            "text": text,
            "reference_items": {
                name: reference.get((chunk_id, name)) for name in names.values()
            },
        }
        for chunk_id, text in chunks
    ]


def run(args) -> dict:
    import tiktoken
    from dotenv import dotenv_values

    from phases_v2.extraction.adapters.secondary.extraction_tool_schema import (
        ExtractionToolSchema,
    )
    from phases_v2.extraction.domain import ANSWER_ATTEMPTS, RetryPolicy, attempt
    from phases_v2.prompts.templates import current_prompts

    prices = json.loads(args.prices)
    models = args.models or list(MODELS)
    keys = {
        name: value
        for name, value in dotenv_values("/app/.env").items()
        if name in KEYS.values() and value
    }
    missing = sorted({KEYS[MODELS[m][0]] for m in models} - set(keys))
    if missing:
        raise SystemExit(f"no value for {', '.join(missing)} in /app/.env")
    ledger = Ledger(args.cap)
    prompts = list(current_prompts())
    if args.chunks_file:
        # Chunks cut from PMC papers by sample_lengths.py: no stored answers.
        names = [p.name for p in prompts]
        with open(args.chunks_file) as handle:
            drawn = json.load(handle)[: args.chunks]
        chunks = [{**c, "reference_items": dict.fromkeys(names)} for c in drawn]
    else:
        chunks = sample_chunks(args.seed, args.chunks)
    records: list[dict] = []
    stopped = None
    dropped_temperature: set[str] = set()

    o200k = tiktoken.get_encoding("o200k_base")

    def offline_tokens(prompt, chunk: dict) -> int:
        """The same count as count_tokens.py: rendered prompt plus tool, o200k."""
        tool = json.dumps(
            ExtractionToolSchema(prompt.output_key).tool, separators=(",", ":")
        )
        rendered = prompt.rendered_for(chunk["text"])
        return len(o200k.encode(rendered, disallowed_special=())) + len(
            o200k.encode(tool, disallowed_special=())
        )

    def one(model_id: str, prompt, chunk: dict) -> dict:
        calls: list[dict] = []
        record = {
            "model_id": model_id,
            "prompt": prompt.name,
            "chunk_id": chunk["chunk_id"],
            "reference_items": chunk["reference_items"].get(prompt.name),
            "calls": calls,
        }
        try:
            route, provider_model, extra = MODELS[model_id]
            # A variant ("model@how") is billed at its model's price.
            price = prices[model_id.split("@")[0]]
            offline = offline_tokens(prompt, chunk)
            record["offline_input_tokens"] = offline
            chat = chat_model(route, provider_model, dict(extra), keys, args.max_output)
            if model_id in dropped_temperature:
                chat.temperature = None
            model = MeteredModel(
                chat,
                prompt.output_key,
                ledger=ledger,
                price=price,
                worst_case=worst_case_usd(offline, args.max_output, price),
                calls=calls,
            )
            result = attempt(
                model,
                prompt,
                chunk["text"],
                retry=RetryPolicy(),
                answer_attempts=ANSWER_ATTEMPTS,
            )
            record.update(
                status=result.status,
                error=(result.error or "")[:300] or None,
                items=len(result.items),
            )
        except OverBudget as over:
            record.update(status="not_run", error=str(over), items=None)
        except Exception as failure:  # noqa: BLE001 - kept as a record, the run goes on
            record.update(
                status="error",
                error=f"{type(failure).__name__}: {failure}"[:300],
                items=None,
            )
        return record

    # A model that refuses temperature=0 shows it on its first chunk; those
    # calls are kept (marked superseded) and the chunk is asked again without it.
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for chunk in chunks:
                for model_id in models:
                    if stopped:
                        break
                    results = list(pool.map(lambda p: one(model_id, p, chunk), prompts))
                    if (
                        any(
                            "temperature" in (r["error"] or "")
                            and r["status"] != "succeeded"
                            for r in results
                        )
                        and model_id not in dropped_temperature
                    ):
                        dropped_temperature.add(model_id)
                        records.extend(
                            {**r, "superseded": "retried without temperature"}
                            for r in results
                        )
                        results = list(
                            pool.map(lambda p: one(model_id, p, chunk), prompts)
                        )
                    records.extend(results)
                    if any(r["status"] == "not_run" for r in results):
                        stopped = f"cap reached at chunk {chunk['chunk_id']}, model {model_id}"
    except Exception as failure:  # noqa: BLE001 - the spend so far must still be reported
        stopped = f"aborted: {type(failure).__name__}: {failure}"[:300]

    return {
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        # "prod": production's chunks (seeded draw); "pmc_sample": chunks of
        # randomly drawn PMC papers, from --chunks-file.
        "chunk_source": "pmc_sample" if args.chunks_file else "prod",
        "seed": args.seed,
        "chunks_requested": args.chunks,
        "chunks": [c["chunk_id"] for c in chunks],
        "models": models,
        "cap_usd": args.cap,
        "already_spent_usd": args.already_spent,
        "max_output_tokens": args.max_output,
        "spent_usd": round(ledger.spent, 6),
        "stopped": stopped,
        "temperature_dropped": sorted(dropped_temperature),
        "prices_used": {m: prices[m.split("@")[0]] for m in models},
        "records": records,
    }


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chunks", type=int, default=10)
    parser.add_argument("--seed", type=int, default=855)
    parser.add_argument("--cap", type=float, required=True)
    parser.add_argument(
        "--already-spent",
        type=float,
        required=True,
        help="USD spent by every earlier calibration (cost_model.py calibration-spent)",
    )
    parser.add_argument("--max-output", type=int, default=8000)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--models", nargs="*")
    parser.add_argument("--prices", required=True)
    parser.add_argument(
        "--chunks-file",
        help="JSON list of {chunk_id, text} to use instead of production's chunks "
        "(a path inside the container, e.g. under /tmp)",
    )
    args = parser.parse_args(argv)
    check_cap(args.cap, args.already_spent)
    report = run(args)
    json.dump(report, sys.stdout, indent=1)
    print(
        f"spent {report['spent_usd']:.4f} USD of {args.cap:.2f}; "
        f"stopped: {report['stopped']}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main(sys.argv[1:])
