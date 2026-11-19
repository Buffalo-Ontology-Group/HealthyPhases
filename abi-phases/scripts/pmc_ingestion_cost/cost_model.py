# /// script
# requires-python = ">=3.12"
# dependencies = ["pyyaml"]
# ///
"""The PMC ingestion cost model: measurements and prices in, report tables out.

Reads only committed files (``prices.yaml``, ``assumptions.yaml`` and
``measurements/*.json``), so it runs offline. Run from ``abi-phases/``::

    S=scripts/pmc_ingestion_cost
    uv run $S/cost_model.py report    # rewrite the generated tables in PMC_INGESTION_COST.md
    uv run $S/cost_model.py check     # exit 1 when the report's tables are stale
    uv run $S/cost_model.py tables    # print the tables
    uv run $S/cost_model.py calibration-prices   # prices JSON for calibrate.py

Every function below the loaders is pure: it takes the loaded inputs and
returns numbers or Markdown, which is what the tests exercise.
"""

from __future__ import annotations

import copy
import json
import math
import random
import re
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
MEASUREMENTS = HERE / "measurements"
REPORT = HERE.parents[1] / "PMC_INGESTION_COST.md"
PROMPTS = (
    "conditional_statement",
    "probabilistic_processes",
    "solitude_causes",
    "solitude_effects",
    "solitude_how",
    "solitude_what",
    "solitude_when",
    "solitude_where",
)
RELATION_PROMPT = "probabilistic_processes"
GIB = 1024**3
GB = 1e9
TB = 1e12


# --- loading -----------------------------------------------------------------


@dataclass
class Inputs:
    prices: dict
    assumptions: dict
    entrez: dict
    inventory: dict
    metadata: dict
    lengths: dict
    prod: dict
    infra: dict
    tokens: dict
    calibration: list[dict] = field(default_factory=list)


def _json(name: str, directory: Path) -> dict:
    return json.loads((directory / name).read_text())


def load(directory: Path = MEASUREMENTS, here: Path = HERE) -> Inputs:
    calibration = [
        {**_json(path.name, directory), "file": path.name}
        for path in sorted(directory.glob("calibration*.json"))
    ]
    return Inputs(
        prices=yaml.safe_load((here / "prices.yaml").read_text()),
        assumptions=yaml.safe_load((here / "assumptions.yaml").read_text()),
        entrez=_json("pmc_entrez.json", directory),
        inventory=_json("pmc_inventory.json", directory),
        metadata=_json("pmc_metadata_sample.json", directory),
        lengths=_json("pmc_lengths.json", directory),
        prod=_json("prod_datasets.json", directory),
        infra=_json("prod_infra.json", directory),
        tokens=_json("offline_tokens.json", directory),
        calibration=calibration,
    )


# --- formatting --------------------------------------------------------------


def usd(value: float | None) -> str:
    """Dollars, rounded to what the precision of the inputs supports."""
    if value is None:
        return "n/a"
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value >= 1e6:
        return f"{sign}${value / 1e6:,.2f}M"
    if value >= 1e4:
        return f"{sign}${value / 1e3:,.0f}k"
    if value >= 100:
        return f"{sign}${value:,.0f}"
    if value >= 1:
        return f"{sign}${value:,.2f}"
    return f"{sign}${value:.4f}"


def count(value: float | None, digits: int = 0) -> str:
    if value is None:
        return "n/a"
    if abs(value) >= 1e9:
        return f"{value / 1e9:,.2f} billion"
    if abs(value) >= 1e6:
        return f"{value / 1e6:,.2f} million"
    return f"{value:,.{digits}f}"


def size(value: float) -> str:
    """Bytes in decimal units."""
    for unit, scale in (
        ("PB", 1e15),
        ("TB", 1e12),
        ("GB", 1e9),
        ("MB", 1e6),
        ("kB", 1e3),
    ):
        if value >= scale:
            return f"{value / scale:,.1f} {unit}"
    return f"{value:,.0f} B"


def duration(hours: float | None) -> str:
    if hours is None:
        return "n/a"
    if hours >= 24 * 365 * 2:
        return f"{hours / (24 * 365):,.0f} years"
    if hours >= 24 * 60:
        return f"{hours / (24 * 30):,.1f} months"
    if hours >= 48:
        return f"{hours / 24:,.0f} days"
    return f"{hours:,.1f} hours"


def table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


# --- corpus ------------------------------------------------------------------


def corpus_size(inputs: Inputs, corpus: str) -> int:
    key = inputs.assumptions["corpora"][corpus]["entrez"]
    return inputs.entrez["totals"][key]


def growth(inputs: Inputs, corpus: str) -> dict:
    """New articles per 30 days by PMC live date: last window, 12-month mean and range."""
    key = inputs.assumptions["corpora"][corpus]["growth"]
    values = [w[key] for w in inputs.entrez["per_30_days"]]
    return {
        "last": values[0],
        "mean": statistics.fmean(values),
        "min": min(values),
        "max": max(values),
        "windows": len(values),
    }


def pdf_share(inputs: Inputs, open_access_only: bool = True) -> float:
    """Share of sampled bucket articles that distribute a PDF."""
    records = inputs.metadata["records"]
    if open_access_only:
        records = [r for r in records if r["open_access"]]
    return sum(r["has_pdf"] for r in records) / len(records)


# --- per-paper profile -------------------------------------------------------


def lengths(inputs: Inputs, source: str | None = None) -> dict:
    source = source or inputs.assumptions["paper_source"]
    return inputs.lengths[source]["summary"]


def chunks_per_paper(inputs: Inputs, source: str | None = None) -> float:
    return lengths(inputs, source)["chunks"]["mean"]


def price_id(model_id: str) -> str:
    return model_id.split("@")[0]


def model_price(inputs: Inputs, model_id: str, batch: bool = False) -> dict:
    """USD per 1M tokens for a model, OpenRouter's fee included."""
    entry = inputs.prices["models"][price_id(model_id)]
    fee = (
        1 + inputs.prices["openrouter"]["fee_percent"] / 100
        if entry["route"] == "openrouter"
        else 1
    )
    if batch:
        return {
            "input": entry["batch_input"] * fee,
            "cached_input": entry["batch_input"] * fee,
            "output": entry["batch_output"] * fee,
        }
    return {
        "input": entry["input"] * fee,
        "cached_input": entry["cached_input"] * fee,
        "output": entry["output"] * fee,
    }


def calibration_records(inputs: Inputs, source: str = "prod") -> list[dict]:
    """A main calibration run's records, without calls a retry replaced.

    ``source`` is the run's ``chunk_source``: "prod" (production's chunks, every
    model on the same ones) or "pmc_sample" (chunks of random PMC papers).
    Smoke tests and probes (a single chunk) are left out.
    """
    runs = [
        c
        for c in inputs.calibration
        if c.get("chunk_source", "prod") == source and c.get("chunks_requested", 0) > 1
    ]
    return [
        r
        for c in runs
        for r in c["records"]
        if not r.get("superseded") and r["status"] in ("succeeded", "failed")
    ]


def calibration_spend(inputs: Inputs) -> dict:
    return {
        "total": sum(c["spent_usd"] for c in inputs.calibration),
        "runs": len(inputs.calibration),
    }


@dataclass
class Profile:
    """One model's billed usage per extraction call, per prompt."""

    model_id: str
    input_tokens: dict[str, float]
    cached_input_tokens: dict[str, float]
    output_tokens: dict[str, float]
    reasoning_tokens: dict[str, float]
    calls_per_extraction: float
    failed_share: float
    item_ratio: float
    items_per_chunk: dict[str, float]
    calibrated_chunks: int
    #: Which calibration the per-call figures come from: "pmc_sample" (the
    #: model ran on chunks of random PMC papers) or "prod" (production's
    #: chunks only, scaled to PMC text through the reference model).
    source: str = "prod"


