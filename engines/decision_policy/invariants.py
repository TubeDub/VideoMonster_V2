"""Architecture invariants for Decision Policy Engine."""

from __future__ import annotations

import ast
from pathlib import Path

from engines.pipeline_integrity.exceptions import ArchitectureViolation

PACKAGE = Path(__file__).resolve().parent

# May read semantic types / integrity; must not own TTS/merge/scheduler writes
FORBIDDEN = (
    "engines.tts",
    "engines.merge",
)


def assert_decision_policy_isolated() -> None:
    found: set[str] = set()
    for path in PACKAGE.rglob("*.py"):
        if path.name == "invariants.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    found.add(a.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module)
    offenders = [
        m
        for m in found
        for bad in FORBIDDEN
        if m == bad or m.startswith(bad + ".")
    ]
    if offenders:
        raise ArchitectureViolation(
            f"Decision Policy isolation violated: {offenders}",
            stage="decision_policy",
            rule="layer_isolation",
        )


def assert_no_text_mutation(before: str, after: str, sentence_uuid: str = "") -> None:
    if (before or "") != (after or ""):
        raise ArchitectureViolation(
            "Decision Policy must not change text",
            stage="decision_policy",
            rule="no_text_mutation",
            segment_id=sentence_uuid,
        )
