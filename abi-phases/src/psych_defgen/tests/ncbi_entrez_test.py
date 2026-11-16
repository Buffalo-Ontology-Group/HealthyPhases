"""NCBI adapter with a fake HTTP session; parsing uses Biopython's bundled DTDs."""

import pytest
import requests

from psych_defgen.adapters.secondary import ncbi_entrez
from psych_defgen.adapters.secondary.ncbi_entrez import NcbiEntrez, NcbiError

EMAIL = "researcher@example.org"
KEY = "ncbi-key-0123456789"

ESEARCH = b"""<?xml version="1.0" encoding="UTF-8" ?>
<!DOCTYPE eSearchResult PUBLIC "-//NLM//DTD esearch 20060628//EN" "https://eutils.ncbi.nlm.nih.gov/eutils/dtd/20060628/esearch.dtd">
<eSearchResult><Count>2</Count><RetMax>2</RetMax><RetStart>0</RetStart>
<IdList><Id>111</Id><Id>222</Id></IdList><TranslationSet/>
<QueryTranslation>"loneliness"[Title/Abstract]</QueryTranslation></eSearchResult>"""

ELINK = b"""<?xml version="1.0" encoding="UTF-8" ?>
<!DOCTYPE eLinkResult PUBLIC "-//NLM//DTD elink 20101123//EN" "https://eutils.ncbi.nlm.nih.gov/eutils/dtd/20101123/elink.dtd">
<eLinkResult><LinkSet><DbFrom>pubmed</DbFrom><IdList><Id>111</Id></IdList>
<LinkSetDb><DbTo>pmc</DbTo><LinkName>pubmed_pmc</LinkName>
<Link><Id>9876</Id></Link></LinkSetDb></LinkSet></eLinkResult>"""

ELINK_EMPTY = b"""<?xml version="1.0" encoding="UTF-8" ?>
<!DOCTYPE eLinkResult PUBLIC "-//NLM//DTD elink 20101123//EN" "https://eutils.ncbi.nlm.nih.gov/eutils/dtd/20101123/elink.dtd">
<eLinkResult><LinkSet><DbFrom>pubmed</DbFrom><IdList><Id>333</Id></IdList>
</LinkSet></eLinkResult>"""

PUBMED = b"""<?xml version="1.0" encoding="UTF-8" ?>
<!DOCTYPE PubmedArticleSet PUBLIC "-//NLM//DTD PubMedArticle, 1st January 2025//EN" "https://dtd.nlm.nih.gov/ncbi/pubmed/out/pubmed_250101.dtd">
<PubmedArticleSet><PubmedArticle>
<MedlineCitation Status="MEDLINE" Owner="NLM"><PMID Version="1">222</PMID>
<Article PubModel="Print"><Journal><JournalIssue CitedMedium="Print">
<PubDate><MedlineDate>2019 Jan-Feb</MedlineDate></PubDate></JournalIssue>
<Title>Journal of Loneliness</Title></Journal>
<ArticleTitle>On loneliness.</ArticleTitle>
<Abstract><AbstractText Label="BACKGROUND">Loneliness is common.</AbstractText>
<AbstractText Label="RESULTS">It varied.</AbstractText></Abstract>
<AuthorList><Author ValidYN="Y"><LastName>Doe</LastName><ForeName>Jane</ForeName>
<Initials>J</Initials></Author>
<Author ValidYN="Y"><CollectiveName>Study Group</CollectiveName></Author></AuthorList>
</Article></MedlineCitation></PubmedArticle></PubmedArticleSet>"""

PMC = b"""<?xml version="1.0" ?>
<pmc-articleset><article><front><article-meta><title-group>
<article-title>A study of loneliness</article-title></title-group>
<abstract><p>Loneliness is a distressing feeling of unmet social needs.</p></abstract>
</article-meta></front><body><sec>
<p>Loneliness has been defined in many ways across the literature.</p>
<table-wrap><p>Table text that is excluded from the article.</p></table-wrap>
<p>Too short.</p></sec></body>
<back><ref-list><p>Reference text that is excluded too.</p></ref-list></back>
</article></pmc-articleset>"""


