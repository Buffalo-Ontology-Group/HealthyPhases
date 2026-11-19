# /// script
# requires-python = ">=3.12"
# ///
"""PubMed Central corpus facts: size, license groups, growth, PDF availability.

Three sources, three subcommands (run from ``abi-phases/``)::

    S=scripts/pmc_ingestion_cost
    uv run $S/collect_pmc.py entrez > $S/measurements/pmc_entrez.json
    uv run $S/collect_pmc.py inventory --cache storage/spikes/pmc_ingestion_cost/inventory \\
        > $S/measurements/pmc_inventory.json
    uv run $S/collect_pmc.py metadata-sample --cache storage/spikes/pmc_ingestion_cost/inventory \\
        --size 3000 --seed 855 > $S/measurements/pmc_metadata_sample.json

* ``entrez``: counts from NCBI E-utilities ``esearch db=pmc`` (all of PMC, the
  open access filter and each license filter, author manuscripts) and, per
  30-day window over the last 12 months, the articles whose PMC live date
  falls in it. At most 3 requests per second, as NCBI asks of callers without
  an API key; set ``NCBI_EMAIL`` to send a contact address with each request.
* ``inventory``: the daily S3 inventory of the ``pmc-oa-opendata`` bucket (the
  PMC Article Datasets our PubMed Library downloads from). Counts article
  versions and articles, and the versions added between the newest snapshot
  and the one 30 days before it.
* ``metadata-sample``: a seeded random sample of articles from the newest
  inventory, reading each one's ``metadata/<PMCID>.<version>.json`` (license,
  author manuscript, open access subset, whether a PDF is distributed).

Standard library only.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import os
import random
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
BUCKET = "https://pmc-oa-opendata.s3.amazonaws.com"
INVENTORY = "inventory-reports/pmc-oa-opendata/metadata/"
TOOL = "pmc-cost-model"
#: NCBI's limit without an API key is 3 requests per second.
ENTREZ_INTERVAL = 0.34

#: PMC's license filters, grouped as PMC groups its open access subset:
#: commercial use allowed, non-commercial use only, and other.
LICENSE_FILTERS = {
    "commercial": [
        "cc0 license[filter]",
        "cc by license[filter]",
        "cc by-sa license[filter]",
        "cc by-nd license[filter]",
    ],
    "non_commercial": [
        "cc by-nc license[filter]",
        "cc by-nc-sa license[filter]",
        "cc by-nc-nd license[filter]",
    ],
}
#: Bucket ``license_code`` values in the same groups. Anything else is "other".
LICENSE_GROUPS = {
    "CC0": "commercial",
    "CC BY": "commercial",
    "CC BY-SA": "commercial",
    "CC BY-ND": "commercial",
    "CC BY-NC": "non_commercial",
    "CC BY-NC-SA": "non_commercial",
    "CC BY-NC-ND": "non_commercial",
}
KEY = re.compile(r"^metadata/(PMC\d+)\.(\d+)\.json$")


# --- parsers (pure, tested) -------------------------------------------------


def esearch_count(body: bytes) -> int:
    """The ``count`` of an ``esearch`` JSON reply."""
    result = json.loads(body)["esearchresult"]
    if "ERROR" in result:
        raise ValueError(f"esearch error: {result['ERROR']}")
    return int(result["count"])


def windows(today: date, days: int = 30, count: int = 12) -> list[tuple[date, date]]:
    """``count`` consecutive ``days``-day windows ending yesterday, newest first.

    Today is left out: its articles are still being loaded.
    """
    end = today - timedelta(days=1)
    spans = []
    for _ in range(count):
        start = end - timedelta(days=days - 1)
        spans.append((start, end))
        end = start - timedelta(days=1)
    return spans


def inventory_rows(text: str) -> list[tuple[str, int, str]]:
    """``(pmcid, version, last_modified)`` for every metadata key of an inventory CSV.

    Rows are ``"bucket","key","last modified","etag"``; keys that are not an
    article version's metadata object are skipped.
    """
    rows = []
    for record in csv.reader(io.StringIO(text)):
        if len(record) < 3:
            continue
        match = KEY.match(urllib.parse.unquote(record[1]))
        if match:
            rows.append((match.group(1), int(match.group(2)), record[2]))
    return rows


def snapshot_names(listing: bytes) -> list[str]:
    """Inventory snapshot folders (``2026-11-18T01-00Z``) in an S3 listing, oldest first."""
    prefixes = re.findall(rb"<Prefix>([^<]*)</Prefix>", listing)
    names = []
    for prefix in prefixes:
        name = prefix.decode().removeprefix(INVENTORY).rstrip("/")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}Z", name):
            names.append(name)
    return sorted(names)


def next_token(listing: bytes) -> str | None:
    """The continuation token of a truncated ListObjectsV2 page, or None on the last page."""
    if re.search(rb"<IsTruncated>true</IsTruncated>", listing):
        match = re.search(
            rb"<NextContinuationToken>([^<]+)</NextContinuationToken>", listing
        )
        if not match:
            raise ValueError("a truncated listing carries no continuation token")
        return match.group(1).decode()
    return None


def classify(metadata: dict) -> dict:
    """What our ingestion cares about in one article version's metadata."""
    code = (metadata.get("license_code") or "").strip()
    return {
        "license_code": code or None,
        "license_group": LICENSE_GROUPS.get(code.upper(), "other")
        if metadata.get("is_pmc_openaccess")
        else "not_oa",
        "open_access": bool(metadata.get("is_pmc_openaccess")),
        "manuscript": bool(metadata.get("is_manuscript")),
        "historical_ocr": bool(metadata.get("is_historical_ocr")),
        "retracted": bool(metadata.get("is_retracted")),
        "has_pdf": bool(metadata.get("pdf_url")),
        "has_text": bool(metadata.get("text_url")),
    }