def _usage(records: list[dict], offline: dict) -> dict[str, dict[str, float]]:
    """Per prompt: billed input (offline count plus measured overhead), cached
    input, output, reasoning and items, means over successful calls."""
    by_prompt: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        by_prompt[record["prompt"]].append(record)
    usage = {}
    for prompt in PROMPTS:
        calls = [
            (call, record)
            for record in by_prompt[prompt]
            for call in record["calls"]
            if call.get("ok")
        ]
        if not calls:
            raise ValueError(f"no successful calibration call for {prompt}")
        answered = [r for r in by_prompt[prompt] if r["status"] == "succeeded"]
        usage[prompt] = {
            "input": offline[prompt]["input_tokens_mean"]
            + statistics.fmean(
                c["input_tokens"] - r["offline_input_tokens"] for c, r in calls
            ),
            "cached": statistics.fmean(c["cached_input_tokens"] or 0 for c, _ in calls),
            "output": statistics.fmean(c["output_tokens"] for c, _ in calls),
            "reasoning": statistics.fmean(c["reasoning_tokens"] or 0 for c, _ in calls),
            "items": statistics.fmean(r["items"] for r in answered)
            if answered
            else 0.0,
        }
    return usage


def profile(inputs: Inputs, model_id: str) -> Profile:
    """Billed tokens and items per call for ``model_id``, for PMC text.

    Input is the offline count of each prompt rendered over 400 production
    chunks, plus this model's measured overhead (billed minus offline count on
    the calibration's own chunks).

    With ``chunk_level: pmc_sample``, a model calibrated on chunks of random PMC
    papers uses that run as measured. A model calibrated only on production's
    chunks (papers chosen for their topic, so richer in what the prompts ask
    for) is scaled through the reference model, which ran on both: its items
    are the reference's PMC items times its item ratio to the reference on
    production's chunks, and its answer text (not its reasoning) shrinks by the
    reference's PMC-to-production output ratio. With ``chunk_level: prod``,
    every model uses production's chunks as measured.
    """
    offline = inputs.tokens["prompts"]
    level = inputs.assumptions["chunk_level"]
    reference = inputs.assumptions["reference_model"]
    prod = [r for r in calibration_records(inputs, "prod") if r["model_id"] == model_id]
    pmc = [
        r
        for r in calibration_records(inputs, "pmc_sample")
        if r["model_id"] == model_id
    ]
    if not prod:
        raise ValueError(f"no calibration records for {model_id}")
    ratio = item_ratio(inputs, model_id)

    if level == "pmc_sample" and pmc:
        records, source = pmc, "pmc_sample"
        usage = _usage(pmc, offline)
        items = {p: usage[p]["items"] for p in PROMPTS}
        output = {p: usage[p]["output"] for p in PROMPTS}
    else:
        records, source = prod, "prod"
        usage = _usage(prod, offline)
        output = {p: usage[p]["output"] for p in PROMPTS}
        items = {p: reference_items(inputs, level)[p] * ratio for p in PROMPTS}
        if level == "pmc_sample":
            ref_prod = _usage(
                [
                    r
                    for r in calibration_records(inputs, "prod")
                    if r["model_id"] == reference
                ],
                offline,
            )
            ref_pmc = _usage(
                [
                    r
                    for r in calibration_records(inputs, "pmc_sample")
                    if r["model_id"] == reference
                ],
                offline,
            )
            for p in PROMPTS:
                shrink = ref_pmc[p]["output"] / ref_prod[p]["output"]
                answer = usage[p]["output"] - usage[p]["reasoning"]
                output[p] = usage[p]["reasoning"] + answer * shrink

    attempts = sum(len(r["calls"]) for r in records)
    return Profile(
        model_id=model_id,
        input_tokens={p: usage[p]["input"] for p in PROMPTS},
        cached_input_tokens={p: usage[p]["cached"] for p in PROMPTS},
        output_tokens=output,
        reasoning_tokens={p: usage[p]["reasoning"] for p in PROMPTS},
        calls_per_extraction=attempts / len(records),
        failed_share=sum(r["status"] == "failed" for r in records) / len(records),
        item_ratio=ratio,
        items_per_chunk=items,
        calibrated_chunks=len({r["chunk_id"] for r in records}),
        source=source,
    )


def reference_items(inputs: Inputs, level: str | None = None) -> dict[str, float]:
    """Items per chunk per prompt of the reference model.

    "pmc_sample": its answers on chunks of random PMC papers (calibration);
    "prod": its stored answers on production's random draw of 400 chunks.
    """
    level = level or inputs.assumptions["chunk_level"]
    model = inputs.assumptions["reference_model"]
    if level == "prod":
        answers = inputs.tokens["answers"][model]
        return {p: answers[p]["items_per_chunk"] for p in PROMPTS}
    records = [
        r
        for r in calibration_records(inputs, "pmc_sample")
        if r["model_id"] == model and r["status"] == "succeeded"
    ]
    if not records:
        raise ValueError("no PMC-sample calibration of the reference model")
    return {
        p: statistics.fmean(r["items"] for r in records if r["prompt"] == p)
        for p in PROMPTS
    }


def item_ratio(inputs: Inputs, model_id: str) -> float:
    """This model's items over the reference model's stored items, same chunks and prompts."""
    if model_id == inputs.assumptions["reference_model"]:
        return 1.0
    records = [
        r
        for r in calibration_records(inputs)
        if r["model_id"] == model_id
        and r["status"] == "succeeded"
        and r["reference_items"] is not None
    ]
    reference = sum(r["reference_items"] for r in records)
    return sum(r["items"] for r in records) / reference if reference else 1.0


def call_cost(profile: Profile, prompt: str, price: dict) -> float:
    """USD for one extraction (retries included) of one prompt on one chunk."""
    cached = min(profile.cached_input_tokens[prompt], profile.input_tokens[prompt])
    fresh = profile.input_tokens[prompt] - cached
    per_call = (
        fresh * price["input"]
        + cached * price["cached_input"]
        + profile.output_tokens[prompt] * price["output"]
    ) / 1e6
    return per_call * profile.calls_per_extraction


def model_cost_per_chunk(
    inputs: Inputs, profile: Profile, prompts=PROMPTS, batch: bool = False
) -> float:
    price = model_price(inputs, profile.model_id, batch)
    return sum(call_cost(profile, p, price) for p in prompts)


def tokens_per_chunk(profile: Profile, prompts=PROMPTS) -> float:
    """Input plus output tokens, retries included: what a tokens-per-minute limit counts."""
    return sum(
        (profile.input_tokens[p] + profile.output_tokens[p])
        * profile.calls_per_extraction
        for p in prompts
    )


def item_embedding_tokens(inputs: Inputs) -> dict[str, float]:
    model = inputs.assumptions["reference_model"]
    answers = inputs.tokens["answers"][model]
    return {p: answers[p]["item_embedding_tokens"]["mean"] for p in PROMPTS}


def chunk_embedding_tokens(inputs: Inputs) -> float:
    return inputs.tokens["chunk"]["embedding_tokens"]["mean"]


def embedding_price(
    inputs: Inputs, model: str | None = None, batch: bool = False
) -> float:
    model = model or inputs.assumptions["embedding"]["model"]
    entry = inputs.prices["embeddings"][model]
    return entry["batch_input"] if batch else entry["input"]


# --- per paper ---------------------------------------------------------------


@dataclass
class Paper:
    """What one paper costs and occupies, for one model."""

    chunks: float
    items: float
    items_by_prompt: dict[str, float]
    model_calls: float
    model_calls_batch: float
    embeddings_chunks: float
    embeddings_items: float
    render_cpu_seconds: float
    vectors: float
    triples: float
    qdrant_disk: float
    qdrant_ram: float
    s3_vectors_bytes: float
    fuseki_bytes: float
    ducklake_bytes: float
    object_bytes: float


