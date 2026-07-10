"""AI Recommendation Engine — aggregate improvement suggestions (TZ #10 §10, §14)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.recommendation_engine")


@dataclass
class Recommendation:
    category: str
    title: str
    detail: str
    action: str
    severity: str = "info"
    auto_apply: bool = False  # Always False (§14)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "title": self.title,
            "detail": self.detail,
            "action": self.action,
            "severity": self.severity,
            "auto_apply": self.auto_apply,
        }


class RecommendationEngine:
    """Aggregate recommendations from all platform layers (§10). Never auto-applies."""

    def generate(
        self,
        *,
        app_dir: str | Path | None = None,
        project_id: str = "",
    ) -> list[Recommendation]:
        recs: list[Recommendation] = []

        # Performance Optimizer recommendations.
        try:
            from core.performance_optimizer import get_performance_optimizer, optimizer_enabled
            if optimizer_enabled():
                opt = get_performance_optimizer(app_dir=app_dir)
                plan = opt.plan()
                for note in plan.notes:
                    recs.append(Recommendation(
                        "performance", "Performance Optimizer",
                        note, "Review optimizer plan", severity="info",
                    ))
        except Exception as exc:
            logger.debug("perf recs: %s", exc)

        # Monitoring / bottleneck.
        try:
            from core.monitoring_center import get_monitor, monitoring_enabled
            if monitoring_enabled():
                mon = get_monitor(app_dir=app_dir)
                bn = mon.get_bottleneck()
                for r in bn.get("recommendations") or []:
                    recs.append(Recommendation(
                        "pipeline", r.get("cause", "Bottleneck"),
                        r.get("detail", ""), r.get("action", "Investigate"),
                        severity=r.get("severity", "medium"),
                    ))
        except Exception:
            pass

        # Technical debt.
        try:
            from core.refactoring_advisor import get_refactoring_advisor
            for s in get_refactoring_advisor().suggest(app_dir=app_dir)[:5]:
                recs.append(Recommendation(
                    "refactoring", s.category,
                    s.rationale, s.suggestion, severity="low",
                ))
        except Exception:
            pass

        # Plugin suggestions.
        try:
            from core.plugin_manager import get_plugin_manager, plugins_enabled
            if plugins_enabled():
                mgr = get_plugin_manager(app_dir=app_dir)
                for p in mgr.list_plugins():
                    if p.get("state") == "failed":
                        recs.append(Recommendation(
                            "plugin", f"Plugin {p['name']} failed",
                            p.get("error", ""), f"Fix or disable plugin {p['name']}",
                            severity="warning",
                        ))
        except Exception:
            pass

        # Knowledge base hints.
        try:
            from core.knowledge_base import get_knowledge_base
            kb = get_knowledge_base(app_dir)
            for entry in kb.by_category("architecture", limit=2):
                recs.append(Recommendation(
                    "knowledge", entry["title"],
                    entry["content"][:200], "Follow architecture best practice",
                    severity="info",
                ))
        except Exception:
            pass

        return recs

    def to_dict(self, **kwargs: Any) -> dict[str, Any]:
        recs = self.generate(**kwargs)
        return {
            "count": len(recs),
            "recommendations": [r.to_dict() for r in recs],
            "policy": "No recommendation is auto-applied — developer decides (§14)",
        }


_engine: RecommendationEngine | None = None


def get_recommendation_engine() -> RecommendationEngine:
    global _engine
    if _engine is None:
        _engine = RecommendationEngine()
    return _engine
