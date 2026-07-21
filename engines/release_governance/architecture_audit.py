"""P17.5 — Architecture Audit (Single Owner / LOCK / Scheduler / imports)."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engines.pipeline_integrity.translation_lock import FIELD_OWNERS, LOCKED_TEXT_FIELDS

ROOT = Path(__file__).resolve().parents[2]

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

TIMING_ASSIGN_ALLOWLIST = (
    str(ROOT / "engines" / "scheduler").replace("\\", "/"),
    str(ROOT / "tests").replace("\\", "/"),
    str(ROOT / "engines" / "pipeline_integrity" / "segment.py").replace("\\", "/"),
    str(ROOT / "engines" / "streamdub" / "modules" / "segmenter.py").replace("\\", "/"),
    str(ROOT / "api" / "studio_api.py").replace("\\", "/"),
)


@dataclass
class AuditItem:
    name: str
    ok: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass
class ArchitectureAuditReport:
    ok: bool
    items: list[AuditItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "items": [i.to_dict() for i in self.items]}


def _py_files():
    for root in DUB_ROOTS:
        if root.is_file() and root.suffix == ".py":
            yield root
        elif root.is_dir():
            yield from root.rglob("*.py")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                names.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
    return names


def run_architecture_audit() -> ArchitectureAuditReport:
    items: list[AuditItem] = []

    # Single Owner map present
    owners_ok = all(f in FIELD_OWNERS for f in LOCKED_TEXT_FIELDS)
    items.append(
        AuditItem(
            "single_owner",
            ok=owners_ok,
            detail=f"locked_fields={len(LOCKED_TEXT_FIELDS)} owners={len(FIELD_OWNERS)}",
        )
    )

    # LOCK fields non-empty
    items.append(
        AuditItem(
            "translation_lock_fields",
            ok=len(LOCKED_TEXT_FIELDS) > 0,
            detail=",".join(sorted(LOCKED_TEXT_FIELDS)[:8]),
        )
    )

    # Forbidden imports
    violations = []
    for path in _py_files():
        for name in _imports(path):
            if any(name == p or name.startswith(p + ".") for p in FORBIDDEN):
                violations.append(f"{path.relative_to(ROOT)}:{name}")
    items.append(
        AuditItem(
            "forbidden_imports",
            ok=not violations,
            detail=";".join(violations[:10]) or "clean",
        )
    )

    # Direct timing assigns outside allowlist
    timing_hits = []
    for path in _py_files():
        norm = str(path).replace("\\", "/")
        if any(norm.startswith(a) for a in TIMING_ASSIGN_ALLOWLIST):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Attribute) and t.attr in ("start_ms", "end_ms"):
                        timing_hits.append(f"{path.relative_to(ROOT)}:{t.attr}")
                    if isinstance(t, ast.Subscript):
                        sl = t.slice
                        if isinstance(sl, ast.Constant) and sl.value in ("start_ms", "end_ms"):
                            timing_hits.append(f"{path.relative_to(ROOT)}:{sl.value}")
    items.append(
        AuditItem(
            "scheduler_bypass",
            ok=not timing_hits,
            detail=";".join(timing_hits[:15]) or "no direct timing assigns outside allowlist",
        )
    )

    # Immutable field mutation helpers exist
    from engines.pipeline_integrity import translation_lock as tl

    items.append(
        AuditItem(
            "immutable_guards",
            ok=callable(getattr(tl, "assert_segments_text_immutable", None)),
            detail="assert_segments_text_immutable present",
        )
    )

    return ArchitectureAuditReport(ok=all(i.ok for i in items), items=items)
