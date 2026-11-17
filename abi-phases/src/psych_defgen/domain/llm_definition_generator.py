"""Generate evidence-grounded psychological construct definitions using LLMs.

The synthesis prompt is upstream's. The model call is ``generate(messages,
json_mode=...)``, which the application binds to its LanguageModel port and
which returns ``{"text": str, "usage": dict | None}``.
"""


def generate_definition_with_llm(
    term: str,
    evidence: list[dict],
    generate,
    top_k: int = 10,
) -> dict:

    """
    Generate a definition using only retrieved evidence passages.

    Args:
        term: Psychological construct to define.
        evidence: Ranked evidence records from semantic retrieval.
        generate: Chat call bound to the configured model.
        top_k: Maximum number of evidence passages to use.

    Returns:
        A dictionary containing the generated definition and LLM token usage.
    """

    passages = []

    for index, item in enumerate(evidence[:top_k], start=1):

        passage = (
            item.get("context_text")
            or item.get("text")
            or ""
        ).strip()

        if passage:
            passages.append(
                f"Evidence {index}:\n{passage}"
            )

    if not passages:
        return {
            "text": "Insufficient evidence to generate a definition.",
            "usage": None,
        }

    

    evidence_text = "\n\n".join(passages)

    system_prompt = """
You generate evidence-based definitions of psychological constructs.

Use ONLY the provided evidence passages.

Rules:
1. Identify information directly relevant to defining the construct.
2. Combine useful information from the evidence passages.
3. Remove repetition and redundant information.
4. Rephrase or reorganize wording only when necessary.
5. Begin with an appropriate class or category supported by the evidence.
6. Do not add information from external knowledge.
7. Do not invent characteristics, causes, functions, or outcomes.
8. Do not introduce interpretations absent from the evidence.
9. Do not force information from irrelevant passages into the definition.
10. If the evidence is insufficient, return exactly:
    Insufficient evidence to generate a definition.
11. Return only one concise, coherent definition.
"""

    user_prompt = f"""
Psychological construct: {term}

Retrieved evidence passages:

{evidence_text}

Generate a consolidated definition using only the evidence above.
"""
    return generate(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
