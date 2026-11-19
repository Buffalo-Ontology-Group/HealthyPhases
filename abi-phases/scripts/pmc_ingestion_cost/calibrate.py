"""Paid calibration: billed tokens per call, per model and prompt, hard-capped.

Runs *inside* the production API container, where the provider keys live in
``/app/.env``. It writes nothing to any dataset and does not start the engine
(an engine start publishes the module's declarations, which is a dataset
write). The keys are read into memory and never printed. Pipe it in on stdin::

    ssh phases 'docker exec -i -e LOG_LEVEL=WARNING -w /app abi-phases-abi-1 \\
        .venv/bin/python - --chunks 10 --seed 855 --cap 5 --prices "$PRICES"' \\
        < scripts/pmc_ingestion_cost/calibrate.py \\
        > scripts/pmc_ingestion_cost/measurements/calibration.json

where ``PRICES`` is ``uv run --no-project --with pyyaml
scripts/pmc_ingestion_cost/cost_model.py calibration-prices`` (input, cached
input and output USD per 1M tokens per model, from ``prices.yaml``).

Each call goes through the same path as a pipeline run: the chat model is
built as the marketplace's model definition builds it (``ChatOpenAI``,
``temperature=0``, ``timeout=120``, ``max_retries=3``, OpenRouter by
``base_url``), bound to the same forced tool by ``LangchainExtractionModel``,
and driven by ``extraction.domain.attempt`` with the run's retry policy and
answer budget. The only addition is that each provider reply's usage is
recorded before its answer is read.

Before each call the worst case is reserved: the call's input at full price
plus ``--max-output`` tokens (passed to the provider as its output limit) at
the output price. A call whose reservation would take the spend over ``--cap``
is not made. The spend reported is the providers' own ``cost`` where they
return one (OpenRouter), otherwise tokens times price.

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
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import duckdb
import tiktoken
from dotenv import dotenv_values
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from phases_v2.extraction.adapters.secondary.extraction_tool_schema import (
    ExtractionToolSchema,
)
from phases_v2.extraction.adapters.secondary.LangchainExtractionModel import (
    LangchainExtractionModel,
    _read_answer,
    is_transient,
)
from phases_v2.extraction.domain import ANSWER_ATTEMPTS, RetryPolicy, attempt
from phases_v2.extraction.interfaces import ModelFailed
from phases_v2.prompts.templates import current_prompts

CATALOG = "ducklake:postgres:postgresql://abi@postgres:5432/ducklake"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
REFERENCE_MODEL = "openai/gpt-4.1-mini"

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


class OverBudget(Exception):
    """The next call's worst case would take the spend past the cap."""


class Ledger:
    """Spend so far plus what in-flight calls have reserved."""

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


def chat_model(
    route: str, provider_model: str, extra: dict, keys: dict, max_output: int
):
    common = dict(model=provider_model, timeout=120, max_retries=3, **extra)
    # The marketplace sets temperature=0 for every model; a reasoning model
    # that refuses it is retried without it (see ``temperature_dropped``).
    if route == "openrouter":
        return ChatOpenAI(
            **common,
            temperature=0,
            max_tokens=max_output,
            api_key=SecretStr(keys["OPENROUTER_API_KEY"]),
            base_url=OPENROUTER_BASE_URL,
            extra_body={"usage": {"include": True}},
        )
    return ChatOpenAI(
        **common,
        temperature=0,
        max_completion_tokens=max_output,
        api_key=SecretStr(keys["OPENAI_API_KEY"]),
    )


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


