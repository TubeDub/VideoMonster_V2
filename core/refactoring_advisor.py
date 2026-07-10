"""AI Refactoring Assistant — recommendations only (TZ #10 §5, §14)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.technical_debt import get_technical_debt_monitor


@dataclass
class RefactoringSuggestion:
    category: str
    file: str
    suggestion: str
    rationale: str
    priority: int = 50
    # Never auto-applied
    approved: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "file": self.file,
            "suggestion": self.suggestion,
            "rationale": self.rationale,
            "priority": self.priority,
            "approved": self.approved,
        }


class RefactoringAdvisor:
    """Propose refactoring — never modifies code (§5, §14)."""

    def suggest(self, *, app_dir: str | Path | None = None) -> list[RefactoringSuggestion]:
        debt = get_technical_debt_monitor(app_dir)
        summary = debt.summary()
        suggestions: list[RefactoringSuggestion] = []

        for item in summary.get("items", []):
            cat = item.get("category", "")
            file = item.get("file", "")
            detail = item.get("detail", "")

            if cat == "large_file":
                suggestions.append(RefactoringSuggestion(
                    "split_module", file,
                    f"Consider splitting {file} into smaller modules",
                    detail, priority=60,
                ))
            elif cat == "complex_function":
                suggestions.append(RefactoringSuggestion(
                    "simplify", file,
                    "Extract helper functions to reduce complexity",
                    detail, priority=65,
                ))
            elif cat == "duplication":
                suggestions.append(RefactoringSuggestion(
                    "deduplicate", file,
                    "Extract shared logic into a utility module",
                    detail, priority=70,
                ))
            elif cat == "high_complexity":
                suggestions.append(RefactoringSuggestion(
                    "simplify", file,
                    "Reduce cyclomatic complexity with early returns or strategy pattern",
                    detail, priority=75,
                ))
            elif cat == "todo":
                suggestions.append(RefactoringSuggestion(
                    "resolve_todo", file,
                    "Resolve or ticket this TODO/FIXME",
                    detail, priority=30,
                ))

        return sorted(suggestions, key=lambda s: s.priority, reverse=True)

    def to_dict(self, *, app_dir: str | Path | None = None) -> dict[str, Any]:
        items = self.suggest(app_dir=app_dir)
        return {
            "count": len(items),
            "suggestions": [s.to_dict() for s in items],
            "note": "All suggestions require developer approval before applying (§14)",
        }


_advisor: RefactoringAdvisor | None = None


def get_refactoring_advisor() -> RefactoringAdvisor:
    global _advisor
    if _advisor is None:
        _advisor = RefactoringAdvisor()
    return _advisor
