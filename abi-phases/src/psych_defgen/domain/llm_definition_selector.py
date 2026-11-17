"""Selection prompt: find an original definition in the evidence.

The prompt is upstream's. The model call is ``generate(messages,
json_mode=...)``, which the application binds to its LanguageModel port and
which returns ``{"text": str, "usage": dict | None}``.
"""

import json


def select_definition_with_llm(
    term,
    retrieved_items,
    generate,
):
    """
    Identify an original definition in retrieved evidence.
    Do not generate or rewrite definitions.
    """

    passages = []

    for item in retrieved_items[:10]:
        text = item.get("text") or item.get("context_text") or ""

        if text.strip():
            passages.append(text.strip())

    if not passages:
        return None, None

    numbered_passages = "\n\n".join(
        f"Evidence {i + 1}:\n{text}"
        for i, text in enumerate(passages)
    )

    prompt = f"""
You are identifying definitions of psychological constructs from scientific literature.

Target construct: {term}

TASK:
Identify whether any evidence passage contains a definition
of the target construct.

RULES:
1. Use only the supplied evidence.
2. Do not generate, summarize, or rewrite a definition.
3. Return the original wording exactly as it appears.
4. A definition explains what the target construct is.
5. Do not confuse definitions with causes, associations,
   measurements, consequences, or study findings.
6. The construct may appear anywhere in a sentence.
7. If introductory text appears before the construct,
   return the text beginning with the construct.
8. Preserve author citations and explanatory phrases.
9. Prefer a direct definition over a contextual description.
10. If no suitable definition exists, return null.

Return valid JSON only:

{{
    "definition": "exact original text or null"
}}

EVIDENCE:
{numbered_passages}
"""

  
    response = generate(
        [{"role": "user", "content": prompt}],
        json_mode=True,
    )

    usage = response["usage"]

    try:
        result = json.loads(response["text"])
        # Rule 10 invites a bare ``null``: any JSON that is not an object
        # means no definition was selected.
        if not isinstance(result, dict):
            return None, usage
        definition = result.get("definition")

        if not isinstance(definition, str) or not definition.strip():
            return None, usage

        definition = definition.strip()

        # Verify that the selected definition exists in the evidence.
        for passage in passages:
            if definition in passage:
                return definition, usage

    except (json.JSONDecodeError, KeyError, TypeError):
        pass

    return None, usage
