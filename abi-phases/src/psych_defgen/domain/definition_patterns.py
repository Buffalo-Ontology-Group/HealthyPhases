"""Shared linguistic patterns for identifying definitions."""

# Highest-priority definition expressions.
STRONG_DEFINITION_PATTERNS = [
    "is defined as",
    "was defined as",
    "are defined as",
    "were defined as",
    "has been defined as",
    "have been defined as",
    "can be defined as",
    "is defined by",
    "are defined by",
    "refers to",
    "refer to",
    "denotes",
    "denote",
]

# Other explicit or conceptual definition expressions.
CONCEPTUAL_DEFINITION_PATTERNS = [
    "is conceptualized as",
    "are conceptualized as",
    "is understood as",
    "are understood as",
    "is described as",
    "are described as",
    "is characterized by",
    "are characterized by",
    "is a subjective",
    "is the subjective",
    "is a ",
    "is an ",
    "is the ",
]

# Weaker expressions that may describe a construct.
FALLBACK_DEFINITION_PATTERNS = [
    "involves",
    "occurs when",
    "stems from",
    "typically defined by",
    "defined by",
    "resulting from",
    "is considered",
    "are considered",
    "determines",
]

# General expressions for candidate extraction and retrieval.
DEFINITION_KEYWORDS = list(dict.fromkeys(
    STRONG_DEFINITION_PATTERNS
    + CONCEPTUAL_DEFINITION_PATTERNS
    + FALLBACK_DEFINITION_PATTERNS
    + [
        "defined as",
        "defined by",
        "characterized by",
        "conceptualized as",
        "understood as",
        "described as",
    ]
))