"""Parse PMC article XML into a title and readable text.

The fetch half of upstream's ``parse_fulltext.py`` moved behind the
``Literature`` port (``adapters/secondary/ncbi_entrez.py``).
"""

from bs4 import BeautifulSoup


def extract_paragraphs(section):
    """
    Extract readable paragraphs from an XML section.

    Exclude reference lists, tables, figures,
    and supplementary material.
    """

    if section is None:
        return []

    paragraphs = []

    excluded_tags = {
        "ref-list",
        "table-wrap",
        "fig",
        "supplementary-material",
        "supplementary-material-wrap",
    }

    for paragraph in section.find_all("p"):

        # Skip paragraphs inside excluded elements.
        if any(
            parent.name in excluded_tags
            for parent in paragraph.parents
        ):
            continue

        text = paragraph.get_text(
            " ",
            strip=True,
        )

        # Normalize whitespace.
        text = " ".join(text.split())

        if not text:
            continue

        # Remove very short fragments.
        if len(text.split()) < 5:
            continue

        paragraphs.append(text)

    return paragraphs


def parse_pmc_xml(xml_data):
    """
    Parse PMC XML and return the article title
    and readable article text.

    Extract only abstract and body paragraphs.
    """

    soup = BeautifulSoup(
        xml_data,
        "xml",
    )

    # --------------------------------------------------
    # Article title
    # --------------------------------------------------

    article = soup.find("article")

    title_tag = (
        article.find("article-title")
        if article
        else soup.find("article-title")
    )

    title = (
        title_tag.get_text(" ", strip=True)
        if title_tag
        else "No title found"
    )

    text_parts = []

    # --------------------------------------------------
    # Abstract
    # --------------------------------------------------

    abstract = soup.find("abstract")

    abstract_paragraphs = extract_paragraphs(
        abstract
    )

    if abstract_paragraphs:
        text_parts.extend(
            abstract_paragraphs
        )

    # --------------------------------------------------
    # Article body
    # --------------------------------------------------

    body = soup.find("body")

    body_paragraphs = extract_paragraphs(
        body
    )

    if body_paragraphs:
        text_parts.extend(
            body_paragraphs
        )

    # --------------------------------------------------
    # Remove duplicate paragraphs
    # --------------------------------------------------

    unique_parts = []
    seen = set()

    for text in text_parts:

        normalized = text.lower().strip()

        if normalized in seen:
            continue

        seen.add(normalized)
        unique_parts.append(text)

    # --------------------------------------------------
    # Return readable article text
    # --------------------------------------------------

    article_text = "\n".join(
        unique_parts
    ).strip()

    return title, article_text
