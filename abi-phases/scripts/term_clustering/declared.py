"""The prompt versions and the model that the selection uses by default.

They are read from the module's own code, never written down here, so the
default follows the code when the conditional prompt gets a new version:

- the versions come from ``_DECLARED`` in ``phases_v2/prompts/templates.py``
  (a name's first declaration is its current text, the later ones are earlier
  versions), the template files, and the id rule in ``phases_v2/identity.py``;
- the model is ``DEFAULT_MODEL`` in ``phases_v2/coverage/interfaces.py``.

The scripts run outside the project environment, where ``phases_v2`` cannot be
imported (its package imports the platform), so this module reads those files
as source and loads ``identity.py`` on its own (it needs only the standard
library). ``tests/term_clustering_declared_test.py`` checks the result against
``current_prompts()`` and ``DEFAULT_MODEL`` in the project environment.
"""

from __future__ import annotations

import ast
import importlib.util
from functools import lru_cache
from pathlib import Path
from types import ModuleType

SRC = Path(__file__).resolve().parents[2] / "src" / "phases_v2"
TEMPLATES = SRC / "prompts" / "templates.py"
TEMPLATE_DIR = SRC / "prompts" / "templates"
IDENTITY = SRC / "identity.py"
COVERAGE = SRC / "coverage" / "interfaces.py"

CONDITIONAL_PROMPT = "conditional_statement"


def module_constant(path: Path, name: str):
    """The literal value assigned to ``name`` at the top level of ``path``."""
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
        else:
            continue
        if name in targets and node.value is not None:
            return ast.literal_eval(node.value)
    raise LookupError(f"{name} is not assigned a literal at the top level of {path}")


@lru_cache(maxsize=1)
def _identity() -> ModuleType:
    spec = importlib.util.spec_from_file_location("term_clustering_identity", IDENTITY)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {IDENTITY}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def declared_versions(name: str = CONDITIONAL_PROMPT) -> list[str]:
    """Prompt ids of every declared version of ``name``, the current one first."""
    ids = [
        _identity().prompt_id(declared, (TEMPLATE_DIR / f"{stem}.txt").read_text())
        for declared, stem, _key in module_constant(TEMPLATES, "_DECLARED")
        if declared == name
    ]
    if not ids:
        raise LookupError(f"no prompt named {name!r} is declared in {TEMPLATES}")
    return ids


def current_version(name: str = CONDITIONAL_PROMPT) -> str:
    return declared_versions(name)[0]


def default_model() -> str:
    return module_constant(COVERAGE, "DEFAULT_MODEL")


if __name__ == "__main__":
    print(f"current {CONDITIONAL_PROMPT}: {current_version()}")
    print(f"declared versions: {', '.join(declared_versions())}")
    print(f"default model: {default_model()}")
