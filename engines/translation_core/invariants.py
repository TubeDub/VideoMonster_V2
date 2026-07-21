"""Architecture invariants — Translation Core isolation."""

from __future__ import annotations

import ast
from pathlib import Path

FORBIDDEN_IMPORT_PREFIXES: tuple[str, ...] = (
    "engines.scheduler",
    "engines.dubbing_engine",
    "engines.tts",
    "engines.merge",
    "api.studio_api",
    "engines.streamdub",
)

PACKAGE_ROOT = Path(__file__).resolve().parent


def collect_imports() -> set[str]:
    found: set[str] = set()
    for path in PACKAGE_ROOT.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.add(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module)
    return found


def assert_translation_core_isolated() -> None:
    from engines.pipeline_integrity.exceptions import ArchitectureViolation

    imports = collect_imports()
    offenders = []
    for mod in imports:
        for bad in FORBIDDEN_IMPORT_PREFIXES:
            if mod == bad or mod.startswith(bad + "."):
                offenders.append(mod)
    if offenders:
        raise ArchitectureViolation(
            f"Translation Core isolation violated: {offenders}",
            stage="translation_core",
            rule="layer_isolation",
            details={"offenders": offenders},
        )
