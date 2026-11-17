"""The LLM selector's handling of replies (see UPSTREAM.md for the change)."""

import pytest

from psych_defgen.domain.llm_definition_selector import select_definition_with_llm

PASSAGE = "Loneliness is defined as a distressing feeling of unmet social needs."
USAGE = {"input_tokens": 10, "output_tokens": 5}


def reply(text):
    def generate(messages, *, json_mode):
        assert json_mode is True
        return {"text": text, "usage": USAGE}

    return generate


def select(text):
    return select_definition_with_llm("loneliness", [{"text": PASSAGE}], reply(text))


@pytest.mark.parametrize("text", ["null", "[]", '["a"]', '"a definition"', "3", "true"])
def test_a_reply_that_is_not_an_object_selects_nothing(text):
    assert select(text) == (None, USAGE)


@pytest.mark.parametrize(
    "text", ['{"definition": null}', '{"definition": "  "}', "not json", "{}"]
)
def test_a_reply_without_a_definition_selects_nothing(text):
    assert select(text) == (None, USAGE)


def test_a_definition_from_the_evidence_is_selected():
    assert select(f'{{"definition": "{PASSAGE}"}}') == (PASSAGE, USAGE)


def test_a_definition_not_in_the_evidence_is_rejected():
    assert select('{"definition": "Loneliness is being alone."}') == (None, USAGE)
