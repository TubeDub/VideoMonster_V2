"""AI Development Assistant — unified interface for project AI tools (TZ #10 §13).

All methods are read-only or produce proposals. No automatic code changes (§14).
Human developer always makes the final decision.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from core.architecture_engine import get_architecture_engine
from core.change_impact import get_change_impact_analyzer
from core.code_reviewer import get_code_reviewer
from core.development_history import get_development_history
from core.doc_sync import get_documentation_sync
from core.knowledge_base import get_knowledge_base
from core.recommendation_engine import get_recommendation_engine
from core.refactoring_advisor import get_refactoring_advisor
from core.task_planner import get_task_planner
from core.technical_debt import get_technical_debt_monitor

logger = logging.getLogger("tubedub.dev_assistant")


def assistant_enabled() -> bool:
    return str(os.getenv("VM_DEV_ASSISTANT", "1")).strip().lower() not in (
        "0", "false", "no", "off",
    )


class DevAssistant:
    """Unified AI development interface (§13)."""

    def __init__(self, *, app_dir: str | Path | None = None) -> None:
        self.app_dir = Path(app_dir) if app_dir else Path.cwd()
        self.brain = get_architecture_engine(self.app_dir)
        self.history = get_development_history(self.app_dir)
        self.kb = get_knowledge_base(self.app_dir)

    # ── Public API (§13) ─────────────────────────────────────────────

    def analyze(self, *, scope: str = "core") -> dict[str, Any]:
        """Full project analysis via Project Brain."""
        return {
            "structure": self.brain.scan_structure(),
            "dependencies": self.brain.dependency_graph(),
            "technical_debt": get_technical_debt_monitor(self.app_dir).summary(),
            "knowledge_base": self.kb.to_dict(),
            "brain_files": self.brain.scan_structure().get("brain_files", []),
            "policy": "Analysis only — no changes applied",
        }

    def plan(self, task: str) -> dict[str, Any]:
        """Create a development plan for a task (§7)."""
        planner = get_task_planner()
        dev_plan = planner.plan(task)
        impact = None
        if dev_plan.steps:
            files = [f for s in dev_plan.steps for f in s.files_hint]
            if files:
                impact = get_change_impact_analyzer().analyze(
                    files[:10], description=task, app_dir=self.app_dir,
                ).to_dict()
        return {
            "plan": dev_plan.to_dict(),
            "estimate": planner.estimate(task),
            "impact_preview": impact,
            "requires_approval": True,
        }

    def review(self, files: list[str] | None = None) -> dict[str, Any]:
        """Code review after changes (§4)."""
        if files:
            report = get_code_reviewer().review_files(files, app_dir=self.app_dir)
        else:
            report = get_code_reviewer().review_directory("core", app_dir=self.app_dir)
        return report.to_dict()

    def optimize(self) -> dict[str, Any]:
        """Performance and architecture optimization recommendations (§10)."""
        return get_recommendation_engine().to_dict(app_dir=self.app_dir)

    def document(self, *, sync: bool = True) -> dict[str, Any]:
        """Update Project Brain documentation (§6)."""
        if not sync:
            return {"brain": {f: self.brain.read_brain(f) for f in self.brain.BRAIN_FILES
                              if self.brain.brain_path(f).is_file()}}
        result = get_documentation_sync().sync_all(app_dir=self.app_dir)
        api_path = get_documentation_sync().sync_api_reference(app_dir=self.app_dir)
        if api_path:
            result["api_reference"] = api_path
        plugin_path = get_documentation_sync().sync_plugin_reference(app_dir=self.app_dir)
        if plugin_path:
            result["plugin_reference"] = plugin_path
        return result

    def test(self, *, targets: list[str] | None = None) -> dict[str, Any]:
        """Run tests and return results (never modifies code)."""
        cmd = ["python", "-m", "pytest", "-q"]
        if targets:
            cmd.extend(targets)
        else:
            cmd.append("tests/")
        try:
            proc = subprocess.run(
                cmd, cwd=str(self.app_dir),
                capture_output=True, text=True, timeout=300,
            )
            return {
                "ok": proc.returncode == 0,
                "returncode": proc.returncode,
                "stdout": proc.stdout[-8000:] if proc.stdout else "",
                "stderr": proc.stderr[-2000:] if proc.stderr else "",
            }
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "timeout"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def explain(self, topic: str) -> dict[str, Any]:
        """Explain a topic using knowledge base + Project Brain."""
        kb_results = self.kb.search(topic, limit=5)
        brain_hits: dict[str, str] = {}
        for fname in self.brain.BRAIN_FILES:
            content = self.brain.read_brain(fname)
            if topic.lower() in content.lower():
                idx = content.lower().index(topic.lower())
                brain_hits[fname] = content[max(0, idx - 100):idx + 300]

        # Module context.
        module_info = None
        for mod in self.brain.scan_structure().get("core_modules", []):
            if topic.lower() in mod.lower():
                module_info = self.brain.analyze_module(mod).__dict__
                break

        return {
            "topic": topic,
            "knowledge_base": kb_results,
            "brain_excerpts": brain_hits,
            "module": module_info,
        }

    def estimate(self, task: str) -> dict[str, Any]:
        """Estimate task complexity (§7)."""
        return get_task_planner().estimate(task)

    # ── Composite workflows ────────────────────────────────────────────

    def pre_change(self, files: list[str], *, description: str = "") -> dict[str, Any]:
        """Full pre-change workflow: impact + plan + architecture check (§3)."""
        impact = get_change_impact_analyzer().analyze(
            files, description=description, app_dir=self.app_dir,
        )
        return {
            "impact": impact.to_dict(),
            "refactoring_hints": get_refactoring_advisor().to_dict(app_dir=self.app_dir),
            "knowledge": self.kb.search(description or "architecture", limit=3),
            "requires_approval": True,
            "policy": "Developer must approve before applying changes (§14)",
        }

    def post_change(
        self,
        files: list[str],
        *,
        title: str = "",
        reason: str = "",
    ) -> dict[str, Any]:
        """Post-change workflow: review + doc sync + history (§4, §6, §11)."""
        review = self.review(files)
        doc_result = self.document(sync=True)
        change_id = self.history.record_change(
            title or f"Change to {len(files)} file(s)",
            files=files,
            reason=reason,
            impact=get_change_impact_analyzer().analyze(files, app_dir=self.app_dir).to_dict(),
            test_results={},
        )
        return {
            "review": review,
            "documentation": doc_result,
            "history_id": change_id,
            "recommendations": self.optimize(),
        }

    def self_diagnose(self, project_id: str = "", metrics: dict[str, Any] | None = None) -> dict[str, Any]:
        """Continuous self-diagnostics after project completion (§9)."""
        recs = self.optimize()
        debt = get_technical_debt_monitor(self.app_dir).summary()

        diagnostics: dict[str, Any] = {"project_id": project_id, "metrics": metrics or {}}
        try:
            from core.monitoring_center import get_monitor, monitoring_enabled
            if monitoring_enabled():
                mon = get_monitor(app_dir=self.app_dir)
                diagnostics["monitoring"] = mon.get_diagnostics()
                diagnostics["bottleneck"] = mon.get_bottleneck()
        except Exception:
            pass

        # Record in knowledge base if issues found.
        for rec in recs.get("recommendations", [])[:3]:
            if rec.get("severity") in ("warning", "high"):
                self.kb.add(
                    "lesson",
                    rec.get("title", "Issue"),
                    rec.get("detail", ""),
                    tags=["auto-diagnose", project_id or "system"],
                    source="self_diagnose",
                )

        return {
            "diagnostics": diagnostics,
            "technical_debt": debt,
            "recommendations": recs,
            "policy": "Recommendations only — developer decides (§14)",
        }

    def get_status(self) -> dict[str, Any]:
        return {
            "enabled": assistant_enabled(),
            "brain_dir": str(self.brain.brain_dir),
            "brain_files": self.brain.scan_structure().get("brain_files", []),
            "knowledge_base": self.kb.to_dict(),
            "recent_changes": len(self.history.recent_changes(limit=5)),
            "recent_decisions": len(self.history.recent_decisions(limit=5)),
        }


_assistant: DevAssistant | None = None
_assistant_lock = __import__("threading").Lock()


def get_dev_assistant(*, app_dir: str | Path | None = None) -> DevAssistant:
    global _assistant
    if _assistant is None:
        with _assistant_lock:
            if _assistant is None:
                _assistant = DevAssistant(app_dir=app_dir)
    return _assistant


def reset_dev_assistant() -> None:
    global _assistant
    with _assistant_lock:
        _assistant = None
