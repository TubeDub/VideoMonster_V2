"""Architecture invariants — Dub Engine 2.0 isolation from Translation."""

from __future__ import annotations

import ast
from pathlib import Path

from engines.pipeline_integrity.exceptions import ArchitectureViolation

PACKAGE = Path(__file__).resolve().parent

# May use decision_policy for escalation; must not import translation_core / TE rewrite
FORBIDDEN = (
    "engines.translation_pipeline",
    "engines.translation_adapt",
    "engines.ai_adaptation_engine",
)


def assert_dub_engine_isolated() -> None:
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
            f"Dub Engine isolation violated: {offenders}",
            stage="dub_engine_v2",
            rule="layer_isolation",
            details={"offenders": offenders},
        )
