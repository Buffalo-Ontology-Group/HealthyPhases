"""PubMed and PMC through NCBI E-utilities, parsed with Biopython's Entrez.

Upstream called ``Bio.Entrez.esearch/elink/efetch``, which configure email and
key as module globals and open URLs with no timeout. This adapter sends the
same requests with ``requests`` instead, so that it can:

- set a timeout and retry 429 and 5xx responses with a back-off;
- keep within NCBI's 3 requests/s (10 with an API key), across threads;
- send email and key in a POST body, so they never appear in a URL, and so in
  no error message or traceback.

The responses are parsed with ``Bio.Entrez.read``, as upstream's were, and the
record handling of ``pubmed_search.py``, ``get_pmc_articles.py`` and
``retrieve_abstracts.py`` is upstream's.
"""

import io
import re
import threading
import time
from typing import Any

import requests
from Bio import Entrez
from naas_abi_core import logger

from psych_defgen.domain.errors import ServiceError
from psych_defgen.domain.parse_fulltext import parse_pmc_xml

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
TOOL = "healthyphases_psych_defgen"

# NCBI allows 3 requests/s per client without a key, 10 with one. Biopython's
# own pacing (0.37 s / 0.1 s) is kept, shared by every instance in the process.
_PACE_LOCK = threading.Lock()
_last_request = 0.0


def _pace(interval: float, clock=time.monotonic, sleep=time.sleep) -> None:
    global _last_request
    with _PACE_LOCK:
        wait = _last_request + interval - clock()
        if wait > 0:
            sleep(wait)
        _last_request = clock()


class NcbiError(ServiceError):
    pass