# --- network ----------------------------------------------------------------


def fetch(url: str, attempts: int = 4) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": TOOL})
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.read()
        except OSError:
            if attempt == attempts:
                raise
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


class Entrez:
    def __init__(self) -> None:
        self._last = 0.0
        self.requests = 0

    def count(self, term: str) -> int:
        wait = ENTREZ_INTERVAL - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        params = {"db": "pmc", "term": term, "rettype": "count", "retmode": "json"}
        params["tool"] = TOOL
        if os.environ.get("NCBI_EMAIL"):
            params["email"] = os.environ["NCBI_EMAIL"]
        body = fetch(f"{EUTILS}/esearch.fcgi?{urllib.parse.urlencode(params)}")
        self._last = time.monotonic()
        self.requests += 1
        return esearch_count(body)


def entrez(today: date) -> dict:
    client = Entrez()
    totals = {
        "all": client.count("all[filter]"),
        "open_access": client.count("open access[filter]"),
        "author_manuscript": client.count("author manuscript[filter]"),
        "open_access_and_manuscript": client.count(
            "open access[filter] AND author manuscript[filter]"
        ),
        "historical_ocr": client.count("historical ocr[filter]"),
    }
    licenses = {
        term: client.count(term) for terms in LICENSE_FILTERS.values() for term in terms
    }
    any_license = " OR ".join(licenses)
    groups = {
        group: sum(licenses[t] for t in terms)
        for group, terms in LICENSE_FILTERS.items()
    }
    groups["other"] = client.count(f"open access[filter] NOT ({any_license})")

    growth = []
    for start, end in windows(today):
        span = f"{start:%Y/%m/%d}:{end:%Y/%m/%d}[PmcLiveDate]"
        growth.append(
            {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "all": client.count(span),
                "open_access": client.count(f"{span} AND open access[filter]"),
            }
        )
    return {
        "retrieved": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": f"{EUTILS}/esearch.fcgi?db=pmc",
        "date_field": "PmcLiveDate (the date an article went live in PMC)",
        "totals": totals,
        "license_filters": licenses,
        "open_access_by_license_group": groups,
        "per_30_days": growth,
        "requests": client.requests,
    }


def inventory_snapshot(name: str, cache: Path) -> list[tuple[str, int, str]]:
    """Every metadata key of one inventory snapshot, downloading it once into ``cache``."""
    manifest = json.loads(fetch(f"{BUCKET}/{INVENTORY}{name}/manifest.json"))
    rows: list[tuple[str, int, str]] = []
    for entry in manifest["files"]:
        target = cache / name / Path(entry["key"]).name
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(fetch(f"{BUCKET}/{entry['key']}"))
        rows.extend(inventory_rows(gzip.decompress(target.read_bytes()).decode()))
    return rows


