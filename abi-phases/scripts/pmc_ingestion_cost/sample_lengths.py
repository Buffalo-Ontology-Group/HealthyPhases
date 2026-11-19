"""How long PMC papers are, as PMC's text and as our pipeline renders their PDFs.

Draws from the seeded metadata sample (``collect_pmc.py metadata-sample``), in
its order:

* ``--texts`` articles that have a plain-text version: words (whitespace
  split), characters, ``o200k_base`` and ``cl100k_base`` tokens, and the chunks
  our ``WindowChunker`` (512/128) cuts from that text;
* ``--pdfs`` articles that distribute a PDF: bytes, pages, then our
  ``PdfTextRenderer`` (the ingest step's renderer) on it, timed in CPU seconds,
  and the same counts over the rendered markdown.

With ``--chunks-out``, one seeded chunk of each rendered PDF is also kept, for
``calibrate.py --chunks-file`` (the calibration on PMC text).

PDFs are downloaded first, in parallel, and rendered one at a time, so the CPU
time is the renderer's alone. Writes per-paper numbers and their summary (no
text). Run from ``abi-phases/`` in the project environment::

    uv run python scripts/pmc_ingestion_cost/sample_lengths.py \\
        scripts/pmc_ingestion_cost/measurements/pmc_metadata_sample.json \\
        --texts 1000 --pdfs 200 > scripts/pmc_ingestion_cost/measurements/pmc_lengths.json
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import random
import statistics
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pymupdf
import tiktoken

from phases_v2.chunking.adapters.secondary.WindowChunker import WindowChunker
from phases_v2.papers.adapters.secondary.PdfTextRenderer import PdfTextRenderer
from phases_v2.papers.interfaces import RenderFailed

BUCKET = "https://pmc-oa-opendata.s3.amazonaws.com"
#: The ingestion's own download limit would cut nothing a paper needs; this
#: only guards the sampler against a pathological object.
MAX_BYTES = 200 * 1024 * 1024


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "pmc-cost-model"})
    for attempt in range(1, 5):
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                return response.read(MAX_BYTES)
        except OSError:
            if attempt == 4:
                raise
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


def cpu_model() -> str:
    """The CPU the render times were measured on (Linux)."""
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor()


def describe(values: list[float]) -> dict:
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
        "min": ordered[0],
        "max": ordered[-1],
    }


class Counter:
    def __init__(self) -> None:
        self.o200k = tiktoken.get_encoding("o200k_base")
        self.cl100k = tiktoken.get_encoding("cl100k_base")
        self.chunker = WindowChunker(512, 128)

    def text(self, text: str) -> dict:
        return {
            "chars": len(text),
            "words": len(text.split()),
            "o200k_tokens": len(self.o200k.encode(text, disallowed_special=())),
            "cl100k_tokens": len(self.cl100k.encode(text, disallowed_special=())),
            "chunks": len(self.chunker.split(text)),
        }


def texts(records: list[dict], size: int, counter: Counter) -> list[dict]:
    chosen = [r for r in records if r["has_text"]][:size]

    def one(record: dict) -> dict:
        name = f"{record['pmcid']}.{record['version']}"
        body = fetch(f"{BUCKET}/{name}/{name}.txt").decode("utf-8", errors="replace")
        return {"pmcid": record["pmcid"], **counter.text(body)}

    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(one, chosen))


def pdfs(
    records: list[dict],
    size: int,
    counter: Counter,
    keep: list[dict] | None = None,
    seed: int = 855,
) -> list[dict]:
    chosen = [r for r in records if r["has_pdf"]][:size]

    def download(record: dict) -> bytes:
        name = f"{record['pmcid']}.{record['version']}"
        return fetch(f"{BUCKET}/{name}/{name}.pdf")

    with ThreadPoolExecutor(max_workers=8) as pool:
        contents = list(pool.map(download, chosen))

    renderer = PdfTextRenderer()
    rows = []
    for record, content in zip(chosen, contents, strict=True):
        row = {"pmcid": record["pmcid"], "bytes": len(content)}
        try:
            with pymupdf.open(stream=content, filetype="pdf") as document:
                row["pages"] = document.page_count
        except Exception as failure:  # noqa: BLE001 - recorded, not fatal
            row["pages"] = None
            row["error"] = f"unreadable: {failure}"[:200]
        cpu, wall = time.process_time(), time.perf_counter()
        try:
            markdown = renderer.render(content, f"{record['pmcid']}.pdf")
        except RenderFailed as failure:
            row["error"] = str(failure)[:200]
            rows.append(row)
            continue
        row["render_cpu_seconds"] = time.process_time() - cpu
        row["render_wall_seconds"] = time.perf_counter() - wall
        rows.append({**row, **counter.text(markdown)})
        if keep is not None:
            chunks = counter.chunker.split(markdown)
            if chunks:
                seq = random.Random(f"{seed}:{record['pmcid']}").randrange(len(chunks))
                keep.append(
                    {"chunk_id": f"{record['pmcid']}:{seq}", "text": chunks[seq][0]}
                )
    return rows


def summarise(rows: list[dict], fields: tuple[str, ...]) -> dict:
    return {
        field: describe([r[field] for r in rows if r.get(field) is not None])
        for field in fields
    }


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("metadata_sample", type=Path)
    parser.add_argument("--texts", type=int, default=1000)
    parser.add_argument("--pdfs", type=int, default=200)
    parser.add_argument(
        "--chunks-out",
        type=Path,
        help="also write one seeded chunk per rendered PDF here (JSON; holds paper text, "
        "so keep it under the gitignored storage/)",
    )
    args = parser.parse_args(argv)
    sample = json.loads(args.metadata_sample.read_text())
    counter = Counter()
    text_rows = texts(sample["records"], args.texts, counter)
    kept: list[dict] = []
    pdf_rows = pdfs(sample["records"], args.pdfs, counter, kept, sample["seed"])
    if args.chunks_out:
        args.chunks_out.write_text(json.dumps(kept, ensure_ascii=False))
    counted = ("chars", "words", "o200k_tokens", "cl100k_tokens", "chunks")
    json.dump(
        {
            "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "source": f"{BUCKET}/<PMCID>.<version>/<PMCID>.<version>.(txt|pdf)",
            "drawn_from": f"pmc_metadata_sample.json (seed {sample['seed']}, "
            f"snapshot {sample['snapshot']}), in its order",
            "machine": {
                "cpu": cpu_model(),
                "cores": os.cpu_count(),
                "python": platform.python_version(),
            },
            "pmc_text": {"summary": summarise(text_rows, counted), "papers": text_rows},
            "pdf_rendered": {
                "summary": summarise(
                    pdf_rows,
                    (
                        "bytes",
                        "pages",
                        "render_cpu_seconds",
                        "render_wall_seconds",
                        *counted,
                    ),
                ),
                "failed": sum(1 for r in pdf_rows if "error" in r),
                "papers": pdf_rows,
            },
        },
        sys.stdout,
        indent=1,
    )


if __name__ == "__main__":
    main(sys.argv[1:])
