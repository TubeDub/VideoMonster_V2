"""P818 Knowledge Base + P819 Long-term Evolution Rules."""

from __future__ import annotations

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

EVOLUTION_RULES = (
    "No core rewrite without ADR",
    "No Single Owner violations",
    "No Semantic Lock violations",
    "No contract changes without Migration Engine",
    "No State Machine bypass",
    "No temporary hacks / TODO-fix-later in main branch",
    "Architecture Review → ADR → Implementation → Tests → Golden → Release",
)


def build_knowledge_base_index() -> dict[str, Any]:
    """P818 — ADR, architecture decisions, contracts, diagrams, pipeline, changelog."""
    adr_dir = ROOT / "docs" / "adr"
    adrs = sorted([p.name for p in adr_dir.glob("ADR-*.md")]) if adr_dir.is_dir() else []
    reports = []
    for pattern in ("*PART*.md", "*PART*.MD", "P16*.md", "P17*.md"):
        reports.extend([p.name for p in (ROOT / "docs").glob(pattern)])
    reports.extend([p.name for p in ROOT.glob("P1*_*.md")])
    reports = sorted(set(reports))

    contracts = {}
    try:
        from engines.pipeline_integrity.contract_versions import CONTRACT_META, CONTRACT_VERSIONS

        contracts = {"versions": dict(CONTRACT_VERSIONS), "meta": dict(CONTRACT_META)}
    except Exception:
        pass

    pipeline = [
        "Input",
        "Recognition",
        "Semantic Core",
        "Translation Core",
        "Semantic Lock",
        "Decision Policy Engine",
        "Dub Engine",
        "Voice Platform",
        "Scheduler",
        "Alignment",
        "Merge",
        "Studio",
        "Diagnostics",
        "Export",
        "Plugin Platform",
        "Cloud",
        "Enterprise Services",
    ]

    changelog = None
    for candidate in (
        ROOT / "docs" / "CHANGELOG_RELEASE.md",
        ROOT / "CHANGELOG.md",
    ):
        if candidate.is_file():
            changelog = str(candidate)
            break

    return {
        "adrs": adrs,
        "adr_count": len(adrs),
        "reports": reports,
        "contracts": contracts,
        "pipeline": pipeline,
        "evolution_rules": list(EVOLUTION_RULES),
        "changelog": changelog,
        "master_spec_parts": list(range(1, 10)),
    }


def assert_evolution_rules(*, require_adr_for_core: bool = True) -> dict[str, Any]:
    """P819 — validate knowledge base readiness for long-term evolution."""
    kb = build_knowledge_base_index()
    issues = []
    if require_adr_for_core and kb["adr_count"] < 12:
        issues.append("insufficient_adrs")
    if "ADR-019-platform-sdk-part8.md" not in kb["adrs"] and not any(
        "ADR-019" in a for a in kb["adrs"]
    ):
        issues.append("missing_part8_adr")
    return {"ok": len(issues) == 0, "issues": issues, "knowledge_base": kb}
