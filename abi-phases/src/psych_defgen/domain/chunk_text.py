import re

from psych_defgen.domain.models import TextChunk


def chunk_article(article, chunk_size=180, overlap=40):
    """
    Split article text into overlapping chunks while preserving
    sentence boundaries and article metadata.
    """
    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("Require chunk_size > overlap >= 0.")

    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", article.text.strip())
        if sentence.strip()
    ]

    chunks = []
    start = 0

    while start < len(sentences):
        end = start
        word_count = 0

        while end < len(sentences):
            sentence_words = len(sentences[end].split())

            if word_count + sentence_words > chunk_size and end > start:
                break

            word_count += sentence_words
            end += 1

        chunks.append(
            TextChunk(
                pmid=article.pmid,
                title=article.title,
                pmcid=article.pmcid,
                source=article.source,
                text=" ".join(sentences[start:end]),
            )
        )

        if end == len(sentences):
            break

        next_start = end
        overlap_words = 0

        while next_start > start + 1:
            sentence_words = len(sentences[next_start - 1].split())

            if overlap_words + sentence_words > overlap:
                break

            next_start -= 1
            overlap_words += sentence_words

        start = next_start

    return chunks