"""Intelligent Task Planner — break down development tasks (TZ #10 §7)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Keyword → stage mapping for dependency ordering.
_STAGE_KEYWORDS = {
    1: ("event bus", "event_bus", "bus"),
    2: ("orchestrator", "agent"),
    3: ("llm", "dispatcher", "model"),
    4: ("pipeline", "chunk"),
    5: ("recovery", "validator", "fault"),
    6: ("memory", "cache", "semantic"),
    7: ("performance", "benchmark", "hardware"),
    8: ("monitoring", "diagnostics", "analytics"),
    9: ("plugin", "sdk"),
    10: ("brain", "assistant", "debt", "architecture"),
}


@dataclass
class TaskStep:
    order: int
    title: str
    description: str
    complexity: str  # low | medium | high
    dependencies: list[int] = field(default_factory=list)
    files_hint: list[str] = field(default_factory=list)
    tests_hint: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "order": self.order,
            "title": self.title,
            "description": self.description,
            "complexity": self.complexity,
            "dependencies": self.dependencies,
            "files_hint": self.files_hint,
            "tests_hint": self.tests_hint,
        }


@dataclass
class DevelopmentPlan:
    title: str
    steps: list[TaskStep] = field(default_factory=list)
    estimated_complexity: str = "medium"
    total_steps: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "estimated_complexity": self.estimated_complexity,
            "total_steps": len(self.steps),
            "steps": [s.to_dict() for s in self.steps],
        }


class TaskPlanner:
    """Break large tasks into ordered development steps (§7)."""

    def plan(self, task_description: str) -> DevelopmentPlan:
        desc = task_description.strip()
        plan = DevelopmentPlan(title=desc[:120])

        # Split on numbered items, bullets, or sentences.
        parts = re.split(r"(?:\n\s*[-*]\s+|\n\s*\d+[\.\)]\s+|\.\s+)", desc)
        parts = [p.strip() for p in parts if p.strip()]

        if len(parts) <= 1:
            parts = self._default_steps(desc)

        stages_found: list[int] = []
        for part in parts:
            stage = self._detect_stage(part.lower())
            if stage:
                stages_found.append(stage)

        for i, part in enumerate(parts, 1):
            stage = self._detect_stage(part.lower())
            complexity = self._estimate_complexity(part)
            deps = [i - 1] if i > 1 else []
            if stage and stage > 1:
                prev_stage_steps = [
                    j for j, p in enumerate(parts[:i - 1], 1)
                    if self._detect_stage(p.lower()) and self._detect_stage(p.lower()) < stage
                ]
                if prev_stage_steps:
                    deps = list(set(deps + prev_stage_steps))

            files_hint = []
            tests_hint = []
            if stage:
                files_hint.append(f"core/ (stage {stage} area)")
                tests_hint.append(f"tests/test_*")

            plan.steps.append(TaskStep(
                order=i,
                title=part[:80],
                description=part,
                complexity=complexity,
                dependencies=deps,
                files_hint=files_hint,
                tests_hint=tests_hint,
            ))

        complexities = [s.complexity for s in plan.steps]
        if "high" in complexities:
            plan.estimated_complexity = "high"
        elif all(c == "low" for c in complexities):
            plan.estimated_complexity = "low"

        plan.total_steps = len(plan.steps)
        return plan

    @staticmethod
    def _detect_stage(text: str) -> int | None:
        for stage, keywords in _STAGE_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                return stage
        return None

    @staticmethod
    def _estimate_complexity(text: str) -> str:
        words = len(text.split())
        if words > 40 or any(w in text for w in ("refactor", "migrate", "rewrite", "architecture")):
            return "high"
        if words > 15:
            return "medium"
        return "low"

    @staticmethod
    def _default_steps(desc: str) -> list[str]:
        return [
            f"Analyze impact: {desc[:60]}",
            "Implement core changes",
            "Add tests",
            "Update .ai/ documentation",
            "Run full test suite",
            "Developer review and approval",
        ]

    def estimate(self, task_description: str) -> dict[str, Any]:
        plan = self.plan(task_description)
        complexity_scores = {"low": 1, "medium": 3, "high": 8}
        total = sum(complexity_scores.get(s.complexity, 2) for s in plan.steps)
        return {
            "steps": len(plan.steps),
            "complexity": plan.estimated_complexity,
            "effort_score": total,
            "effort_label": "small" if total <= 5 else "medium" if total <= 15 else "large",
        }


_planner: TaskPlanner | None = None


def get_task_planner() -> TaskPlanner:
    global _planner
    if _planner is None:
        _planner = TaskPlanner()
    return _planner
