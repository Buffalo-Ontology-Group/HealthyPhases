"""Exact offline token counts for the pipeline's calls, from a production sample.

Input: the JSON lines ``collect_prod.py sample`` printed (chunk text plus each
current prompt's stored answers and items). For every sampled chunk and every
current prompt it renders the template exactly as a run does
(``PromptTemplate.rendered_for``) and counts:

* input tokens: the rendered prompt plus the tool definition sent with it
  (``ExtractionToolSchema(output_key).tool`` as JSON), in ``o200k_base``;
* output tokens: the stored answer (the validated tool arguments), ``o200k_base``;
* embedding tokens: the chunk text and every item text, in ``cl100k_base``
  (the tokenizer of ``text-embedding-3-*``).

``o200k_base`` is the OpenAI tokenizer for the GPT-4.1 and GPT-5 families.
Other vendors count differently and wrap tool definitions in their own
preamble; the paid calibration measures each model's billed tokens against
these same counts, and the cost model scales by that ratio.

Run from ``abi-phases/`` in the project environment (it imports
``phases_v2``)::

    uv run python scripts/pmc_ingestion_cost/count_tokens.py \\
        storage/spikes/pmc_ingestion_cost/prod_sample.jsonl \\
        > scripts/pmc_ingestion_cost/measurements/offline_tokens.json
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import tiktoken

from phases_v2.extraction.adapters.secondary.extraction_tool_schema import (
    ExtractionToolSchema,
)
from phases_v2.prompts.templates import current_prompts


def tokens(encoding, text: str) -> int:
    """Token count, treating special-token text in a paper as plain text."""
    return len(encoding.encode(text, disallowed_special=()))


def describe(values: list[float]) -> dict:
    """Mean, median, p10 and p90 of ``values``."""
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    deciles = statistics.quantiles(ordered, n=10) if len(ordered) > 1 else ordered * 9
    return {
        "n": len(ordered),
        "mean": statistics.fmean(ordered),
        "median": statistics.median(ordered),
        "p10": deciles[0],
        "p90": deciles[-1],
    }


def count(path: Path) -> dict:
    o200k = tiktoken.get_encoding("o200k_base")
    cl100k = tiktoken.get_encoding("cl100k_base")
    prompts = {p.name: p for p in current_prompts()}
    tools = {
        name: json.dumps(ExtractionToolSchema(p.output_key).tool, separators=(",", ":"))
        for name, p in prompts.items()
    }

    chunks = [json.loads(line) for line in path.read_text().splitlines() if line]
    drawn = [c for c in chunks if c.get("drawn", "random") == "random"]
    rendered: dict[str, list[int]] = defaultdict(list)
    chunk_o200k, chunk_cl100k, chunk_chars = [], [], []
    # Per (model, prompt): over the random draw only, so a model's figures are
    # not skewed toward the chunks a second model happened to be tried on.
    output: dict[tuple[str, str], list[int]] = defaultdict(list)
    items: dict[tuple[str, str], list[int]] = defaultdict(list)
    item_tokens: dict[tuple[str, str], list[int]] = defaultdict(list)
    # Per (model, prompt): over chunks every sampled model answered, to compare
    # models on the same text.
    matched_output: dict[tuple[str, str], list[int]] = defaultdict(list)
    matched_items: dict[tuple[str, str], list[int]] = defaultdict(list)
    matched_item_tokens: dict[tuple[str, str], list[int]] = defaultdict(list)
    models = sorted({a["model_id"] for c in chunks for a in c["answers"]})

    for chunk in drawn:
        text = chunk["text"]
        chunk_chars.append(len(text))
        chunk_o200k.append(tokens(o200k, text))
        chunk_cl100k.append(tokens(cl100k, text))
        for name, prompt in prompts.items():
            rendered[name].append(tokens(o200k, prompt.rendered_for(text)))
        for answer in chunk["answers"]:
            key = (answer["model_id"], answer["prompt"])
            output[key].append(tokens(o200k, answer["answer"] or ""))
            items[key].append(answer["item_count"] or 0)
            item_tokens[key].extend(tokens(cl100k, t) for t in answer["items"])

    for chunk in chunks:
        by_prompt: dict[str, list[dict]] = defaultdict(list)
        for answer in chunk["answers"]:
            by_prompt[answer["prompt"]].append(answer)
        for prompt, answers in by_prompt.items():
            if {a["model_id"] for a in answers} != set(models):
                continue
            for answer in answers:
                key = (answer["model_id"], prompt)
                matched_output[key].append(tokens(o200k, answer["answer"] or ""))
                matched_items[key].append(answer["item_count"] or 0)
                matched_item_tokens[key].extend(
                    tokens(cl100k, t) for t in answer["items"]
                )

    per_prompt = {}
    for name, prompt in prompts.items():
        template_tokens = tokens(o200k, prompt.template.replace("{chunk_text}", ""))
        tool_tokens = tokens(o200k, tools[name])
        prompt_tokens = describe(rendered[name])
        per_prompt[name] = {
            "prompt_id": prompt.prompt_id,
            "output_key": prompt.output_key,
            "template_tokens": template_tokens,
            "tool_schema_tokens": tool_tokens,
            "rendered_prompt_tokens": prompt_tokens,
            "input_tokens_mean": prompt_tokens["mean"] + tool_tokens,
        }

    answers = defaultdict(dict)
    for (model_id, prompt), values in sorted(output.items()):
        answers[model_id][prompt] = {
            "chunks": len(values),
            "output_tokens": describe(values),
            "items_per_chunk": statistics.fmean(items[(model_id, prompt)]),
            "item_embedding_tokens": describe(item_tokens[(model_id, prompt)]),
        }
    matched = defaultdict(dict)
    for (model_id, prompt), values in sorted(matched_output.items()):
        matched[model_id][prompt] = {
            "chunks": len(values),
            "output_tokens_mean": statistics.fmean(values),
            "items_per_chunk": statistics.fmean(matched_items[(model_id, prompt)]),
            "item_embedding_tokens_mean": statistics.fmean(
                matched_item_tokens[(model_id, prompt)] or [0]
            ),
        }

    return {
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": "prod sample: collect_prod.py sample 400 855",
        "tokenizers": {"model_calls": "o200k_base", "embeddings": "cl100k_base"},
        "chunks": len(drawn),
        "matched_chunks_models": models,
        "chunk": {
            "chars": describe(chunk_chars),
            "o200k_tokens": describe(chunk_o200k),
            "embedding_tokens": describe(chunk_cl100k),
        },
        "prompts": per_prompt,
        "answers": answers,
        "answers_matched_chunks": matched,
    }


def main(argv: list[str]) -> None:
    if len(argv) != 1:
        raise SystemExit("usage: count_tokens.py <prod_sample.jsonl>")
    json.dump(count(Path(argv[0])), sys.stdout, indent=1)


if __name__ == "__main__":
    main(sys.argv[1:])
