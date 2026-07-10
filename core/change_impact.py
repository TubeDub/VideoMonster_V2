"""Change Impact Analyzer — pre-change risk assessment (TZ #10 §3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.architecture_engine import get_architecture_engine


@dataclass
class ChangePlan:
    """Structured change plan — always produced before implementation (§3)."""
    title: str
    files: list[str] = field(default_factory=list)
    affected_tests: list[str] = field(default_factory=list)
    dependents: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    side_effects: list[str] = field(default_factory=list)
    rule_violations: list[dict[str, str]] = field(default_factory=list)
    recommended_steps: list[str] = field(default_factory=list)
    approved: bool = False  # Human must approve (§14)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "files": self.files,
            "affected_tests": self.affected_tests,
            "dependents": self.dependents,
            "risks": self.risks,
            "side_effects": self.side_effects,
            "rule_violations": self.rule_violations,
            "recommended_steps": self.recommended_steps,
            "approved": self.approved,
        }


class ChangeImpactAnalyzer:
    """Analyze impact before any change (§3). Never applies changes."""

    def analyze(
        self,
        files: list[str],
        *,
        description: str = "",
        app_dir: str | Path | None = None,
    ) -> ChangePlan:
        engine = get_architecture_engine(app_dir)
        impact = engine.assess_change_impact(files)

        plan = ChangePlan(
            title=description or f"Change to {len(files)} file(s)",
            files=files,
            affected_tests=impact.get("affected_tests", []),
            dependents=impact.get("dependents", []),
            risks=list(impact.get("risks", [])),
            rule_violations=impact.get("rule_violations", []),
        )

        # Side effects from dependency chain.
        if plan.dependents:
            plan.side_effects.append(
                f"May affect {len(plan.dependents)} dependent module(s): "
                + ", ".join(plan.dependents[:5])
            )

        # Recommended steps.
        plan.recommended_steps = [
            "Review architecture rule violations",
            "Run affected tests: " + ", ".join(plan.affected_tests[:5]) if plan.affected_tests
            else "Run smoke tests",
        ]
        if impact.get("requires_full_suite"):
            plan.recommended_steps.append("Run full test suite (core/ modified)")
            plan.risks.append("Full regression required")
        plan.recommended_steps.append("Update .ai/CHANGELOG.md after merge")
        plan.recommended_steps.append("Developer approval required before applying (§14)")

        return plan

    def analyze_diff(self, diff_text: str, *, app_dir: str | Path | None = None) -> ChangePlan:
        """Extract files from a unified diff and analyze."""
        files: list[str] = []
        for line in diff_text.splitlines():
            if line.startswith("+++ ") or line.startswith("--- "):
                path = line[4:].strip()
                if path.startswith("a/") or path.startswith("b/"):
                    path = path[2:]
                if path != "/dev/null" and not path.startswith("/"):
                    files.append(path.replace("\\", "/"))
        return self.analyze(list(set(files)), description="Diff analysis", app_dir=app_dir)


_analyzer: ChangeImpactAnalyzer | None = None


def get_change_impact_analyzer() -> ChangeImpactAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = ChangeImpactAnalyzer()
    return _analyzer
