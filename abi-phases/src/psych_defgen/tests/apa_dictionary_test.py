import pytest
import requests

from psych_defgen.adapters.secondary.apa_dictionary import ApaDictionary, slugify_term

HEADER = """
<header><h1>APA Dictionary of Psychology</h1>
<nav>APA.org APA Style</nav></header>
"""

ENTRY_PAGE = f"""<html><head><title>loneliness – APA Dictionary of Psychology</title>
<script>var notice = "This term was not found";</script></head>
<body>{HEADER}
<main><div class="entry">
<span xmlns="http://apa.org/dop/enriched">loneliness</span>
<p>n. affective and cognitive discomfort or uneasiness from being alone.</p>
</div></main></body></html>"""

GENERIC_PAGE = f"""<html><head><title>APA Dictionary of Psychology</title></head>
<body>{HEADER}<p>More than 25,000 authoritative entries across 90 subfields
of psychology.</p><h2>Browse Dictionary</h2></body></html>"""

MISSING_PAGE = f"""<html><head><title>APA Dictionary of Psychology</title></head>
<body>{HEADER}<p>Sorry, “zzz” is not in the Dictionary of Psychology.</p>
</body></html>"""

UNKNOWN_PAGE = """<html><head><title>Maintenance</title></head>
<body><p>Back soon.</p></body></html>"""


class FakeResponse:
    def __init__(self, status_code, body="", url=None):
        self.status_code = status_code
        self.content = body.encode()
        self.url = url


class FakeSession:
    def __init__(self, response=None, error=None):
        self.response, self.error = response, error
        self.requests = []

    def get(self, url, headers=None, timeout=None):
        self.requests.append((url, headers, timeout))
        if self.error is not None:
            raise self.error
        return self.response


def lookup(term, **session):
    fake = FakeSession(**session)
    return ApaDictionary(session=fake).lookup(term), fake


def test_found_entry_from_the_enriched_markup():
    entry, session = lookup(
        "Loneliness",
        response=FakeResponse(200, ENTRY_PAGE, "https://dictionary.apa.org/loneliness"),
    )

    assert entry == {
        "term": "Loneliness",
        "source": "APA Dictionary of Psychology",
        "url": "https://dictionary.apa.org/loneliness",
        "status": "found",
        "http_status": 200,
        "entry_heading": "loneliness",
        "page_title": "loneliness – APA Dictionary of Psychology",
    }
    url, headers, timeout = session.requests[0]
    assert url == "https://dictionary.apa.org/loneliness"
    assert headers["User-Agent"].startswith("Mozilla/5.0") and timeout == 30.0


def test_scripts_do_not_count_as_page_text():
    # ENTRY_PAGE's script says "term was not found"; a browser would not show it.
    entry, _ = lookup("loneliness", response=FakeResponse(200, ENTRY_PAGE))
    assert entry["status"] == "found"


def test_missing_entry_message_is_not_found():
    entry, _ = lookup("zzz", response=FakeResponse(200, MISSING_PAGE))
    assert entry["status"] == "not_found"


def test_http_404_is_not_found():
    entry, _ = lookup("zzz", response=FakeResponse(404))
    assert entry["status"] == "not_found" and entry["http_status"] == 404


def test_generic_page_is_unavailable():
    entry, _ = lookup("xyzzy", response=FakeResponse(200, GENERIC_PAGE))

    assert entry["status"] == "unavailable"
    assert "could not be verified automatically" in entry["error"]


def test_unrecognised_page_is_unavailable():
    entry, _ = lookup("loneliness", response=FakeResponse(200, UNKNOWN_PAGE))

    assert entry["status"] == "unavailable"
    assert "could not be determined reliably" in entry["error"]


def test_http_error_is_a_request_error():
    entry, _ = lookup("loneliness", response=FakeResponse(503))

    assert entry["status"] == "request_error"
    assert entry["http_status"] == 503
    assert entry["error"] == "The APA Dictionary returned HTTP status 503."


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (requests.Timeout("slow"), "The APA Dictionary request timed out."),
        (
            requests.ConnectionError("refused"),
            "The APA Dictionary could not be reached (ConnectionError).",
        ),
    ],
)
def test_network_failures_are_request_errors(error, message):
    entry, _ = lookup("loneliness", error=error)
    assert entry["status"] == "request_error" and entry["error"] == message


def test_upstream_slugs():
    assert slugify_term("loneliness") == "loneliness"
    assert slugify_term("Social Isolation") == "social-isolation"
    assert slugify_term("self-transcendence") == "self-transcendence"
