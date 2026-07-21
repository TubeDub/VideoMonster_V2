"""P623 Architecture Rules for Voice Platform."""

from __future__ import annotations

FORBIDDEN_IMPORTS_IN_DUB = (
    "engines.tts_engines.edge_engine",
    "engines.tts_engines.providers",
    "edge_tts",
)


def assert_voice_platform_isolated() -> None:
    """
    Dub Engine must not import concrete TTS providers.
    Voice Platform may wrap them; Translation/Scheduler must not call providers directly.
    """
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    targets = [
        root / "dub_engine_v2",
        root / "decision_policy",
        root / "translation_core",
    ]
    violations: list[str] = []
    for base in targets:
        if not base.exists():
            continue
        files = [base] if base.is_file() else list(base.rglob("*.py"))
        for path in files:
            if path.name.startswith("_") and path.name != "__init__.py":
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        name = alias.name
                        if name.startswith("engines.tts_engines") or name == "edge_tts":
                            violations.append(f"{path}: import {name}")
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    if mod.startswith("engines.tts_engines") or mod == "edge_tts":
                        violations.append(f"{path}: from {mod}")
    if violations:
        raise AssertionError("Voice Platform isolation violated:\n" + "\n".join(violations[:20]))


def assert_no_text_mutation(before: str, after: str) -> None:
    """P623 — TTS must not change Speech Unit text."""
    if (before or "") != (after or ""):
        raise AssertionError("TTS mutated Speech Unit text")


def assert_uuid_not_filename(voice_ref: str) -> None:
    """P623 — use UUID, not raw filenames as identity."""
    if voice_ref.lower().endswith((".wav", ".mp3", ".ogg")):
        raise AssertionError("voice identity must be UUID, not filename")
