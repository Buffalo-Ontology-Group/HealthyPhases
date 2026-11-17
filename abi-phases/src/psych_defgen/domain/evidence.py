"""Evidence records and fixed result texts from upstream's ``pipeline.py``."""

NO_DEFINITION_FOUND = (
    "No explicit definition matching the selection criteria was "
    "identified in the retrieved literature."
)

INSUFFICIENT_EVIDENCE = "Insufficient evidence to generate a definition."


def create_pmc_evidence_record(text, title, pmid, pmcid):
    """
    Create a PMC full-text evidence record with article metadata and source URLs.
    """
    return {
        "text": text,
        "title": title,
        "authors": [],
        "journal": None,
        "year": None,
        "pmid": pmid,
        "pmcid": pmcid,
        "source": "PMC full text",
        "pubmed_url": (
            f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
            if pmid else None
        ),
        "url": (
            f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/"
            if pmcid else None
        ),
    }


def create_abstract_evidence_record(record):
    """
    Convert a PubMed abstract record into the standard evidence format used for retrieval.
    """
    return {
        "text": record.get("abstract", ""),
        "title": record.get("title"),
        "authors": record.get("authors", []),
        "journal": record.get("journal"),
        "year": record.get("year"),
        "pmid": record.get("pmid"),
        "pmcid": None,
        "source": record.get("source", "PubMed abstract"),
        "pubmed_url": record.get("pubmed_url"),
        "url": None,
    }
