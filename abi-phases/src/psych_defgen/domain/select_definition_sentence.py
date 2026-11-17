import re

from psych_defgen.domain.definition_patterns import (
    CONCEPTUAL_DEFINITION_PATTERNS,
    FALLBACK_DEFINITION_PATTERNS,
    STRONG_DEFINITION_PATTERNS,
)

EXPLICIT_DEFINITION_PATTERNS = (
    STRONG_DEFINITION_PATTERNS
    + CONCEPTUAL_DEFINITION_PATTERNS
)

BAD_STUDY_PATTERNS = [
    "systematic review",
    "meta-analysis",
    "association between",
    "found that",
    "study investigating",
    "study examined",
    "study investigates",
    "risk of developing",
    "predictor of",
    "tested a",
    "tested as",
]


def split_sentences(text):
    return re.split(
        r"(?<=[.!?])\s+",
        text,
    )


def clean_sentence(sentence):
    sentence = re.sub(
        r"^\d+\s+",
        "",
        sentence,
    )

    sentence = re.sub(
        r"\s+",
        " ",
        sentence,
    )

    return sentence.strip()

def get_term_variants(term):
    """
    Return simple singular/plural variants of the
    requested term for definition matching.
    """

    term_lower = term.lower().strip()

    variants = {
        term_lower,
    }

    if term_lower.endswith("s"):
        variants.add(
            term_lower[:-1]
        )
    else:
        variants.add(
            f"{term_lower}s"
        )

    return variants

def extract_embedded_definition(sentence, term):
    """Extract a definition starting from the construct, preserving original wording."""
    sentence = clean_sentence(sentence)

    for variant in sorted(get_term_variants(term), key=len, reverse=True):
        pattern = (
            rf"\b{re.escape(variant)}\b"
            rf"\s*,\s*"
            rf"defined\b"
            rf"[\s\S]*?"
            rf"\bas\s+"
            rf".+"
        )

        match = re.search(pattern, sentence, flags=re.IGNORECASE)

        if match:
            return sentence[match.start():].strip()

    return None

def normalize_for_matching(
    sentence,
    term,
):
    """
    Normalize simple extraction artifacts for
    matching.

    Example:

    "ECONOMIC STABILITY Economic stability is..."
    becomes temporarily:

    "Economic stability is..."

    This normalization is used for matching and
    selection only.
    """

    normalized = clean_sentence(
        sentence
    )

    term_lower = term.lower().strip()
    lower = normalized.lower()

    repeated_term = (
        f"{term_lower} {term_lower}"
    )

    if lower.startswith(
        repeated_term
    ):
        normalized = normalized[
            len(term):
        ].strip()

    return normalized


def get_term_start(
    sentence,
    term,
):
    """
    Return the matched beginning of a definition
    sentence.

    Supports forms such as:

    "social environment ..."
    "the social environment ..."
    "a social environment ..."
    """

    lower = sentence.lower().strip()

    term_variants = get_term_variants(
        term
    )

    valid_starts = []

    for variant in term_variants:
        valid_starts.extend(
            [
                variant,
                f"the {variant}",
                f"a {variant}",
            ]
        )

    for start in sorted(
        valid_starts,
        key=len,
        reverse=True,
    ):
        if lower.startswith(start):
            return start

    return None


def is_bad_study_sentence(
    sentence,
):
    """
    Reject sentences that clearly describe
    study design, associations, predictors,
    or review methodology rather than defining
    the requested construct.
    """

    lower = sentence.lower()

    return any(
        pattern in lower
        for pattern in BAD_STUDY_PATTERNS
    )

def has_embedded_definition_context(sentence, term):
    """
    Recognize contextual expressions within an already
    valid definition sentence.

    Embedded expressions alone do not establish a definition.
    """
    sentence = normalize_for_matching(sentence, term)
    lower = sentence.lower()

    matched_start = get_term_start(sentence, term)

    if matched_start is None:
        return False

    remainder = lower[len(matched_start):].strip()

    # Require an independently recognizable definition opening.
    valid_opening = any(
        remainder.startswith(pattern)
        for pattern in EXPLICIT_DEFINITION_PATTERNS
    )

    if not valid_opening:
        return False

    embedded_patterns = (
        "resulting from",
        "determines",
    )

    return any(
        pattern in remainder
        for pattern in embedded_patterns
    )


def is_explicit_definition_sentence(
    sentence,
    term,
):
    """
    Return True when the sentence contains an
    explicit definitional construction.
    """

    sentence = normalize_for_matching(
        sentence,
        term,
    )

    lower = sentence.lower()
    matched_start = get_term_start(
        sentence,
        term,
    )

    if matched_start is None:
        return False

    word_count = len(
        sentence.split()
    )

    if (
        word_count < 6
        or word_count > 60
    ):
        return False

    if is_bad_study_sentence(
        sentence
    ):
        return False

    remainder = lower[
        len(matched_start):
    ].strip()

    return any(
        remainder.startswith(pattern)
        for pattern in EXPLICIT_DEFINITION_PATTERNS
    )