def storage_rates(inputs: Inputs) -> dict:
    """Bytes per stored thing, from production."""
    qdrant = inputs.infra["qdrant"]["phases_v2_extracted_items"]
    fuseki = inputs.infra["fuseki"]
    triples = sum(fuseki["triples_by_graph"].values())
    rows = inputs.prod["table_rows"]
    files = {r["table_name"]: r["bytes"] for r in inputs.prod["data_file_bytes"]}
    live = sum(files.values())
    papers = inputs.prod["papers"]["n"]
    return {
        "qdrant_bytes_per_point": qdrant["disk_bytes"] / qdrant["points"],
        "fuseki_bytes_per_triple": fuseki["database_bytes"] / triples,
        "chunk_row": files["chunks"] / rows["chunks"],
        "extraction_row": files["extractions"] / rows["extractions"],
        "item_row": files["extracted_items"] / rows["extracted_items"],
        "projection_row": files["projections"] / rows["projections"],
        "relation_row": files["probabilistic_relations"]
        / rows["probabilistic_relations"],
        "ducklake_on_disk_ratio": inputs.infra["storage"]["ducklake_data_dir_bytes"]
        / live,
        "object_bytes_per_paper": inputs.infra["storage"]["object_store_abi_bytes"]
        / papers,
    }


def triples_per_paper(chunks: float, extractions: float, items: float) -> float:
    """What ``projection/graph.py`` writes: 2 per paper, 5 per chunk and extraction, 7 per item."""
    return 2 + 5 * chunks + 5 * extractions + 7 * items


def qdrant_ram_per_point(
    inputs: Inputs, dimensions: int, quantization: str | None = None
) -> float:
    """RAM per point by Qdrant's capacity formulas, headroom included.

    Without quantization the float32 vectors are in RAM. With it, the quantized
    copy is in RAM and the originals move to disk.
    """
    sizing = inputs.prices["qdrant_sizing"]
    vector = dimensions * 4
    if quantization:
        vector = vector / sizing["quantization_ratio"][quantization]
    raw = vector + sizing["hnsw_bytes_per_point"] + sizing["id_tracker_bytes_per_point"]
    return raw * (1 + sizing["headroom"])


def paper(
    inputs: Inputs,
    model_id: str,
    *,
    source: str | None = None,
    prompts=PROMPTS,
    embed_items: bool = True,
    embedding_model: str | None = None,
    dimensions: int | None = None,
    quantization: str | None = None,
) -> Paper:
    prof = profile(inputs, model_id)
    summary = lengths(inputs, source)
    chunks = summary["chunks"]["mean"]
    items_by_prompt = {p: prof.items_per_chunk[p] * chunks for p in prompts}
    items = sum(items_by_prompt.values())
    extractions = chunks * len(prompts)
    rates = storage_rates(inputs)
    dims = dimensions or inputs.assumptions["embedding"]["dimensions"]
    embed_price = embedding_price(inputs, embedding_model) / 1e6
    item_tokens = item_embedding_tokens(inputs)
    embedded_items = items if embed_items else 0.0
    vectors = chunks + embedded_items
    # Measured bytes per point scale with the vector size: the vector is the bulk.
    bytes_per_point = rates["qdrant_bytes_per_point"] * dims / 3072
    relations = items_by_prompt.get(RELATION_PROMPT, 0.0)
    ducklake = (
        chunks * rates["chunk_row"]
        + extractions * rates["extraction_row"]
        + items * rates["item_row"]
        + (extractions + vectors) * rates["projection_row"]
        + relations * rates["relation_row"]
    )
    if inputs.assumptions["ducklake_size"] == "measured":
        ducklake *= rates["ducklake_on_disk_ratio"]
    pdf = inputs.lengths["pdf_rendered"]["summary"]
    object_bytes = (
        (pdf["bytes"]["mean"] + pdf["chars"]["mean"])
        if (source or inputs.assumptions["paper_source"]) == "pdf_rendered"
        else lengths(inputs, "pmc_text")["chars"]["mean"]
    )
    triples = triples_per_paper(chunks, extractions, items)
    return Paper(
        chunks=chunks,
        items=items,
        items_by_prompt=items_by_prompt,
        model_calls=chunks * model_cost_per_chunk(inputs, prof, prompts),
        model_calls_batch=chunks
        * model_cost_per_chunk(inputs, prof, prompts, batch=True),
        embeddings_chunks=chunks * chunk_embedding_tokens(inputs) * embed_price,
        embeddings_items=(
            sum(items_by_prompt[p] * item_tokens[p] for p in prompts) * embed_price
            if embed_items
            else 0.0
        ),
        render_cpu_seconds=(
            pdf["render_cpu_seconds"]["mean"]
            if (source or inputs.assumptions["paper_source"]) == "pdf_rendered"
            else 0.0
        ),
        vectors=vectors,
        triples=triples,
        qdrant_disk=vectors * bytes_per_point,
        qdrant_ram=vectors * qdrant_ram_per_point(inputs, dims, quantization),
        s3_vectors_bytes=vectors
        * (
            dims * 4
            + inputs.assumptions["managed"]["s3_vectors_key_and_metadata_bytes"]
        ),
        fuseki_bytes=triples * rates["fuseki_bytes_per_triple"],
        ducklake_bytes=ducklake,
        object_bytes=object_bytes,
    )


# --- infrastructure ----------------------------------------------------------


def s3_month(inputs: Inputs, nbytes: float) -> float:
    tiers = inputs.prices["aws_us_east_1"]["s3_standard_gb_month"]
    gb = nbytes / GB
    first = min(gb, 50_000)
    second = min(max(gb - 50_000, 0), 450_000)
    rest = max(gb - 500_000, 0)
    return (
        first * tiers["first_50tb"]
        + second * tiers["next_450tb"]
        + rest * tiers["over_500tb"]
    )


def ec2_month(inputs: Inputs, instance: str, count_: float = 1) -> float:
    aws = inputs.prices["aws_us_east_1"]
    return aws["ec2_hourly"][instance] * inputs.assumptions["hours_per_month"] * count_


def ebs_month(inputs: Inputs, nbytes: float) -> float:
    headroom = 1 + inputs.assumptions["self_hosted"]["disk_headroom"]
    return nbytes * headroom / GB * inputs.prices["aws_us_east_1"]["ebs_gp3_gb_month"]


@dataclass
class Infra:
    """Monthly cost of holding a corpus, and how big it is."""

    papers: float
    vectors: float
    triples: float
    qdrant_ram: float
    qdrant_disk: float
    fuseki_bytes: float
    ducklake_bytes: float
    object_bytes: float
    qdrant_nodes: int
    self_storage: float
    self_hosting: float
    managed_storage: float
    managed_hosting: float
    managed_vectors: str
    neptune_over_limit: bool
    s3_vectors_indexes: int


