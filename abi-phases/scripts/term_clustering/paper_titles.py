# /// script
# requires-python = ">=3.12"
# dependencies = ["duckdb>=1.3"]
# ///
"""Export one row per paper (id, title, PMID, file name, discovery time).

The report shows paper titles next to a term's central members; the vector
payloads carry only paper ids. Titles are resolved the way the workspace does
it: the PubMed title when the PDF came from PubMed, otherwise the file name.

The catalog is attached read-only. Run it where the DuckLake catalog and data
path are reachable, for example inside the API container (its ``duckdb`` has
the ``ducklake`` and ``postgres`` extensions), with the password in
``PGPASSWORD`` so it never appears in an argument:

    python paper_titles.py --catalog "host=<db host> dbname=<catalog db> user=<user>" \\
        --data-path storage/datasets/ --out /tmp/papers.parquet

``--catalog`` defaults to ``$DUCKLAKE_CATALOG``. Copy the file next to the
dump as ``papers.parquet``.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import duckdb

QUERY = """
SELECT p.paper_id,
       coalesce(nullif(trim(pp.title), ''), p.file_name, p.paper_id) AS title,
       a.pmid,
       p.file_name,
       p.discovered_at
FROM lake.phases_v2.papers p
LEFT JOIN (
    SELECT content_sha256, min(pmid) AS pmid FROM lake.pubmed.artifacts
    WHERE status = 'ready' AND contract_version = 1 AND content_sha256 <> ''
    GROUP BY content_sha256
) a ON a.content_sha256 = p.content_sha256
LEFT JOIN (
    SELECT pmid, any_value(title) AS title FROM lake.pubmed.papers GROUP BY pmid
) pp ON pp.pmid = a.pmid
WHERE p.text_key IS NOT NULL
ORDER BY p.paper_id
"""


def quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--catalog",
        default=os.environ.get("DUCKLAKE_CATALOG"),
        help="libpq conninfo, no password",
    )
    parser.add_argument("--data-path", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if not args.catalog:
        parser.error("no catalog: pass --catalog or set DUCKLAKE_CATALOG")
    if "password" in args.catalog.lower():
        parser.error("keep the password out of --catalog; set PGPASSWORD instead")

    con = duckdb.connect()
    con.execute("LOAD ducklake")
    # ATTACH takes no parameters; quote the two literals instead.
    catalog, data_path = (
        quote(v) for v in (f"ducklake:postgres:{args.catalog}", args.data_path)
    )
    con.execute(f"ATTACH {catalog} AS lake (READ_ONLY, DATA_PATH {data_path})")
    con.execute(f"COPY ({QUERY}) TO {quote(str(args.out))} (FORMAT parquet)")
    (count,) = con.execute(
        f"SELECT count(*) FROM read_parquet({quote(str(args.out))})"
    ).fetchone()
    print(f"wrote {count} papers to {args.out}")


if __name__ == "__main__":
    main()
