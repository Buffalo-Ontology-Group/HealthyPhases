"""The term-clustering scripts must select what the module declares.

``scripts/term_clustering/declared.py`` reads the prompt declarations and the
default model from source, because the scripts run outside the project
environment. This checks it against the module's own answer, so a change to
how prompts are declared cannot leave the scripts selecting a stale version.
"""

import importlib.util
from pathlib import Path

from phases_v2.coverage.interfaces import DEFAULT_MODEL
from phases_v2.prompts.templates import current_prompts, declared_prompts

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "term_clustering" / "declared.py"


def _declared():
    spec = importlib.util.spec_from_file_location("term_clustering_declared", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scripts_select_the_current_conditional_version() -> None:
    declared = _declared()
    (current,) = [p for p in current_prompts() if p.name == declared.CONDITIONAL_PROMPT]
    assert declared.current_version() == current.prompt_id


def test_scripts_know_every_declared_conditional_version_in_order() -> None:
    declared = _declared()
    expected = [p.prompt_id for p in declared_prompts() if p.name == declared.CONDITIONAL_PROMPT]
    assert declared.declared_versions() == expected


def test_scripts_default_to_the_coverage_default_model() -> None:
    assert _declared().default_model() == DEFAULT_MODEL