class FakeResponse:
    def __init__(self, status_code, content=b""):
        self.status_code = status_code
        self.content = content


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.posts = []

    def post(self, url, data=None, timeout=None):
        self.posts.append({"url": url, "data": dict(data), "timeout": timeout})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Fail instead of downloading a DTD Biopython does not bundle."""

    def refuse(*args, **kwargs):
        raise AssertionError("Bio.Entrez tried to reach the network")

    monkeypatch.setattr("Bio.Entrez.Parser.urlopen", refuse, raising=False)
    monkeypatch.setattr(ncbi_entrez, "_last_request", 0.0)


def source(*responses, api_key=KEY):
    session = FakeSession(*responses)
    sleeps = []
    return (
        NcbiEntrez(email=EMAIL, api_key=api_key, session=session, sleep=sleeps.append),
        session,
        sleeps,
    )


def test_search_posts_credentials_in_the_body_not_the_url():
    ncbi, session, _ = source(FakeResponse(200, ESEARCH))

    assert ncbi.search("loneliness", max_results=2) == ["111", "222"]

    post = session.posts[0]
    assert post["url"] == f"{ncbi_entrez.EUTILS}/esearch.fcgi"
    assert post["data"] == {
        "db": "pubmed",
        "term": '"loneliness"[Title/Abstract]',
        "retmax": 2,
        "sort": "relevance",
        "tool": "healthyphases_psych_defgen",
        "email": EMAIL,
        "api_key": KEY,
    }
    assert post["timeout"] == 60.0


def test_pacing_follows_ncbi_limits():
    assert source(api_key=KEY)[0].interval == 0.1
    assert source(api_key=None)[0].interval == 0.37
    assert "api_key" not in _posted(source(FakeResponse(200, ESEARCH), api_key=None))


def _posted(built):
    ncbi, session, _ = built
    ncbi.search("loneliness", max_results=1)
    return session.posts[0]["data"]


def test_retries_429_and_5xx_with_backoff():
    ncbi, session, sleeps = source(
        FakeResponse(429), FakeResponse(503), FakeResponse(200, ESEARCH)
    )

    assert ncbi.search("loneliness", max_results=2) == ["111", "222"]
    assert len(session.posts) == 3
    assert [s for s in sleeps if s >= 1] == [1, 2]


def test_gives_up_with_a_secret_free_error():
    ncbi, session, _ = source(*[FakeResponse(500)] * 4)

    with pytest.raises(NcbiError) as raised:
        ncbi.search("loneliness", max_results=2)

    assert str(raised.value) == (
        "NCBI PubMed search failed (HTTP 500) after 4 attempts; retry later."
    )
    assert len(session.posts) == 4


def test_client_errors_are_not_retried():
    ncbi, session, _ = source(FakeResponse(400))

    with pytest.raises(NcbiError, match="check NCBI_EMAIL and NCBI_API_KEY"):
        ncbi.search("loneliness", max_results=2)
    assert len(session.posts) == 1


def test_connection_errors_name_no_url_or_key():
    error = requests.ConnectionError(f"https://example.invalid/?api_key={KEY}")
    ncbi, _, _ = source(*[error] * 4)

    with pytest.raises(NcbiError) as raised:
        ncbi.search("loneliness", max_results=2)

    assert "ConnectionError" in str(raised.value)
    assert KEY not in str(raised.value) and EMAIL not in str(raised.value)
    assert raised.value.__cause__ is None


def test_pmc_ids_skip_pmids_without_a_link_or_whose_lookup_fails():
    ncbi, _, _ = source(
        FakeResponse(200, ELINK),
        FakeResponse(200, ELINK_EMPTY),
        FakeResponse(404),
    )

    assert ncbi.pmc_ids(["111", "333", "444"]) == {"111": "PMC9876"}


def test_abstracts_keep_upstreams_record_shape():
    ncbi, session, _ = source(FakeResponse(200, PUBMED))

    records = ncbi.abstracts(["222"])

    assert session.posts[0]["data"]["rettype"] == "medline"
    assert records == [
        {
            "pmid": "222",
            "title": "On loneliness.",
            "authors": ["Doe J", "Study Group"],
            "journal": "Journal of Loneliness",
            "year": "2019",
            "abstract": "BACKGROUND: Loneliness is common.\nRESULTS: It varied.",
            "source": "PubMed abstract",
            "pubmed_url": "https://pubmed.ncbi.nlm.nih.gov/222/",
        }
    ]


def test_authors_by_pmid():
    ncbi, _, _ = source(FakeResponse(200, PUBMED))

    assert ncbi.authors(["222"]) == {"222": ["Jane Doe", "Study Group"]}


def test_full_text_is_parsed_without_tables_or_references():
    ncbi, session, _ = source(FakeResponse(200, PMC))

    title, text = ncbi.full_text("PMC9876")

    assert session.posts[0]["data"]["id"] == "9876"
    assert title == "A study of loneliness"
    assert text == (
        "Loneliness is a distressing feeling of unmet social needs.\n"
        "Loneliness has been defined in many ways across the literature."
    )


def test_an_email_is_required():
    with pytest.raises(ValueError, match="NCBI_EMAIL"):
        NcbiEntrez(email="")