def infra(inputs: Inputs, per_paper: Paper, papers: float) -> Infra:
    hosted = inputs.assumptions["self_hosted"]
    aws = inputs.prices["aws_us_east_1"]
    node_ram = aws["ec2_specs"][hosted["qdrant_node"]][1] * GIB
    usable = inputs.prices["qdrant_sizing"]["node_ram_usable"]
    ram = per_paper.qdrant_ram * papers
    qdrant_nodes = max(1, math.ceil(ram / (node_ram * usable)))
    qdrant_disk = per_paper.qdrant_disk * papers
    fuseki = per_paper.fuseki_bytes * papers
    ducklake = per_paper.ducklake_bytes * papers
    objects = per_paper.object_bytes * papers

    self_storage = (
        ebs_month(inputs, qdrant_disk)
        + ebs_month(inputs, fuseki)
        + s3_month(inputs, ducklake + objects)
    )
    self_hosting = (
        ec2_month(inputs, hosted["qdrant_node"], qdrant_nodes)
        + ec2_month(inputs, hosted["fuseki_node"])
        + ec2_month(inputs, hosted["app_node"])
    )

    managed = inputs.prices["managed"]
    neptune = managed["neptune"]
    choice = inputs.assumptions["managed"]
    storage_key = (
        "io_optimized_storage_gb_month"
        if choice["neptune_storage"] == "io_optimized"
        else "storage_gb_month"
    )
    qdrant_quote = managed["qdrant_cloud"].get("usd_per_gb_ram_month")
    if qdrant_quote:
        vectors_month = ram / GB * qdrant_quote
        label = "Qdrant Cloud (quote)"
    else:
        vectors_month = (
            per_paper.s3_vectors_bytes
            * papers
            / GB
            * managed["s3_vectors"]["storage_gb_month"]
        )
        label = "S3 Vectors"
    managed_storage = (
        vectors_month
        + fuseki / GB * neptune[storage_key]
        + s3_month(inputs, ducklake + objects)
    )
    managed_hosting = neptune["instance_hourly"][
        choice["neptune_instance"]
    ] * inputs.assumptions["hours_per_month"] + ec2_month(inputs, hosted["app_node"])
    per_index = managed["s3_vectors"]["max_vectors_per_index"]
    return Infra(
        papers=papers,
        vectors=per_paper.vectors * papers,
        triples=per_paper.triples * papers,
        qdrant_ram=ram,
        qdrant_disk=qdrant_disk,
        fuseki_bytes=fuseki,
        ducklake_bytes=ducklake,
        object_bytes=objects,
        qdrant_nodes=qdrant_nodes,
        self_storage=self_storage,
        self_hosting=self_hosting,
        managed_storage=managed_storage,
        managed_hosting=managed_hosting,
        managed_vectors=label,
        neptune_over_limit=fuseki > neptune["cluster_volume_max_tib"] * 1024**4,
        s3_vectors_indexes=math.ceil(per_paper.vectors * papers / per_index),
    )


def render_cost(inputs: Inputs, per_paper: Paper, papers: float) -> float:
    """EC2 for rendering ``papers`` PDFs on the render worker type."""
    worker = inputs.assumptions["self_hosted"]["render_worker"]
    aws = inputs.prices["aws_us_east_1"]
    vcpus = aws["ec2_specs"][worker][0]
    hours = per_paper.render_cpu_seconds * papers / 3600 / vcpus
    return hours * aws["ec2_hourly"][worker]


@dataclass
class Bill:
    backfill_calls: float
    backfill_calls_batch: float
    backfill_embeddings: float
    backfill_render: float
    monthly_calls: float
    monthly_embeddings: float
    monthly_render: float
    infra: Infra
    new_papers_month: float


def bill(inputs: Inputs, model_id: str, corpus: str, **paper_options) -> Bill:
    per = paper(inputs, model_id, **paper_options)
    papers = corpus_size(inputs, corpus)
    new = growth(inputs, corpus)["mean"]
    held = infra(inputs, per, papers + new)
    return Bill(
        backfill_calls=per.model_calls * papers,
        backfill_calls_batch=per.model_calls_batch * papers,
        backfill_embeddings=(per.embeddings_chunks + per.embeddings_items) * papers,
        backfill_render=render_cost(inputs, per, papers),
        monthly_calls=per.model_calls * new,
        monthly_embeddings=(per.embeddings_chunks + per.embeddings_items) * new,
        monthly_render=render_cost(inputs, per, new),
        infra=held,
        new_papers_month=new,
    )


# --- time to complete --------------------------------------------------------


def prod_throughput(inputs: Inputs) -> dict:
    """Calls per hour over production's recent extraction runs."""
    since = inputs.assumptions["throughput"]["runs_since"]
    minimum = inputs.assumptions["throughput"]["min_calls"]
    runs = [
        r
        for r in inputs.prod["extraction_runs"]
        if r["started_at"][:10] >= since and r["succeeded"] + r["failed"] >= minimum
    ]
    calls = sum(r["succeeded"] + r["failed"] for r in runs)
    seconds = sum(r["seconds"] for r in runs)
    rates = sorted((r["succeeded"] + r["failed"]) / r["seconds"] * 3600 for r in runs)
    return {
        "runs": len(runs),
        "calls_per_hour": calls / seconds * 3600,
        "median_run": statistics.median(rates),
        "min_run": rates[0],
        "max_run": rates[-1],
    }


def provider_limit(inputs: Inputs, model_id: str, tier: str) -> dict | None:
    """Chunks per hour a tier's per-minute limits allow, or None when unpublished."""
    limits = inputs.prices["openai_rate_limits"].get(price_id(model_id))
    if not limits:
        return None
    prof = profile(inputs, model_id)
    utilisation = inputs.assumptions["rate_limit_utilisation"]
    by_tokens = limits[tier]["tpm"] * utilisation / tokens_per_chunk(prof) * 60
    calls_per_chunk = len(PROMPTS) * prof.calls_per_extraction
    by_requests = limits[tier]["rpm"] * utilisation / calls_per_chunk * 60
    return {
        "chunks_per_hour": min(by_tokens, by_requests),
        "batch_queue": limits[tier]["batch_queue"],
    }


def hours_to_complete(inputs: Inputs, model_id: str, papers: float) -> dict:
    per = paper(inputs, model_id)
    prof = profile(inputs, model_id)
    chunks = per.chunks * papers
    calls = chunks * len(PROMPTS)
    out = {"prod": calls / prod_throughput(inputs)["calls_per_hour"]}
    # The worker pool alone: 20 calls in flight, each taking the calibration's
    # mean latency, with the datastore out of the way.
    records = [r for r in calibration_records(inputs) if r["model_id"] == model_id]
    latency = statistics.fmean(
        c["seconds"] for r in records for c in r["calls"] if c.get("ok")
    )
    workers = inputs.assumptions["extraction_workers"]
    out["workers"] = calls * prof.calls_per_extraction * latency / workers / 3600
    tier = inputs.assumptions["openai_tier"]
    embed = inputs.prices["openai_rate_limits"][
        inputs.assumptions["embedding"]["model"]
    ][tier]
    tokens = (
        per.chunks * chunk_embedding_tokens(inputs)
        + sum(
            per.items_by_prompt[p] * item_embedding_tokens(inputs)[p] for p in PROMPTS
        )
    ) * papers
    out["embeddings"] = (
        tokens / (embed["tpm"] * inputs.assumptions["rate_limit_utilisation"]) / 60
    )
    for tier in ("build", "launch", "grow"):
        limit = provider_limit(inputs, model_id, tier)
        if limit is None:
            out[tier] = out[f"batch_{tier}"] = None
            continue
        out[tier] = chunks / limit["chunks_per_hour"]
        # A batch's input is counted against the queue when it is submitted and
        # each batch completes within 24 hours: at most one full queue per day.
        input_tokens = chunks * sum(
            prof.input_tokens[p] * prof.calls_per_extraction for p in PROMPTS
        )
        days = max(1.0, input_tokens / limit["batch_queue"])
        out[f"batch_{tier}"] = days * 24
    # Grow's monthly usage limit caps spend, so it caps the pace as well.
    cap = inputs.prices["openai_tiers"]["monthly_usage_limit_usd"]["grow"]
    if inputs.prices["models"][price_id(model_id)]["route"] == "openai":
        months = per.model_calls * papers / cap
        out["grow_cap"] = months * inputs.assumptions["hours_per_month"]
    else:
        out["grow_cap"] = None
    return out


