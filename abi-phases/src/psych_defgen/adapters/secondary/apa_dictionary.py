"""
Check whether a psychological term has an entry in the
APA Dictionary of Psychology.

This module does not extract or reproduce the APA definition.
It only returns the official APA Dictionary URL when an entry
can be verified.

Upstream rendered the page with Playwright and Chromium. The entry markup it
looked for (``span[xmlns='http://apa.org/dop/enriched']``) is already in the
HTML the server sends, so this adapter fetches the page with ``requests`` and
reads it with BeautifulSoup. The slug, the statuses, the missing-entry and
generic-page detection and the heading search are upstream's.
"""

import re
from urllib.parse import unquote

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 "
    "(Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 "
    "(KHTML, like Gecko) "
    "Chrome/120.0 Safari/537.36"
)

APA_BASE_URL = "https://dictionary.apa.org"


APA_NOT_FOUND_PATTERNS = [
    r"not\s+in\s+the\s+dictionary\s+of\s+psychology",
    r"not\s+in\s+the\s+apa\s+dictionary",
    r"no\s+dictionary\s+entry",
    r"entry\s+was\s+not\s+found",
    r"term\s+was\s+not\s+found",
    r"page\s+not\s+found",
    r"please\s+report\s+to\s+apa",
    r"sorry.{0,200}not\s+in\s+the\s+dictionary",
]


GENERIC_PAGE_TEXT = [
    "apa dictionary of psychology",
    "a trusted reference in the field of psychology",
    "offering more than 25,000",
    "more than 25,000 authoritative entries",
]


def normalize_term(term: str) -> str:
    """
    Normalize a term for text comparison.

    Examples:
        "Social Isolation" -> "social isolation"
        "self-transcendence" -> "self transcendence"
    """

    term = unquote(term)
    term = term.lower().strip()
    term = re.sub(r"[-–—_]+", " ", term)
    term = re.sub(r"[^a-z0-9\s]", "", term)
    term = re.sub(r"\s+", " ", term)

    return term.strip()


def slugify_term(term: str) -> str:
    """
    Convert a term into an APA Dictionary URL slug.

    Examples:
        "loneliness" -> "loneliness"
        "social isolation" -> "social-isolation"
        "self-transcendence" -> "self-transcendence"
    """

    normalized = normalize_term(term)
    return normalized.replace(" ", "-")


def normalize_page_text(text: str) -> str:
    """
    Normalize rendered page text for comparison.
    """

    if not text:
        return ""

    text = text.lower()
    text = re.sub(r"[-–—_]+", " ", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def contains_missing_entry_message(page_text: str) -> bool:
    """
    Check whether the rendered page explicitly indicates
    that the requested term does not have an APA entry.
    """

    if not page_text:
        return False

    return any(
        re.search(
            pattern,
            page_text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        for pattern in APA_NOT_FOUND_PATTERNS
    )


def is_generic_apa_page(page_text: str) -> bool:
    """
    Determine whether the rendered page contains only generic
    APA Dictionary material rather than a specific term entry.
    """

    normalized_text = normalize_page_text(page_text)

    return any(generic_text in normalized_text for generic_text in GENERIC_PAGE_TEXT)


def term_matches_text(term: str, candidate_text: str) -> bool:
    """Check whether a heading exactly matches the requested term."""

    normalized_term = normalize_term(term)
    normalized_candidate = normalize_term(candidate_text)

    return bool(normalized_term and normalized_term == normalized_candidate)


ENTRY_HEADING_SELECTORS = [
    "h1",
    "h2",
    "h3",
    "[role='heading']",
    "[class*='title']",
    "[class*='term']",
    "[class*='headword']",
    "span[xmlns='http://apa.org/dop/enriched']",
]


def find_entry_heading(soup, term: str) -> str | None:
    """
    Find an APA Dictionary entry title matching the requested term.
    """

    for selector in ENTRY_HEADING_SELECTORS:
        for element in soup.select(selector)[:100]:
            text = element.get_text(" ", strip=True)

            if term_matches_text(term, text):
                return text

    return None


def page_text(soup) -> str:
    """The text a browser shows: no scripts, styles or templates."""

    for element in soup(["script", "style", "noscript", "template"]):
        element.decompose()

    body = soup.body or soup

    return body.get_text(" ", strip=True)


def read_apa_page(result: dict, html, term: str) -> dict:
    """
    Classify a loaded APA Dictionary page, as upstream did with
    the rendered page.
    """

    soup = BeautifulSoup(html, "lxml")

    page_title = soup.title.get_text(" ", strip=True) if soup.title else ""

    normalized_body = normalize_page_text(page_text(soup))

    # A clear missing-entry message is the strongest
    # evidence that the term is absent.
    if contains_missing_entry_message(normalized_body):
        return {
            **result,
            "status": "not_found",
            "page_title": page_title,
        }

    # Look for a heading that exactly matches the requested term.
    entry_heading = find_entry_heading(soup, term)

    if entry_heading:
        return {
            **result,
            "status": "found",
            "entry_heading": entry_heading,
            "page_title": page_title,
        }

    # The generic application page does not give enough
    # evidence to claim that the entry is absent.
    if is_generic_apa_page(normalized_body):
        return {
            **result,
            "status": "unavailable",
            "page_title": page_title,
            "error": (
                "The APA Dictionary page loaded, "
                "but a term-specific entry could not "
                "be verified automatically."
            ),
        }

    return {
        **result,
        "status": "unavailable",
        "page_title": page_title,
        "error": (
            "The APA Dictionary page loaded, but the "
            "entry status could not be determined "
            "reliably."
        ),
    }


class ApaDictionary:
    """The ``Dictionary`` port: an HTTP check of the APA Dictionary."""

    def __init__(self, session: requests.Session | None = None, timeout: float = 30.0):
        self.http = session or requests.Session()
        self.timeout = timeout

    def lookup(self, term: str) -> dict:
        """
        Check whether a term has an APA Dictionary entry.

        This function does not extract the APA definition.

        Returns a dictionary with one of these statuses:

        - found:
            A matching APA Dictionary entry was verified.

        - not_found:
            The page explicitly indicated that the
            term does not have an entry.

        - unavailable:
            The page loaded, but the entry could not
            be reliably confirmed.

        - request_error:
            The APA website could not be accessed.
        """

        original_term = term.strip()

        if not original_term:
            raise ValueError("The APA Dictionary term cannot be empty.")

        slug = slugify_term(original_term)
        requested_url = f"{APA_BASE_URL}/{slug}"

        result = {
            "term": original_term,
            "source": "APA Dictionary of Psychology",
            "url": requested_url,
            "status": "unavailable",
        }

        try:
            response = self.http.get(
                requested_url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept-Language": "en-US",
                },
                timeout=self.timeout,
            )
        except requests.Timeout:
            return {
                **result,
                "status": "request_error",
                "error": "The APA Dictionary request timed out.",
            }
        except requests.RequestException as error:
            return {
                **result,
                "status": "request_error",
                "error": (
                    f"The APA Dictionary could not be reached ({type(error).__name__})."
                ),
            }

        status_code = response.status_code

        if status_code == 404:
            return {
                **result,
                "status": "not_found",
                "http_status": status_code,
            }

        if status_code >= 400:
            return {
                **result,
                "status": "request_error",
                "http_status": status_code,
                "error": (f"The APA Dictionary returned HTTP status {status_code}."),
            }

        result["url"] = response.url or requested_url
        result["http_status"] = status_code

        return read_apa_page(result, response.content, original_term)