class NcbiEntrez:
    """The ``Literature`` port on NCBI E-utilities."""

    def __init__(
        self,
        *,
        email: str,
        api_key: str | None = None,
        session: requests.Session | None = None,
        timeout: float = 60.0,
        attempts: int = 4,
        sleep=time.sleep,
    ):
        if not email:
            raise ValueError("NCBI requires an email address (NCBI_EMAIL).")
        self._email = email
        self._api_key = api_key or None
        self.http = session or requests.Session()
        self.timeout = timeout
        self.attempts = attempts
        self.sleep = sleep
        self.interval = 0.1 if self._api_key else 0.37

    # -- HTTP ---------------------------------------------------------------

    def _request(self, endpoint: str, what: str, **params: Any) -> bytes:
        params = {key: value for key, value in params.items() if value is not None}
        params.update(tool=TOOL, email=self._email)
        if self._api_key:
            params["api_key"] = self._api_key
        url = f"{EUTILS}/{endpoint}.fcgi"

        for attempt in range(self.attempts):
            last = attempt == self.attempts - 1
            _pace(self.interval, sleep=self.sleep)
            try:
                response = self.http.post(url, data=params, timeout=self.timeout)
            except requests.Timeout:
                if last:
                    raise NcbiError(
                        f"NCBI {what} timed out after {self.attempts} attempts; "
                        "retry later."
                    ) from None
            except requests.RequestException as error:
                if last:
                    raise NcbiError(
                        f"NCBI {what} could not reach NCBI "
                        f"({type(error).__name__}); retry later."
                    ) from None
            else:
                status = response.status_code
                if status == 200:
                    return response.content
                if status != 429 and status < 500:
                    raise NcbiError(
                        f"NCBI {what} was refused (HTTP {status}); "
                        "check NCBI_EMAIL and NCBI_API_KEY."
                        if status in (400, 401, 403)
                        else f"NCBI {what} failed (HTTP {status})."
                    )
                if last:
                    raise NcbiError(
                        f"NCBI {what} failed (HTTP {status}) after "
                        f"{self.attempts} attempts; retry later."
                    )
            self.sleep(2**attempt)
        raise NcbiError(f"NCBI {what} failed.")  # pragma: no cover

    def _read(self, endpoint: str, what: str, **params: Any) -> Any:
        content = self._request(endpoint, what, **params)
        try:
            return Entrez.read(io.BytesIO(content))
        except Exception as error:  # Bio raises several parser errors
            raise NcbiError(
                f"NCBI {what} returned a response that could not be parsed "
                f"({type(error).__name__})."
            ) from None

    # -- Literature port ----------------------------------------------------

    def search(self, term: str, max_results: int) -> list[str]:
        """Upstream's ``search_pubmed``."""
        query = f'"{term}"[Title/Abstract]'

        record = self._read(
            "esearch",
            "PubMed search",
            db="pubmed",
            term=query,
            retmax=max_results,
            sort="relevance",
        )

        return [str(pmid) for pmid in record["IdList"]]

    def pmc_ids(self, pmids: list[str]) -> dict[str, str]:
        """Upstream's ``get_pmc_ids``: one lookup per PMID, failures skipped."""
        if not pmids:
            return {}

        pmid_to_pmcid = {}

        for pmid in pmids:
            try:
                records = self._read(
                    "elink",
                    "PMC lookup",
                    dbfrom="pubmed",
                    db="pmc",
                    id=str(pmid),
                    linkname="pubmed_pmc",
                )
            except NcbiError as error:
                logger.warning(
                    f"psych_defgen: could not retrieve PMC ID for PMID {pmid}: {error}"
                )
                continue

            if not records:
                continue

            link_sets = records[0].get("LinkSetDb", [])

            if not link_sets:
                continue

            links = link_sets[0].get("Link", [])

            if not links:
                continue

            pmid_to_pmcid[str(pmid)] = f"PMC{links[0]['Id']}"

        return pmid_to_pmcid

    def full_text(self, pmcid: str) -> tuple[str, str]:
        """Upstream's ``get_full_text_from_pmcid``."""
        xml_data = self._request(
            "efetch",
            f"full text of {pmcid}",
            db="pmc",
            id=pmcid.replace("PMC", ""),
            rettype="full",
            retmode="xml",
        )

        return parse_pmc_xml(xml_data)

    def abstracts(self, pmids: list[str]) -> list[dict[str, Any]]:
        """Upstream's ``fetch_pubmed_abstracts``."""
        if not pmids:
            return []

        records = self._read(
            "efetch",
            "PubMed abstracts",
            db="pubmed",
            id=",".join(str(pmid) for pmid in pmids),
            rettype="medline",
            retmode="xml",
        )

        abstract_records = []

        for pubmed_article in records.get("PubmedArticle", []):
            medline_citation = pubmed_article.get("MedlineCitation", {})
            article = medline_citation.get("Article", {})

            pmid = str(medline_citation.get("PMID", "")).strip()
            title = str(article.get("ArticleTitle", "Title unavailable")).strip()

            abstract_text = _extract_abstract_text(
                article.get("Abstract", {}).get("AbstractText", [])
            )

            if not abstract_text:
                continue

            authors = _extract_authors(article.get("AuthorList", []))
            journal = str(article.get("Journal", {}).get("Title", "")).strip()
            year = _extract_publication_year(pubmed_article)

            abstract_records.append(
                {
                    "pmid": pmid or None,
                    "title": title,
                    "authors": authors,
                    "journal": journal or None,
                    "year": year,
                    "abstract": abstract_text,
                    "source": "PubMed abstract",
                    "pubmed_url": (
                        f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None
                    ),
                }
            )

        return abstract_records

    def authors(self, pmids: list[str]) -> dict[str, list[str]]:
        """Upstream's ``fetch_pubmed_authors``."""
        if not pmids:
            return {}

        records = self._read(
            "efetch",
            "PubMed authors",
            db="pubmed",
            id=",".join(map(str, pmids)),
            retmode="xml",
        )

        authors_by_pmid = {}

        for article in records.get("PubmedArticle", []):
            citation = article["MedlineCitation"]
            pmid = str(citation["PMID"])

            authors = []

            for author in citation["Article"].get("AuthorList", []):
                collective_name = author.get("CollectiveName")

                if collective_name:
                    authors.append(str(collective_name))
                    continue

                first_name = author.get("ForeName", "")
                last_name = author.get("LastName", "")

                full_name = f"{first_name} {last_name}".strip()

                if full_name:
                    authors.append(full_name)

            authors_by_pmid[pmid] = authors

        return authors_by_pmid


def _extract_authors(author_list):
    """Extract readable author names from PubMed author records."""
    authors = []

    for author in author_list or []:
        collective_name = author.get("CollectiveName")

        if collective_name:
            authors.append(str(collective_name))
            continue

        last_name = author.get("LastName")
        initials = author.get("Initials")
        fore_name = author.get("ForeName")

        if last_name and initials:
            authors.append(f"{last_name} {initials}")
        elif fore_name and last_name:
            authors.append(f"{fore_name} {last_name}")
        elif last_name:
            authors.append(str(last_name))

    return authors


def _extract_abstract_text(abstract_sections):
    """Combine PubMed abstract sections, preserving section labels."""
    sections = []

    for section in abstract_sections or []:
        text = str(section).strip()

        if not text:
            continue

        label = getattr(section, "attributes", {}).get("Label")
        sections.append(f"{label}: {text}" if label else text)

    return "\n".join(sections)


def _extract_publication_year(article_record):
    """Extract the publication year from PubMed journal date metadata."""
    pub_date = (
        article_record.get("MedlineCitation", {})
        .get("Article", {})
        .get("Journal", {})
        .get("JournalIssue", {})
        .get("PubDate", {})
    )

    year = pub_date.get("Year")

    if year:
        return str(year)

    medline_date = pub_date.get("MedlineDate", "")
    match = re.search(r"\b(?:19|20)\d{2}\b", str(medline_date))

    return match.group(0) if match else None