# --- uncertainty -------------------------------------------------------------


def calibration_interval(
    inputs: Inputs, model_id: str, seed: int = 855, draws: int = 2000
) -> tuple[float, float]:
    """90% bootstrap interval, over calibration chunks, of model-call cost relative to its mean."""
    records = [r for r in calibration_records(inputs) if r["model_id"] == model_id]
    price = model_price(inputs, model_id)
    per_chunk: dict[str, float] = defaultdict(float)
    for record in records:
        for call in record["calls"]:
            if call.get("ok"):
                per_chunk[record["chunk_id"]] += (
                    call["input_tokens"] * price["input"]
                    + call["output_tokens"] * price["output"]
                ) / 1e6
    values = list(per_chunk.values())
    mean = statistics.fmean(values)
    rng = random.Random(seed)
    means = sorted(
        statistics.fmean(rng.choices(values, k=len(values))) for _ in range(draws)
    )
    return means[int(0.05 * draws)] / mean, means[int(0.95 * draws)] / mean


# --- tables ------------------------------------------------------------------


def display(inputs: Inputs, model_id: str) -> str:
    entry = inputs.prices["models"][price_id(model_id)]
    name = entry["display"]
    if "@effort-none" in model_id:
        name += " (effort none)"
    elif "@responses" in model_id:
        name += " (Responses API)"
    notes = []
    retiring = inputs.prices["deprecations"].get(price_id(model_id))
    if retiring:
        notes.append(f"retiring {retiring['shutdown']}")
    if not entry["declared"]:
        notes.append("not declared")
    if model_id == inputs.assumptions["reference_model"]:
        notes.append("measured reference")
    return name + (f" ({'; '.join(notes)})" if notes else "")


def short(inputs: Inputs, model_id: str) -> str:
    """The model's name and, for a variant, how it was called."""
    name = inputs.prices["models"][price_id(model_id)]["display"]
    if "@effort-none" in model_id:
        return name + ", effort none"
    if "@responses" in model_id:
        return name + ", Responses"
    return name


def headline_models(inputs: Inputs) -> list[str]:
    return inputs.assumptions["headline_models"]


def t_key_numbers(inputs: Inputs) -> str:
    oa = growth(inputs, "oa")
    everything = growth(inputs, "all")
    pdf = lengths(inputs, "pdf_rendered")
    text = lengths(inputs, "pmc_text")
    groups = inputs.entrez["open_access_by_license_group"]
    entrez = f"Entrez, {inputs.entrez['retrieved'][:10]}"
    bucket = f"bucket inventory, {inputs.inventory['retrieved'][:10]}"
    seed = inputs.metadata["seed"]
    sampled = f"sample (seed {seed}), {inputs.lengths['measured_at'][:10]}"
    rows = [
        [
            "Articles in PMC",
            count(corpus_size(inputs, "all")),
            f"{entrez}: `esearch db=pmc`, `all[filter]`",
        ],
        [
            "… in the open access subset",
            count(corpus_size(inputs, "oa")),
            f"{entrez}: `open access[filter]`",
        ],
        [
            "… of which commercial / non-commercial / other license",
            f"{count(groups['commercial'])} / {count(groups['non_commercial'])} / {count(groups['other'])}",
            f"{entrez}: license filters (license table)",
        ],
        [
            "Author manuscripts",
            count(inputs.entrez["totals"]["author_manuscript"]),
            f"{entrez}: `author manuscript[filter]`",
        ],
        [
            "Article versions in the PMC Cloud bucket",
            count(inputs.inventory["article_versions"]),
            f"{bucket}: snapshot {inputs.inventory['snapshot']}",
        ],
        [
            "Articles added to the bucket in 30 days",
            f"{inputs.inventory['added_articles']:,}",
            f"{bucket}: {inputs.inventory['compared_with']} vs {inputs.inventory['snapshot']}",
        ],
        [
            "Paper length, PMC text (words, mean / median)",
            f"{text['words']['mean']:,.0f} / {text['words']['median']:,.0f}",
            f"{sampled}: {text['words']['n']:,} texts",
        ],
        [
            "Paper length, PMC text (o200k tokens, mean / median)",
            f"{text['o200k_tokens']['mean']:,.0f} / {text['o200k_tokens']['median']:,.0f}",
            f"{sampled}: {text['o200k_tokens']['n']:,} texts",
        ],
        [
            "Paper length, rendered PDF (pages / chunks, mean)",
            f"{pdf['pages']['mean']:,.1f} / {pdf['chunks']['mean']:,.1f}",
            f"{sampled}: {pdf['chunks']['n']:,} PDFs",
        ],
        [
            "New articles per 30 days, all PMC (last / 12-month mean / range)",
            f"{everything['last']:,} / {everything['mean']:,.0f} / {everything['min']:,}–{everything['max']:,}",
            f"{entrez}: `[PmcLiveDate]`",
        ],
        [
            "New articles per 30 days, open access (last / mean / range)",
            f"{oa['last']:,} / {oa['mean']:,.0f} / {oa['min']:,}–{oa['max']:,}",
            f"{entrez}: `[PmcLiveDate]`",
        ],
    ]
    return table(["Figure", "Value", "Source"], rows)


def t_headline(inputs: Inputs, corpus: str) -> str:
    rows = []
    for model_id in headline_models(inputs):
        b = bill(inputs, model_id, corpus)
        low, high = calibration_interval(inputs, model_id)
        running = b.monthly_calls + b.monthly_embeddings + b.monthly_render
        rows.append(
            [
                display(inputs, model_id),
                usd(b.backfill_calls),
                f"{usd(b.backfill_calls * low)}–{usd(b.backfill_calls * high)}",
                usd(b.backfill_calls_batch),
                usd(b.backfill_embeddings),
                usd(b.monthly_calls),
                usd(b.monthly_embeddings),
                usd(b.backfill_calls + b.backfill_embeddings + b.backfill_render),
                usd(running + b.infra.self_storage + b.infra.self_hosting),
                usd(running + b.infra.managed_storage + b.infra.managed_hosting),
            ]
        )
    return table(
        [
            "Model",
            "Backfill: model calls",
            "90% range",
            "… with Batch",
            "Backfill: embeddings",
            "Per 30 days: model calls",
            "Per 30 days: embeddings",
            "Backfill total¹",
            "Per 30 days, self-hosted²",
            "Per 30 days, managed²",
        ],
        rows,
    )


def t_infra(inputs: Inputs, corpus: str) -> str:
    rows = []
    for model_id in headline_models(inputs):
        b = bill(inputs, model_id, corpus)
        i = b.infra
        rows.append(
            [
                display(inputs, model_id),
                count(i.vectors),
                count(i.triples),
                size(i.qdrant_ram),
                str(i.qdrant_nodes),
                size(
                    i.qdrant_disk + i.fuseki_bytes + i.ducklake_bytes + i.object_bytes
                ),
                usd(i.self_storage),
                usd(i.self_hosting),
                usd(i.managed_storage),
                usd(i.managed_hosting),
            ]
        )
    return table(
        [
            "Model",
            "Vectors",
            "Triples",
            "Qdrant RAM",
            "Qdrant nodes",
            "Disk, all stores",
            "Self-hosted: storage /mo",
            "Self-hosted: hosting /mo",
            "Managed: storage /mo",
            "Managed: hosting /mo",
        ],
        rows,
    )


def input_per_paper(inputs: Inputs, model_id: str) -> float:
    """Billed input tokens for one paper through all 8 prompts, retries included."""
    prof = profile(inputs, model_id)
    return chunks_per_paper(inputs) * sum(
        prof.input_tokens[p] * prof.calls_per_extraction for p in PROMPTS
    )


