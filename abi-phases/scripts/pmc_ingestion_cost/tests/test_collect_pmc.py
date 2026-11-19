"""The PMC collectors' parsers, on canned replies: no network.

Run with ``make test-pmc-cost``.
"""

import gzip
import json
import sys
import urllib.parse
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import collect_pmc  # noqa: E402
from collect_pmc import (  # noqa: E402
    classify,
    esearch_count,
    inventory_rows,
    latest_versions,
    snapshot_names,
    windows,
)


def test_esearch_count_reads_the_count():
    body = json.dumps({"esearchresult": {"count": "8402308", "retmax": "0"}}).encode()
    assert esearch_count(body) == 8402308


def test_esearch_count_raises_on_an_error_reply():
    body = json.dumps({"esearchresult": {"ERROR": "Invalid query"}}).encode()
    with pytest.raises(ValueError, match="Invalid query"):
        esearch_count(body)


def test_windows_are_consecutive_30_day_spans_ending_yesterday():
    spans = windows(date(2026, 11, 19), days=30, count=3)
    assert spans[0] == (date(2026, 10, 20), date(2026, 11, 18))
    assert spans[1] == (date(2026, 9, 20), date(2026, 10, 19))
    assert spans[2][1] == date(2026, 9, 19)
    for start, end in spans:
        assert (end - start).days == 29


def test_inventory_rows_keep_only_metadata_keys():
    text = "\n".join(
        [
            '"pmc-oa-opendata","metadata/PMC10000000.1.json","2026-11-01T00:00:00.000Z","abc"',
            '"pmc-oa-opendata","metadata/PMC123.2.json","2026-11-02T00:00:00.000Z","def"',
            '"pmc-oa-opendata","PMC123.2/PMC123.2.pdf","2026-11-02T00:00:00.000Z","ghi"',
            '"pmc-oa-opendata","metadata/","2026-11-02T00:00:00.000Z","jkl"',
        ]
    )
    assert inventory_rows(text) == [
        ("PMC10000000", 1, "2026-11-01T00:00:00.000Z"),
        ("PMC123", 2, "2026-11-02T00:00:00.000Z"),
    ]


def test_latest_versions_keeps_the_highest_version():
    rows = [("PMC1", 1, ""), ("PMC1", 3, ""), ("PMC1", 2, ""), ("PMC2", 1, "")]
    assert latest_versions(rows) == {"PMC1": 3, "PMC2": 1}


def test_snapshot_names_ignore_data_and_hive_folders():
    listing = (
        b"<ListBucketResult>"
        b"<CommonPrefixes><Prefix>inventory-reports/pmc-oa-opendata/metadata/2026-11-18T01-00Z/</Prefix></CommonPrefixes>"
        b"<CommonPrefixes><Prefix>inventory-reports/pmc-oa-opendata/metadata/data/</Prefix></CommonPrefixes>"
        b"<CommonPrefixes><Prefix>inventory-reports/pmc-oa-opendata/metadata/2026-10-19T01-00Z/</Prefix></CommonPrefixes>"
        b"<CommonPrefixes><Prefix>inventory-reports/pmc-oa-opendata/metadata/hive/</Prefix></CommonPrefixes>"
        b"</ListBucketResult>"
    )
    assert snapshot_names(listing) == ["2026-10-19T01-00Z", "2026-11-18T01-00Z"]


@pytest.mark.parametrize(
    ("code", "group"),
    [
        ("CC BY", "commercial"),
        ("CC0", "commercial"),
        ("CC BY-NC-ND", "non_commercial"),
        ("", "other"),
    ],
)
def test_classify_groups_open_access_licenses(code, group):
    record = classify(
        {"is_pmc_openaccess": True, "license_code": code, "pdf_url": "s3://x"}
    )
    assert record["license_group"] == group
    assert record["has_pdf"] is True


def test_classify_marks_manuscripts_outside_the_open_access_subset():
    record = classify(
        {
            "is_pmc_openaccess": False,
            "is_manuscript": True,
            "license_code": "TDM",
            "pdf_url": None,
        }
    )
    assert record["license_group"] == "not_oa"
    assert record["manuscript"] is True
    assert record["has_pdf"] is False


def test_snapshots_follow_every_page_of_the_listing():
    prefix = b"inventory-reports/pmc-oa-opendata/metadata/"
    pages = {
        None: b"<r><IsTruncated>true</IsTruncated><NextContinuationToken>t+1/=</NextContinuationToken>"
        b"<CommonPrefixes><Prefix>"
        + prefix
        + b"2026-11-18T01-00Z/</Prefix></CommonPrefixes></r>",
        "t+1/=": b"<r><IsTruncated>false</IsTruncated>"
        b"<CommonPrefixes><Prefix>"
        + prefix
        + b"2026-10-19T01-00Z/</Prefix></CommonPrefixes></r>",
    }
    requested = []

    def fake_fetch(url):
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        token = query.get("continuation-token")
        requested.append(token)
        return pages[token]

    assert collect_pmc.snapshots(fake_fetch) == [
        "2026-10-19T01-00Z",
        "2026-11-18T01-00Z",
    ]
    assert requested == [None, "t+1/="]


def test_a_truncated_listing_without_a_token_is_an_error():
    with pytest.raises(ValueError):
        collect_pmc.next_token(b"<r><IsTruncated>true</IsTruncated></r>")


def test_inventory_snapshot_reads_cached_files_without_network(tmp_path, monkeypatch):
    csv = '"pmc-oa-opendata","metadata/PMC7.1.json","2026-11-01","e"\n'
    (tmp_path / "2026-11-18T01-00Z").mkdir()
    (tmp_path / "2026-11-18T01-00Z" / "part.csv.gz").write_bytes(
        gzip.compress(csv.encode())
    )
    manifest = {"files": [{"key": "inventory-reports/x/data/part.csv.gz"}]}

    def fake_fetch(url):
        assert url.endswith("manifest.json"), f"unexpected download {url}"
        return json.dumps(manifest).encode()

    monkeypatch.setattr(collect_pmc, "fetch", fake_fetch)
    assert collect_pmc.inventory_snapshot("2026-11-18T01-00Z", tmp_path) == [
        ("PMC7", 1, "2026-11-01")
    ]