class MeteredModel(LangchainExtractionModel):
    """``LangchainExtractionModel`` that records each reply's usage and cost.

    ``complete`` is the adapter's own, with the reply's usage recorded between
    the call and the reading of its answer, and the worst case reserved first.
    """

    def __init__(self, chat, output_key, *, ledger, price, worst_case, calls):
        super().__init__(chat, output_key)
        self._ledger = ledger
        self._price = price
        self._worst_case = worst_case
        self._calls = calls

    def complete(self, prompt: str) -> str:
        self._ledger.reserve(self._worst_case)
        started = time.monotonic()
        try:
            response = self._chat_model.invoke(prompt)
        except Exception as failure:
            self._ledger.settle(self._worst_case, 0.0)
            self._calls.append(
                {"ok": False, "error": type(failure).__name__, "seconds": 0.0}
            )
            raise ModelFailed(
                f"extraction tool call failed: {failure}",
                transient=is_transient(failure),
            ) from failure
        usage = usage_of(response)
        computed = priced(usage, self._price)
        reported = usage["provider_cost_usd"]
        cost = max(computed, reported or 0.0)
        self._ledger.settle(self._worst_case, cost)
        self._calls.append(
            {
                "ok": True,
                **usage,
                "computed_cost_usd": computed,
                "seconds": time.monotonic() - started,
            }
        )
        return _read_answer(response, self._schema, self._output_key)


def sample_chunks(seed: int, size: int) -> list[dict]:
    """The first ``size`` chunks of the seeded draw ``collect_prod.py sample`` uses."""
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
    keys = {
        name: value
        for name, value in dotenv_values("/app/.env").items()
        if name in ("OPENAI_API_KEY", "OPENROUTER_API_KEY") and value
    }
    prices = json.loads(args.prices)
    models = args.models or list(MODELS)
    ledger = Ledger(args.cap)
    prompts = list(current_prompts())
    if args.chunks_file:
        # Chunks cut from PMC papers by sample_lengths.py: no stored answers.
        names = [p.name for p in prompts]
        drawn = json.loads(open(args.chunks_file).read())[: args.chunks]
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
        return len(o200k.encode(prompt.rendered_for(chunk["text"]))) + len(
            o200k.encode(tool)
        )

    def one(model_id: str, prompt, chunk: dict) -> dict:
        route, provider_model, extra = MODELS[model_id]
        # A variant ("model@how") is billed at its model's price.
        price = prices[model_id.split("@")[0]]
        input_estimate = 1.5 * len(prompt.rendered_for(chunk["text"])) / 3
        worst_case = (
            input_estimate * price["input"] + args.max_output * price["output"]
        ) / 1e6
        calls: list[dict] = []
        params = dict(extra)
        chat = chat_model(route, provider_model, params, keys, args.max_output)
        if model_id in dropped_temperature:
            chat.temperature = None
        model = MeteredModel(
            chat,
            prompt.output_key,
            ledger=ledger,
            price=price,
            worst_case=worst_case,
            calls=calls,
        )
        try:
            result = attempt(
                model,
                prompt,
                chunk["text"],
                retry=RetryPolicy(),
                answer_attempts=ANSWER_ATTEMPTS,
            )
            status, error, items = result.status, result.error, len(result.items)
        except OverBudget as over:
            status, error, items = "not_run", str(over), None
        return {
            "model_id": model_id,
            "prompt": prompt.name,
            "chunk_id": chunk["chunk_id"],
            "status": status,
            "error": (error or "")[:300] or None,
            "items": items,
            "reference_items": chunk["reference_items"].get(prompt.name),
            "offline_input_tokens": offline_tokens(prompt, chunk),
            "calls": calls,
        }

    # A model that refuses temperature=0 shows it on its first chunk; those
    # calls are kept (marked superseded) and the chunk is asked again without it.
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
                    results = list(pool.map(lambda p: one(model_id, p, chunk), prompts))
                records.extend(results)
                if any(r["status"] == "not_run" for r in results):
                    stopped = (
                        f"cap reached at chunk {chunk['chunk_id']}, model {model_id}"
                    )

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
    parser.add_argument("--cap", type=float, default=5.0)
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
    if args.cap > 5.0:
        raise SystemExit("the calibration is capped at 5 USD")
    report = run(args)
    json.dump(report, sys.stdout, indent=1)
    print(
        f"spent {report['spent_usd']:.4f} USD of {args.cap:.2f}; "
        f"stopped: {report['stopped']}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main(sys.argv[1:])