def t_per_paper(inputs: Inputs) -> str:
    rates = storage_rates(inputs)
    model = inputs.assumptions["reference_model"]
    per = paper(inputs, model)
    pdf = lengths(inputs, "pdf_rendered")
    prod = inputs.prod
    rows = [
        [
            "Chunks per paper",
            f"{per.chunks:,.1f}",
            "sample (rendered OA PDFs)",
            f"prod {prod['chunks_per_paper']['mean']:,.1f}",
        ],
        [
            "Rendered characters per paper",
            f"{pdf['chars']['mean']:,.0f}",
            "sample",
            f"prod {prod['paper_rendered_chars']['mean']:,.0f}",
        ],
        [
            "PDF size",
            size(pdf["bytes"]["mean"]),
            "sample",
            f"prod {size(prod['papers']['mean'])}",
        ],
        [
            "Render CPU per paper",
            f"{pdf['render_cpu_seconds']['mean']:,.1f} s",
            "sample",
            "",
        ],
        [
            "Extraction calls per paper (8 prompts)",
            f"{per.chunks * len(PROMPTS):,.0f}",
            "sample × prompts",
            "",
        ],
        [
            "Billed input tokens per paper, by model",
            ", ".join(
                f"{short(inputs, m)} {input_per_paper(inputs, m) / 1000:,.0f}k"
                for m in (
                    "openai/gpt-4.1-mini",
                    "openai/gpt-6-luna@effort-none",
                    "openrouter/claude-sonnet-4.6",
                )
            ),
            "offline count + calibration overhead",
            "scoping guess ~300k",
        ],
        [
            "Chunk tokens (o200k)",
            f"{inputs.tokens['chunk']['o200k_tokens']['mean']:,.0f}",
            "offline count, prod sample",
            "",
        ],
        [
            "Chunk embedding tokens (cl100k)",
            f"{chunk_embedding_tokens(inputs):,.0f}",
            "offline count, prod sample",
            "",
        ],
        [
            "Items per paper, reference model",
            f"{per.items:,.0f}",
            "calibration (PMC chunks) × sample chunks",
            "",
        ],
        [
            "Qdrant bytes per vector (3072 d, payload and index)",
            f"{rates['qdrant_bytes_per_point']:,.0f}",
            "prod",
            "",
        ],
        [
            "Fuseki bytes per triple (TDB2, never compacted)",
            f"{rates['fuseki_bytes_per_triple']:,.0f}",
            "prod",
            "",
        ],
        [
            "Triples per paper, reference model",
            f"{per.triples:,.0f}",
            "projection code × counts",
            "",
        ],
        [
            "DuckLake bytes per paper (live files)",
            size(per.ducklake_bytes),
            "prod bytes per row × counts",
            f"on disk ×{rates['ducklake_on_disk_ratio']:.1f}",
        ],
        [
            "Object storage per paper (PDF + markdown)",
            size(per.object_bytes),
            "sample",
            f"prod {size(rates['object_bytes_per_paper'])}",
        ],
    ]
    return table(["Per paper", "Value", "From", "Compare"], rows)


def t_prompt_tokens(inputs: Inputs) -> str:
    rows = []
    prod_items = reference_items(inputs, "prod")
    pmc_items = reference_items(inputs, "pmc_sample")
    item_tokens = item_embedding_tokens(inputs)
    answers = inputs.tokens["answers"][inputs.assumptions["reference_model"]]
    for p in PROMPTS:
        entry = inputs.tokens["prompts"][p]
        rows.append(
            [
                f"`{p}`",
                f"{entry['template_tokens']:,}",
                f"{entry['tool_schema_tokens']:,}",
                f"{entry['input_tokens_mean']:,.0f}",
                f"{answers[p]['output_tokens']['mean']:,.0f}",
                f"{prod_items[p]:.2f}",
                f"{pmc_items[p]:.2f}",
                f"{item_tokens[p]:.1f}",
            ]
        )
    return table(
        [
            "Prompt",
            "Template tokens",
            "Tool schema tokens",
            "Input per call (offline)",
            "Answer tokens (prod)",
            "Items per chunk (prod)",
            "Items per chunk (PMC sample)",
            "Embedding tokens per item (prod)",
        ],
        rows,
    )


def t_calibration(inputs: Inputs) -> str:
    """Each model's measured usage, per calibration run (raw, before any scaling)."""
    rows = []
    for source, label in (("prod", "prod chunks"), ("pmc_sample", "PMC chunks")):
        for model_id in headline_models(inputs):
            records = [
                r
                for r in calibration_records(inputs, source)
                if r["model_id"] == model_id
            ]
            if not records:
                continue
            calls = [c for r in records for c in r["calls"] if c.get("ok")]
            price = model_price(inputs, model_id)
            cost = sum(
                (
                    c["input_tokens"] * price["input"]
                    + c["output_tokens"] * price["output"]
                )
                / 1e6
                for c in calls
            )
            chunks = {r["chunk_id"] for r in records}
            answered = [r for r in records if r["status"] == "succeeded"]
            rows.append(
                [
                    display(inputs, model_id),
                    f"{label}: {len(chunks)} × {len(PROMPTS)}",
                    f"{statistics.fmean(c['input_tokens'] for c in calls):,.0f} / "
                    f"{statistics.fmean(r['offline_input_tokens'] for r in records):,.0f}",
                    f"{statistics.fmean(c['output_tokens'] for c in calls):,.0f}",
                    f"{statistics.fmean(c['reasoning_tokens'] or 0 for c in calls):,.0f}",
                    f"{sum(len(r['calls']) for r in records) / len(records):.2f}",
                    f"{sum(r['status'] == 'failed' for r in records) / len(records):.0%}",
                    f"{sum(r['items'] for r in answered) / len(chunks):.1f}",
                    f"{item_ratio(inputs, model_id):.2f}" if source == "prod" else "",
                    f"{statistics.fmean(c['seconds'] for c in calls):.1f}",
                    usd(cost / len(chunks)),
                ]
            )
    return table(
        [
            "Model",
            "Chunks × prompts",
            "Input tokens billed / offline",
            "Output tokens",
            "… of which reasoning",
            "Calls per extraction",
            "Failed",
            "Items per chunk",
            "Items vs reference",
            "Seconds per call",
            "Cost per chunk (8 prompts)",
        ],
        rows,
    )


def t_time(inputs: Inputs, corpus: str) -> str:
    papers = corpus_size(inputs, corpus)
    rows = []
    for model_id in headline_models(inputs):
        h = hours_to_complete(inputs, model_id, papers)
        rows.append(
            [
                display(inputs, model_id),
                duration(h["prod"]),
                duration(h["workers"]),
                duration(h["build"]),
                duration(h["launch"]),
                duration(h["grow"]),
                duration(h["batch_launch"]),
                duration(h["batch_grow"]),
                duration(h["grow_cap"]),
                duration(h["embeddings"]),
            ]
        )
    return table(
        [
            "Model",
            "At production's measured rate",
            "20 workers, latency only",
            "Rate limit: Build",
            "Launch",
            "Grow",
            "Batch queue: Launch",
            "Batch queue: Grow",
            "Grow's $200k/month usage limit",
            "Embeddings at tier limit³",
        ],
        rows,
    )


def t_two_models(inputs: Inputs, corpus: str) -> str:
    models = headline_models(inputs)
    single = {m: bill(inputs, m, corpus) for m in models}
    # A second model adds its calls and its items' embeddings, not the chunks'.
    added = {
        m: single[m].backfill_calls
        + paper(inputs, m).embeddings_items * corpus_size(inputs, corpus)
        for m in models
    }
    header = ["First model ↓ / second →"] + [short(inputs, m) for m in models]
    rows = []
    for first in models:
        first_cost = single[first].backfill_calls + single[first].backfill_embeddings
        rows.append(
            [short(inputs, first)]
            + [
                "—" if first == second else usd(first_cost + added[second])
                for second in models
            ]
        )
    return table(header, rows)


