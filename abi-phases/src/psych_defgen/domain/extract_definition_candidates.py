import re

from psych_defgen.domain.definition_patterns import DEFINITION_KEYWORDS


def clean_sentence(sentence):
    """Remove leading numbering and normalize whitespace."""
    sentence = re.sub(r'^\d+\s*,\s*\d+\s*', '', sentence)
    sentence = re.sub(r'\s+', ' ', sentence)
    return sentence.strip()


def split_sentences(text):
    """Split text into sentences using punctuation boundaries."""
    return re.split(r'(?<=[.!?])\s+', text)


def extract_definition_candidates(text, term):
    """Extract sentences containing the construct or related definition keywords."""
    candidates = []
    term_lower = term.lower()
    term_words = term_lower.split()

    for sentence in split_sentences(text):
        sentence_clean = clean_sentence(sentence)
        sentence_lower = sentence_clean.lower()

        if (
            term_lower in sentence_lower
            or (
                any(keyword in sentence_lower for keyword in DEFINITION_KEYWORDS)
                and any(word in sentence_lower for word in term_words)
            )
        ):
            candidates.append(sentence_clean)

    return candidates