def snapshots(fetch_page=None) -> list[str]:
    """Every inventory snapshot, oldest first, following the listing's pages."""
    fetch_page = fetch_page or fetch
    names: list[str] = []
    token = None
    while True:
        params = {"list-type": "2", "delimiter": "/", "prefix": INVENTORY}
        if token:
            params["continuation-token"] = token
        listing = fetch_page(f"{BUCKET}/?{urllib.parse.urlencode(params)}")
        names += snapshot_names(listing)
        token = next_token(listing)
        if not token:
            return sorted(names)


def inventory(cache: Path, days: int = 30) -> dict:
    names = snapshots()
    newest = names[-1]
    newest_day = datetime.strptime(newest[:10], "%Y-%m-%d").date()
    target = newest_day - timedelta(days=days)
    earlier = min(
        names,
        key=lambda n: abs((datetime.strptime(n[:10], "%Y-%m-%d").date() - target).days),
    )
    rows = inventory_snapshot(newest, cache)
    before = inventory_snapshot(earlier, cache)
    versions = {(pmcid, version) for pmcid, version, _ in rows}
    earlier_versions = {(pmcid, version) for pmcid, version, _ in before}
    articles = {pmcid for pmcid, _ in versions}
    earlier_articles = {pmcid for pmcid, _ in earlier_versions}
    span = (newest_day - datetime.strptime(earlier[:10], "%Y-%m-%d").date()).days
    return {
        "retrieved": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": f"{BUCKET}/{INVENTORY}<snapshot>/manifest.json",
        "snapshot": newest,
        "article_versions": len(versions),
        "articles": len(articles),
        "versions_per_article": Counter(Counter(p for p, _ in versions).values()),
        "compared_with": earlier,
        "days_between": span,
        "added_versions": len(versions - earlier_versions),
        "added_articles": len(articles - earlier_articles),
        "removed_versions": len(earlier_versions - versions),
    }


def latest_versions(rows: list[tuple[str, int, str]]) -> dict[str, int]:
    """The newest version of every article."""
    latest: dict[str, int] = {}
    for pmcid, version, _ in rows:
        if version > latest.get(pmcid, 0):
            latest[pmcid] = version
    return latest


def metadata_sample(cache: Path, size: int, seed: int) -> dict:
    newest = snapshots()[-1]
    latest = latest_versions(inventory_snapshot(newest, cache))
    picked = random.Random(seed).sample(sorted(latest), size)
    records = []
    for pmcid in picked:
        key = f"metadata/{pmcid}.{latest[pmcid]}.json"
        metadata = json.loads(fetch(f"{BUCKET}/{key}"))
        records.append({"pmcid": pmcid, "version": latest[pmcid], **classify(metadata)})
    summary = {
        field: Counter(str(r[field]) for r in records)
        for field in (
            "license_group",
            "license_code",
            "open_access",
            "manuscript",
            "historical_ocr",
            "has_pdf",
            "has_text",
        )
    }
    pdf_by_group = {
        group: sum(r["has_pdf"] for r in records if r["license_group"] == group)
        for group in summary["license_group"]
    }
    return {
        "retrieved": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": f"{BUCKET}/metadata/<PMCID>.<version>.json",
        "snapshot": newest,
        "seed": seed,
        "size": size,
        "population_articles": len(latest),
        "summary": summary,
        "has_pdf_by_license_group": pdf_by_group,
        "records": records,
    }


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("entrez")
    for name in ("inventory", "metadata-sample"):
        command = sub.add_parser(name)
        command.add_argument("--cache", type=Path, required=True)
        if name == "metadata-sample":
            command.add_argument("--size", type=int, default=3000)
            command.add_argument("--seed", type=int, default=855)
    args = parser.parse_args(argv)
    if args.command == "entrez":
        out = entrez(date.today())
    elif args.command == "inventory":
        out = inventory(args.cache)
    else:
        out = metadata_sample(args.cache, args.size, args.seed)
    json.dump(out, sys.stdout, indent=1, default=str)


if __name__ == "__main__":
    main(sys.argv[1:])
