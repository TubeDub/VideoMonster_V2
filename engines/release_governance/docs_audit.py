"""P17.6 — Documentation Audit before major release."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

REQUIRED_DOCS = (
    ("Architecture Specification", ROOT / "docs" / "ARCHITECTURE.md"),
    ("ADR-001 Translation Lock", ROOT / "docs" / "adr" / "ADR-001-translation-lock.md"),
    ("ADR-002 Audio First", ROOT / "docs" / "adr" / "ADR-002-audio-first.md"),
    ("ADR-003 Scheduler Owner", ROOT / "docs" / "adr" / "ADR-003-scheduler-owner.md"),
    ("ADR-004 Versioned Contracts", ROOT / "docs" / "adr" / "ADR-004-versioned-contracts.md"),
    ("ADR-005 State Machine", ROOT / "docs" / "adr" / "ADR-005-state-machine.md"),
    ("ADR-006 Deterministic Dub", ROOT / "docs" / "adr" / "ADR-006-deterministic-dub.md"),
    ("API Contracts (SDK)", ROOT / "sdk" / "docs" / "API_REFERENCE.md"),
    ("Migration Guide", ROOT / "sdk" / "docs" / "MIGRATION_GUIDE.md"),
    ("Troubleshooting Guide", ROOT / "docs" / "LONG_VIDEO_TEST_CHECKLIST.md"),
    ("Changelog", ROOT / "docs" / "CHANGELOG_RELEASE.md"),
)


@dataclass
class DocItem:
    name: str
    path: str
    ok: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "ok": self.ok,
            "detail": self.detail,
        }


@dataclass
class DocsAuditReport:
    ok: bool
    items: list[DocItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "items": [i.to_dict() for i in self.items]}


CHANGELOG_STUB = """# Release Changelog

## Unreleased

- P17 Quality Certification & Release Governance
- P16 Production Hardening
- P0–P15 Dub Engine Stabilization (Translation Lock, Scheduler, Runtime Integrity)

## Known limitations

- Full golden dataset (20 films / 10k segments) is scaffolded; content fill is ongoing.
- Neural TTS backends require installed packages on the target machine.
- P5 UI dashboards are API/data-ready; full UI is a separate frontend track.
- Lab 8h/24h Production Hardening must be run on release hardware before GA.
"""


def ensure_changelog() -> Path:
    path = ROOT / "docs" / "CHANGELOG_RELEASE.md"
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(CHANGELOG_STUB, encoding="utf-8")
    return path


def run_docs_audit(*, create_missing_changelog: bool = True) -> DocsAuditReport:
    if create_missing_changelog:
        ensure_changelog()
    items: list[DocItem] = []
    for name, path in REQUIRED_DOCS:
        exists = path.is_file()
        size = path.stat().st_size if exists else 0
        items.append(
            DocItem(
                name=name,
                path=str(path.relative_to(ROOT)).replace("\\", "/"),
                ok=exists and size > 20,
                detail="present" if exists else "MISSING",
            )
        )
    return DocsAuditReport(ok=all(i.ok for i in items), items=items)
