"""Architecture lint — Dub Engine must not import Translation/LLM stack (TZ v2 P1)."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DUB_ROOTS = (
    ROOT / "engines" / "dubbing_engine",
    ROOT / "engines" / "dub",
    ROOT / "engines" / "scheduler",
    ROOT / "engines" / "audio_timing_optimizer.py",
    ROOT / "engines" / "dub_engine.py",
)

FORBIDDEN = (
    "engines.ai_core",
    "engines.translation_adapt",
    "engines.translation_pipeline",
    "engines.smart_segment_optimizer",
    "engines.adaptive_dubbing_adapter",
    "engines.semantic_adaptation",
    "engines.semantic_optimizer",
)
FORBIDDEN_TOKENS = ("ollama", "prompt_builder", "qwen", "grammar_agent", "semantic_agent")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                names.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            names.add(mod)
    return names


def _files():
    for root in DUB_ROOTS:
        if root.is_file():
            yield root
        elif root.is_dir():
            yield from root.rglob("*.py")


def test_dub_engine_forbidden_imports_lint():
    violations = []
    for path in _files():
        for name in _imports(path):
            low = name.lower()
            if any(name == p or name.startswith(p + ".") for p in FORBIDDEN):
                violations.append(f"{path.relative_to(ROOT)}: {name}")
            if any(t in low for t in FORBIDDEN_TOKENS):
                violations.append(f"{path.relative_to(ROOT)}: {name}")
    assert not violations, "Dub Engine import lint failed:\n" + "\n".join(violations)