def is_fallback_definition_sentence(
    sentence,
    term,
):
    """
    Return True for weaker conceptual statements
    that may be used only when no explicit
    definition is available.
    """

    sentence = normalize_for_matching(
        sentence,
        term,
    )

    lower = sentence.lower()
    matched_start = get_term_start(
        sentence,
        term,
    )

    if matched_start is None:
        return False

    word_count = len(
        sentence.split()
    )

    if (
        word_count < 6
        or word_count > 60
    ):
        return False

    if is_bad_study_sentence(
        sentence
    ):
        return False

    remainder = lower[
        len(matched_start):
    ].strip()

    return any(
        remainder.startswith(pattern)
        for pattern in FALLBACK_DEFINITION_PATTERNS
        if pattern not in ("resulting from", "determines")
    )


def is_definition_sentence(
    sentence,
    term,
):
    """
    Return True when the sentence is either:

    1. an explicit definition, or
    2. a fallback conceptual definition.
    """

    return (
        is_explicit_definition_sentence(
            sentence,
            term,
        )
        or is_fallback_definition_sentence(
            sentence,
            term,
        )
    )


def definition_score(sentence, term):
    """
    Score definitions using shared linguistic patterns.

    Strong: 100
    Conceptual: 80
    Noun-based: 70
    Fallback: 50
    """

    sentence = normalize_for_matching(sentence, term)
    lower = sentence.lower().strip()

    matched_start = get_term_start(sentence, term)

    if matched_start is None:
        return -1

    word_count = len(sentence.split())

    if word_count < 6 or word_count > 60:
        return -1

    if is_bad_study_sentence(sentence):
        return -1

    remainder = lower[len(matched_start):].strip()

    if any(
        remainder.startswith(pattern)
        for pattern in STRONG_DEFINITION_PATTERNS
    ):
        return 100

    noun_patterns = ("is a ", "is an ", "is the ")

    if any(
        remainder.startswith(pattern)
        for pattern in CONCEPTUAL_DEFINITION_PATTERNS
        if pattern not in noun_patterns
    ):
        return 80

    if any(
        remainder.startswith(pattern)
        for pattern in noun_patterns
    ):
        return 70

    if any(
        remainder.startswith(pattern)
        for pattern in FALLBACK_DEFINITION_PATTERNS
    ):
        # Causal or relational statements alone are not definitions.
        if remainder.startswith(("resulting from", "determines")):
            return -1

        return 50

    return -1


def select_best_definition_sentence(
    term,
    retrieved_items,
    select_definition_with_llm=None,
):
    """
    Select the strongest definition sentence.

    Selection hierarchy:

    1. Prefer explicit definitions.
    2. Use fallback conceptual statements only
       when no explicit definition is available.
    3. Use retrieval similarity only as a
       tie-breaker within each category.

    ``select_definition_with_llm(term, retrieved_items)`` returns
    ``(definition or None, usage or None)``; it is called only when
    no pattern matches.
    """

    explicit_candidates = []
    fallback_candidates = []

    for item in retrieved_items:

        text = item.get(
            "text",
            "",
        )

        retrieval_score = item.get(
            "score",
            0.0,
        )


        for sentence in split_sentences(text):

            original_sentence = clean_sentence(sentence)

            embedded_definition = extract_embedded_definition(
                original_sentence,
                term,
            )

            output_sentence = (
                embedded_definition
                if embedded_definition
                else normalize_for_matching(
                    original_sentence,
                    term,
                )
            )

            if embedded_definition:
                explicit_candidates.append(
                    (100, retrieval_score, output_sentence)
                )
                continue
            
            score = definition_score(
                output_sentence,
                term,
            )

            if score < 0:
                continue

            candidate = (
                score,
                retrieval_score,
                output_sentence,
            )

            if is_explicit_definition_sentence(
                output_sentence,
                term,
            ):
                explicit_candidates.append(
                    candidate
                )

            elif is_fallback_definition_sentence(
                output_sentence,
                term,
            ):
                fallback_candidates.append(
                    candidate
                )

    if explicit_candidates:
        candidates = explicit_candidates

    elif fallback_candidates:
        candidates = fallback_candidates

    else:
        # The LLM call sits behind the application's LanguageModel port.
        llm_definition, selection_usage = (
            select_definition_with_llm(term, retrieved_items)
            if select_definition_with_llm is not None
            else (None, None)
        )

        if llm_definition:
            return llm_definition, "llm_selection", selection_usage

        return None, None, selection_usage

    candidates.sort(
        key=lambda item: (
            item[0],
            item[1],
        ),
        reverse=True,
    )

    return candidates[0][2], "pattern", None