def t_per_prompt(inputs: Inputs) -> str:
    rows = []
    for p in PROMPTS:
        row = [f"`{p}`"]
        for model_id in headline_models(inputs):
            per = paper(inputs, model_id, prompts=(p,))
            row.append(usd((per.model_calls + per.embeddings_items) * 1000))
        rows.append(row)
    chunks = paper(inputs, inputs.assumptions["reference_model"])
    rows.append(
        ["chunk embeddings (once per corpus)"]
        + [usd(chunks.embeddings_chunks * 1000)] * len(headline_models(inputs))
    )
    return table(
        ["Prompt, per 1,000 papers"]
        + [short(inputs, m) for m in headline_models(inputs)],
        rows,
    )


def t_embedding_scenarios(inputs: Inputs, corpus: str) -> str:
    model = inputs.assumptions["reference_model"]
    papers = corpus_size(inputs, corpus)
    scenarios = [
        ("As today: text-embedding-3-large, 3072 d, chunks and items", {}),
        ("Chunks only (no item vectors)", {"embed_items": False}),
        (
            "text-embedding-3-small, 1536 d",
            {"embedding_model": "text-embedding-3-small", "dimensions": 1536},
        ),
        ("text-embedding-3-large at 1024 d", {"dimensions": 1024}),
        (
            "3072 d, scalar int8 quantization, originals on disk",
            {"quantization": "scalar_int8"},
        ),
        (
            "3072 d, binary quantization, originals on disk",
            {"quantization": "binary_1bit"},
        ),
    ]
    rows = []
    for label, options in scenarios:
        per = paper(inputs, model, **options)
        held = infra(inputs, per, papers)
        rows.append(
            [
                label,
                usd((per.embeddings_chunks + per.embeddings_items) * papers),
                count(held.vectors),
                size(held.qdrant_ram),
                str(held.qdrant_nodes),
                usd(held.self_storage + held.self_hosting),
            ]
        )
    return table(
        [
            "Embedding scenario (reference model)",
            "Backfill embeddings",
            "Vectors",
            "Qdrant RAM",
            "Qdrant nodes",
            "Self-hosted storage + hosting /mo",
        ],
        rows,
    )


def t_text_scenario(inputs: Inputs, corpus: str) -> str:
    rows = []
    for model_id in headline_models(inputs):
        pdf = bill(inputs, model_id, corpus)
        text = bill(inputs, model_id, corpus, source="pmc_text")
        rows.append(
            [
                display(inputs, model_id),
                usd(pdf.backfill_calls + pdf.backfill_embeddings + pdf.backfill_render),
                usd(
                    text.backfill_calls
                    + text.backfill_embeddings
                    + text.backfill_render
                ),
            ]
        )
    pdf_chunks = chunks_per_paper(inputs, "pdf_rendered")
    text_chunks = chunks_per_paper(inputs, "pmc_text")
    return (
        table(["Model", "Backfill from PDFs", "Backfill from PMC text"], rows)
        + f"\n\nChunks per paper: {pdf_chunks:.1f} from rendered PDFs, {text_chunks:.1f} from PMC's text."
    )


def t_per_thousand(inputs: Inputs) -> str:
    rows = []
    for model_id in headline_models(inputs):
        per = paper(inputs, model_id)
        held = infra(inputs, per, 1000)
        rows.append(
            [
                display(inputs, model_id),
                usd(per.model_calls * 1000),
                usd(per.model_calls_batch * 1000),
                usd((per.embeddings_chunks + per.embeddings_items) * 1000),
                usd(render_cost(inputs, per, 1000)),
                size(
                    held.qdrant_disk
                    + held.fuseki_bytes
                    + held.ducklake_bytes
                    + held.object_bytes
                ),
                usd(held.self_storage),
            ]
        )
    return table(
        [
            "Model",
            "Model calls",
            "… with Batch",
            "Embeddings",
            "Rendering (EC2)",
            "Disk",
            "Storage /mo (self-hosted)",
        ],
        rows,
    )


def t_throughput(inputs: Inputs) -> str:
    t = prod_throughput(inputs)
    steps = inputs.infra["pipeline_steps"]

    def rate(step: str, prefix: str) -> float:
        done = [
            (int(s["message"].split("=")[1].split()[0]), s["seconds"])
            for s in steps
            if s["step"] == step and (s["message"] or "").startswith(prefix)
        ]
        done = [(n, sec) for n, sec in done if n >= 1000 and sec > 0]
        return sum(n for n, _ in done) / sum(sec for _, sec in done)

    chunks = chunks_per_paper(inputs)
    rows = [
        [
            "Extraction calls per hour (mean)",
            f"{t['calls_per_hour']:,.0f}",
            f"{t['runs']} runs since {inputs.assumptions['throughput']['runs_since']}",
        ],
        [
            "… per run (median, min–max)",
            f"{t['median_run']:,.0f} ({t['min_run']:,.0f}–{t['max_run']:,.0f})",
            "same runs",
        ],
        [
            "Papers per hour, all 8 prompts",
            f"{t['calls_per_hour'] / (chunks * len(PROMPTS)):,.1f}",
            "at sample chunks per paper",
        ],
        [
            "Vectors embedded per second",
            f"{rate('project_vectors_op', 'embedded='):,.1f}",
            "projection runs ≥ 1,000 vectors",
        ],
        [
            "Extractions projected to the graph per second",
            f"{rate('project_graph_op', 'projected='):,.1f}",
            "projection runs ≥ 1,000",
        ],
        [
            "PDF rendering, CPU-hours for the OA subset",
            f"{paper(inputs, inputs.assumptions['reference_model']).render_cpu_seconds * corpus_size(inputs, 'oa') / 3600:,.0f}",
            "sample render CPU × corpus",
        ],
    ]
    return table(["Production throughput", "Value", "Basis"], rows)


def t_calibration_spend(inputs: Inputs) -> str:
    rows = [
        [
            f"`{c['file']}`",
            c.get("chunk_source", "prod"),
            ", ".join(m.split("/")[-1] for m in c["models"]),
            str(len({r["chunk_id"] for r in c["records"] if r["status"] != "not_run"})),
            usd(c["spent_usd"]),
            "stopped by its cap" if c.get("stopped") else "",
        ]
        for c in inputs.calibration
    ]
    total = usd(calibration_spend(inputs)["total"])
    rows.append(["**Total**", "", "", "", f"**{total}**", ""])
    return table(["Run", "Chunks from", "Models", "Chunks run", "Spent", "Note"], rows)


