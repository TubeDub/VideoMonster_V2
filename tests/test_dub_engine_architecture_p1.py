"""Architecture tests — Freeze TZ P1 Dub Engine / Scheduler boundaries."""

from __future__ import annotations

import ast
import copy
from pathlib import Path

import pytest

from engines.pipeline_integrity import StageSnapshotGuard, TranslationLockError
from engines.pipeline_integrity.exceptions import StageSnapshotIntegrityError
from engines.scheduler import update_time

ROOT = Path(__file__).resolve().parents[1]

# Packages that constitute the post-LOCK Dub Engine surface.
DUB_ENGINE_PACKAGES = (
    ROOT / "engines" / "dubbing_engine",
    ROOT / "engines" / "dub",
    ROOT / "engines" / "scheduler",
    ROOT / "engines" / "dub_engine.py",
)

FORBIDDEN_IMPORT_PREFIXES = (
    "engines.ai_core",
    "engines.translation_adapt",
    "engines.translation_pipeline",
    "engines.translation_review",
    "engines.smart_segment_optimizer",
    "engines.adaptive_dubbing_adapter",
    "engines.semantic_adaptation",
    "engines.semantic_optimizer",
)

FORBIDDEN_NAME_TOKENS = (
    "qwen",
    "ollama",
    "grammar_agent",
    "semantic_agent",
    "prompt_builder",
    "agent_coordinator",
    "ai_coordinator",
)

# Modules allowed to assign start_ms / end_ms directly (construction / Scheduler / tests / Studio UI).
TIMING_ASSIGN_ALLOWLIST_PREFIXES = (
    str(ROOT / "engines" / "scheduler").replace("\\", "/"),
    str(ROOT / "tests").replace("\\", "/"),
    str(ROOT / "engines" / "pipeline_integrity" / "segment.py").replace("\\", "/"),
    str(ROOT / "engines" / "streamdub" / "modules" / "segmenter.py").replace("\\", "/"),
    # Studio user edits still go through API layer; full Studio→Scheduler migration is follow-up.
    str(ROOT / "api" / "studio_api.py").replace("\\", "/"),
)

TIMING_FIELDS = ("start_ms", "end_ms", "start_time", "end_time")


def _iter_py_files(path: Path):
    if path.is_file() and path.suffix == ".py":
        yield path
        return
    if path.is_dir():
        for p in path.rglob("*.py"):
            if p.name == "__pycache__":
                continue
            yield p


def _collect_imports(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            names.add(mod)
            for alias in node.names:
                if mod:
                    names.add(f"{mod}.{alias.name}")
                else:
                    names.add(alias.name)
    return names


def _is_forbidden_import(name: str) -> bool:
    low = name.lower()
    for prefix in FORBIDDEN_IMPORT_PREFIXES:
        if name == prefix or name.startswith(prefix + "."):
            return True
    for token in FORBIDDEN_NAME_TOKENS:
        if token in low.replace("-", "_"):
            return True
    return False


class TestDubEngineImportBoundary:
    def test_dub_engine_does_not_import_ai_core_or_translation(self):
        violations: list[str] = []
        for root in DUB_ENGINE_PACKAGES:
            if not root.exists():
                continue
            for path in _iter_py_files(root):
                for name in _collect_imports(path):
                    if _is_forbidden_import(name):
                        violations.append(f"{path.relative_to(ROOT)}: import {name}")
        assert not violations, "Dub Engine import boundary broken:\n" + "\n".join(
            violations
        )


class TestSchedulerImportBoundary:
    def test_scheduler_does_not_import_translation(self):
        violations: list[str] = []
        sched_root = ROOT / "engines" / "scheduler"
        for path in _iter_py_files(sched_root):
            for name in _collect_imports(path):
                if name.startswith("engines.translation") or name.startswith(
                    "engines.ai_core"
                ):
                    violations.append(f"{path.name}: import {name}")
                if any(t in name.lower() for t in ("qwen", "ollama", "grammar_agent")):
                    violations.append(f"{path.name}: import {name}")
        assert not violations, "Scheduler must not call Translation/LLM:\n" + "\n".join(
            violations
        )


class TestDirectTimingMutationForbidden:
    def test_slot_fit_stage_cannot_mutate_start_ms(self):
        before = [
            {
                "segment_id": "s1",
                "index": 0,
                "text": "hi",
                "start_ms": 0,
                "end_ms": 1000,
                "translation_locked": True,
            }
        ]
        after = copy.deepcopy(before)
        after[0]["start_ms"] = 50
        with pytest.raises((StageSnapshotIntegrityError, TranslationLockError)):
            StageSnapshotGuard.check(before, after, stage="slot_fit")

    def test_scheduler_stage_can_mutate_start_ms(self):
        before = [
            {
                "segment_id": "s1",
                "index": 0,
                "text": "hi",
                "start_ms": 0,
                "end_ms": 1000,
                "translation_locked": True,
            }
        ]
        after = copy.deepcopy(before)
        update_time(after, "s1", start_ms=50, end_ms=1000)
        StageSnapshotGuard.check(before, after, stage="scheduler")

    def test_no_direct_start_ms_assign_outside_allowlist(self):
        """AST scan: segment['start_ms'] = ... must live only in allowlisted modules."""
        violations: list[str] = []
        scan_roots = [
            ROOT / "engines",
            ROOT / "api" / "auto_dub_api.py",
        ]
        for root in scan_roots:
            for path in _iter_py_files(root):
                norm = str(path).replace("\\", "/")
                if any(norm.startswith(p) or p in norm for p in TIMING_ASSIGN_ALLOWLIST_PREFIXES):
                    continue
                # Skip scheduler package itself
                if "/engines/scheduler/" in norm or norm.endswith("/engines/scheduler"):
                    continue
                try:
                    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                except SyntaxError:
                    continue
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Assign):
                        continue
                    for target in node.targets:
                        if not isinstance(target, ast.Subscript):
                            continue
                        sl = target.slice
                        key = None
                        if isinstance(sl, ast.Constant):
                            key = sl.value
                        if key not in TIMING_FIELDS:
                            continue
                        # Allow reading patterns? Assign only.
                        violations.append(
                            f"{path.relative_to(ROOT)}:{node.lineno} assigns {key!r}"
                        )
        assert not violations, (
            "Direct start/end mutation outside Scheduler:\n" + "\n".join(violations)
        )