def t_lengths(inputs: Inputs) -> str:
    """Paper length distributions: PMC's text, rendered PDFs, and production."""
    text = lengths(inputs, "pmc_text")
    pdf = lengths(inputs, "pdf_rendered")
    prod = inputs.prod

    def cells(d: dict | None) -> list[str]:
        if not d:
            return ["", "", "", ""]
        return [
            f"{d[k]:,.0f}" if d[k] >= 100 else f"{d[k]:,.1f}"
            for k in ("mean", "median", "p10", "p90")
        ]

    rows = [
        ["Words", "PMC text", *cells(text["words"])],
        ["Words", "rendered PDF", *cells(pdf["words"])],
        ["Characters", "PMC text", *cells(text["chars"])],
        ["Characters", "rendered PDF", *cells(pdf["chars"])],
        ["Characters", "prod (rendered)", *cells(prod["paper_rendered_chars"])],
        ["Tokens (o200k)", "PMC text", *cells(text["o200k_tokens"])],
        ["Tokens (o200k)", "rendered PDF", *cells(pdf["o200k_tokens"])],
        ["Tokens (cl100k)", "PMC text", *cells(text["cl100k_tokens"])],
        ["Pages", "PDF", *cells(pdf["pages"])],
        ["Chunks (512/128)", "PMC text", *cells(text["chunks"])],
        ["Chunks (512/128)", "rendered PDF", *cells(pdf["chunks"])],
        ["Chunks (512/128)", "prod", *cells(prod["chunks_per_paper"])],
        ["PDF bytes", "PDF", *cells(pdf["bytes"])],
        ["PDF bytes", "prod", *cells(prod["papers"])],
        ["Render CPU seconds", "rendered PDF", *cells(pdf["render_cpu_seconds"])],
    ]
    seed = inputs.metadata["seed"]
    note = (
        f"\n\nSample: seed {seed}, {text['words']['n']:,} PMC texts and "
        f"{pdf['words']['n']:,} PDFs drawn in order from the "
        f"{inputs.metadata['size']:,}-article metadata sample, measured "
        f"{inputs.lengths['measured_at'][:10]}. Prod: {prod['papers']['n']} papers, "
        f"{prod['measured_at'][:10]}."
    )
    return table(["Per paper", "From", "Mean", "Median", "p10", "p90"], rows) + note


def t_license(inputs: Inputs) -> str:
    groups = inputs.entrez["open_access_by_license_group"]
    filters = inputs.entrez["license_filters"]
    summary = inputs.metadata["summary"]
    n = inputs.metadata["size"]
    sample_groups = summary["license_group"]
    pdf_by_group = inputs.metadata["has_pdf_by_license_group"]
    rows = []
    for group in ("commercial", "non_commercial", "other"):
        sampled = sample_groups.get(group, 0)
        rows.append(
            [
                group.replace("_", "-"),
                count(groups[group]),
                f"{sampled / n:.1%}",
                f"{pdf_by_group.get(group, 0) / sampled:.0%}" if sampled else "n/a",
            ]
        )
    not_oa = sample_groups.get("not_oa", 0)
    rows.append(
        [
            "not OA (author manuscripts, text-mining license)",
            count(
                inputs.entrez["totals"]["author_manuscript"]
                - inputs.entrez["totals"]["open_access_and_manuscript"]
            ),
            f"{not_oa / n:.1%}",
            f"{pdf_by_group.get('not_oa', 0) / not_oa:.0%}" if not_oa else "n/a",
        ]
    )
    detail = ", ".join(f"`{term}` {value:,}" for term, value in filters.items())
    detail += (
        f" (Entrez, {inputs.entrez['retrieved'][:10]}; bucket sample of "
        f"{n:,} articles, seed {inputs.metadata['seed']}, {inputs.metadata['retrieved'][:10]})"
    )
    return (
        table(
            [
                "License group",
                "Entrez count",
                "Share of bucket sample",
                "Has a PDF (sample)",
            ],
            rows,
        )
        + f"\n\nFilter counts: {detail}."
    )


def t_prod_capacity(inputs: Inputs) -> str:
    """What production holds per paper today, and how many more papers fit."""
    infra_ = inputs.infra
    papers = inputs.prod["papers"]["n"]
    stores = {
        "Qdrant (vectors)": infra_["qdrant"]["storage_bytes"],
        "Fuseki (TDB2)": infra_["fuseki"]["database_bytes"],
        "DuckLake data files": infra_["storage"]["ducklake_data_dir_bytes"],
        "DuckLake catalog (Postgres)": infra_["storage"]["postgres_database_bytes"][
            "ducklake"
        ],
        "Object storage (PDFs, markdown)": infra_["storage"]["object_store_abi_bytes"],
    }
    total = sum(stores.values())
    rows = [[name, size(value), size(value / papers)] for name, value in stores.items()]
    rows.append(["**All stores**", f"**{size(total)}**", f"**{size(total / papers)}**"])
    free = infra_["host"]["disk_bytes"]["free"]
    rows.append(
        [
            f"Free disk on the {infra_['host']['instance_type']}",
            size(free),
            f"room for about {free / (total / papers):,.0f} more papers",
        ]
    )
    return table([f"Production store ({papers} papers)", "On disk", "Per paper"], rows)


def with_assumption(inputs: Inputs, **changes) -> Inputs:
    changed = copy.copy(inputs)
    changed.assumptions = {**inputs.assumptions, **changes}
    return changed


def t_item_levels(inputs: Inputs, corpus: str) -> str:
    papers = corpus_size(inputs, corpus)
    rows = []
    for model_id in headline_models(inputs):
        cells = [display(inputs, model_id)]
        for level in ("pmc_sample", "prod"):
            varied = with_assumption(inputs, chunk_level=level)
            per = paper(varied, model_id)
            held = infra(varied, per, papers)
            cells += [
                f"{per.items:,.0f}",
                usd((per.embeddings_chunks + per.embeddings_items) * papers),
                usd(held.self_storage + held.self_hosting),
            ]
        rows.append(cells)
    return table(
        [
            "Model",
            "Items/paper (PMC level)",
            "Embeddings",
            "Storage + hosting /mo",
            "Items/paper (prod level)",
            "Embeddings",
            "Storage + hosting /mo",
        ],
        rows,
    )


TABLES = {
    "key-numbers": lambda i: t_key_numbers(i),
    "headline-oa": lambda i: t_headline(i, "oa"),
    "headline-all": lambda i: t_headline(i, "all"),
    "infra-oa": lambda i: t_infra(i, "oa"),
    "infra-all": lambda i: t_infra(i, "all"),
    "time-oa": lambda i: t_time(i, "oa"),
    "time-all": lambda i: t_time(i, "all"),
    "license": t_license,
    "lengths": t_lengths,
    "per-paper": t_per_paper,
    "prompt-tokens": t_prompt_tokens,
    "calibration": t_calibration,
    "calibration-spend": t_calibration_spend,
    "throughput": t_throughput,
    "two-models": lambda i: t_two_models(i, "oa"),
    "per-prompt": t_per_prompt,
    "embedding-scenarios": lambda i: t_embedding_scenarios(i, "oa"),
    "text-scenario": lambda i: t_text_scenario(i, "oa"),
    "per-thousand": t_per_thousand,
    "item-levels": lambda i: t_item_levels(i, "oa"),
    "prod-capacity": t_prod_capacity,
}
#: A generated block: its opening marker, anything up to its closing marker
#: (nothing, when the block is new), and the closing marker.
MARKER = re.compile(r"<!-- table:([a-z0-9-]+) -->\n(?:.*?\n)??<!-- /table:\1 -->", re.S)


def render(inputs: Inputs, report: str) -> str:
    """``report`` with every marked table regenerated."""

    def fill(match: re.Match) -> str:
        name = match.group(1)
        if name not in TABLES:
            raise KeyError(f"the report names an unknown table {name!r}")
        return f"<!-- table:{name} -->\n{TABLES[name](inputs)}\n<!-- /table:{name} -->"

    return MARKER.sub(fill, report)


def calibration_prices(inputs: Inputs) -> dict:
    out = {m: model_price(inputs, m) for m in inputs.prices["models"]}
    return out


def main(argv: list[str]) -> int:
    command = argv[0] if argv else "tables"
    inputs = load()
    if command == "tables":
        for name, make in TABLES.items():
            print(f"## {name}\n\n{make(inputs)}\n")
    elif command == "report":
        REPORT.write_text(render(inputs, REPORT.read_text()))
    elif command == "check":
        current = REPORT.read_text()
        if render(inputs, current) != current:
            print(f"{REPORT.name} is stale: run cost_model.py report", file=sys.stderr)
            return 1
    elif command == "calibration-prices":
        print(json.dumps(calibration_prices(inputs), separators=(",", ":")))
    else:
        print(f"unknown command {command!r}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
