"""Diagnostics Center — automatic system health checks (TZ #8 §9).

Scans live monitoring data for:
- stuck tasks
- overflowing queues
- memory leaks (rising RAM trend)
- duplicate processing
- idle agents

Read-only — never modifies restricted modules.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("tubedub.diagnostics")

_STUCK_THRESHOLD_S = 300.0
_QUEUE_OVERFLOW_RATIO = 0.9
_MEMORY_LEAK_SLOPE = 2.0  # % RAM increase per sample
_IDLE_AGENT_S = 120.0


@dataclass
class DiagnosticIssue:
    category: str
    severity: str  # info | warning | critical
    message: str
    detail: str = ""
    stage: str = ""
    agent: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "severity": self.severity,
            "message": self.message,
            "detail": self.detail,
            "stage": self.stage,
            "agent": self.agent,
        }


@dataclass
class DiagnosticReport:
    issues: list[DiagnosticIssue] = field(default_factory=list)
    scanned_at: float = field(default_factory=time.time)
    healthy: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "healthy": self.healthy,
            "issue_count": len(self.issues),
            "issues": [i.to_dict() for i in self.issues],
            "scanned_at": self.scanned_at,
        }


class DiagnosticsCenter:
    """Automatic diagnostics engine (§9)."""

    def run_full_scan(self, state: dict[str, Any]) -> DiagnosticReport:
        report = DiagnosticReport()
        report.issues.extend(self.detect_stuck_tasks(state))
        report.issues.extend(self.detect_queue_overflow(state))
        report.issues.extend(self.detect_memory_leak(state))
        report.issues.extend(self.detect_duplicate_processing(state))
        report.issues.extend(self.detect_idle_agents(state))
        report.healthy = not any(i.severity == "critical" for i in report.issues)
        return report

    def detect_stuck_tasks(self, state: dict[str, Any]) -> list[DiagnosticIssue]:
        issues: list[DiagnosticIssue] = []
        now = time.time()
        recovery = state.get("recovery") or {}
        for task in recovery.get("running_tasks") or []:
            started = float(task.get("started_at") or 0)
            if started and (now - started) > _STUCK_THRESHOLD_S:
                issues.append(DiagnosticIssue(
                    category="stuck_task",
                    severity="critical",
                    message=f"Task stuck on stage {task.get('stage', '?')}",
                    detail=f"Running for {now - started:.0f}s (chunk {task.get('chunk_id', '?')})",
                    stage=str(task.get("stage") or ""),
                ))

        agents = (state.get("agents") or {}).get("agents") or state.get("agents") or {}
        for name, info in agents.items():
            if isinstance(info, dict):
                state_name = str(info.get("state") or "")
                last_active = float(info.get("last_active_at") or info.get("started_at") or 0)
                if state_name == "working" and last_active and (now - last_active) > _STUCK_THRESHOLD_S:
                    issues.append(DiagnosticIssue(
                        category="stuck_task",
                        severity="warning",
                        message=f"Agent {name} may be stuck",
                        detail=f"No progress for {now - last_active:.0f}s",
                        agent=name,
                    ))
        return issues

    def detect_queue_overflow(self, state: dict[str, Any]) -> list[DiagnosticIssue]:
        issues: list[DiagnosticIssue] = []
        queues = state.get("queues") or {}
        for name, qinfo in queues.items():
            if not isinstance(qinfo, dict):
                continue
            current = int(qinfo.get("current_size") or qinfo.get("depth") or 0)
            maximum = int(qinfo.get("max_size") or qinfo.get("capacity") or 0)
            if maximum > 0 and current / maximum >= _QUEUE_OVERFLOW_RATIO:
                issues.append(DiagnosticIssue(
                    category="queue_overflow",
                    severity="warning",
                    message=f"Queue {name} near capacity",
                    detail=f"{current}/{maximum} ({current/maximum*100:.0f}%)",
                    stage=name,
                ))
            dropped = int(qinfo.get("dropped_tasks") or 0)
            if dropped > 0:
                issues.append(DiagnosticIssue(
                    category="queue_overflow",
                    severity="critical",
                    message=f"Queue {name} dropped {dropped} tasks",
                    stage=name,
                ))
        return issues

    def detect_memory_leak(self, state: dict[str, Any]) -> list[DiagnosticIssue]:
        issues: list[DiagnosticIssue] = []
        history = state.get("resource_history") or []
        if len(history) < 5:
            return issues
        rams = [float(h.get("ram_percent", 0)) for h in history[-10:]]
        slope = (rams[-1] - rams[0]) / max(1, len(rams) - 1)
        if slope >= _MEMORY_LEAK_SLOPE and rams[-1] >= 70.0:
            issues.append(DiagnosticIssue(
                category="memory_leak",
                severity="warning",
                message="Possible memory pressure trend",
                detail=f"RAM rising {slope:.1f}%/sample, now {rams[-1]:.0f}%",
            ))
        return issues

    def detect_duplicate_processing(self, state: dict[str, Any]) -> list[DiagnosticIssue]:
        issues: list[DiagnosticIssue] = []
        stats = state.get("statistics") or {}
        retries = int(stats.get("total_retries") or 0)
        chunks = int(stats.get("chunks_processed") or 0)
        if chunks > 0 and retries > chunks * 2:
            issues.append(DiagnosticIssue(
                category="duplicate_processing",
                severity="warning",
                message="High retry rate detected",
                detail=f"{retries} retries for {chunks} chunks",
            ))
        parking = (state.get("recovery") or {}).get("parking_count") or 0
        if parking > 3:
            issues.append(DiagnosticIssue(
                category="duplicate_processing",
                severity="info",
                message=f"{parking} chunks in parking queue",
                detail="Chunks may be reprocessed after recovery",
            ))
        return issues

    def detect_idle_agents(self, state: dict[str, Any]) -> list[DiagnosticIssue]:
        issues: list[DiagnosticIssue] = []
        agents = (state.get("agents") or {}).get("agents") or state.get("agents") or {}
        pipeline_busy = bool(state.get("pipeline_running"))
        if not pipeline_busy:
            return issues

        for name, info in agents.items():
            if not isinstance(info, dict):
                continue
            state_name = str(info.get("state") or "")
            if state_name in ("idle", "paused", "ready"):
                issues.append(DiagnosticIssue(
                    category="idle_agent",
                    severity="info",
                    message=f"Agent {name} is idle while pipeline runs",
                    agent=name,
                ))
        return issues

    def build_ai_report(
        self,
        bottleneck: dict[str, Any],
        diagnostics: DiagnosticReport,
    ) -> dict[str, Any]:
        """Post-project AI diagnostics summary (§11)."""
        primary = bottleneck.get("primary") or "unknown"
        primary_pct = bottleneck.get("primary_percent") or 0
        recs = list(bottleneck.get("recommendations") or [])

        summary_parts = []
        if primary_pct >= 40:
            top_rec = recs[0] if recs else {}
            summary_parts.append({
                "title": "Основная причина замедления",
                "cause": top_rec.get("cause") or primary,
                "detail": top_rec.get("detail") or f"{primary_pct:.0f}% pipeline time",
                "recommendation": top_rec.get("action") or "Increase resources",
            })

        for issue in diagnostics.issues:
            if issue.severity in ("warning", "critical"):
                summary_parts.append({
                    "title": issue.category.replace("_", " ").title(),
                    "cause": issue.message,
                    "detail": issue.detail,
                    "recommendation": self._suggest_fix(issue),
                })

        return {
            "summary": summary_parts,
            "healthy": diagnostics.healthy,
            "bottleneck": bottleneck,
            "issue_count": len(diagnostics.issues),
        }

    @staticmethod
    def _suggest_fix(issue: DiagnosticIssue) -> str:
        fixes = {
            "stuck_task": "Check recovery manager or restart the affected stage",
            "queue_overflow": "Increase queue size or add workers",
            "memory_leak": "Reduce chunk size and concurrency",
            "duplicate_processing": "Review recovery retries and parking queue",
            "idle_agent": "Rebalance workers to utilise idle agents",
        }
        return fixes.get(issue.category, "Review system logs")


_center: DiagnosticsCenter | None = None


def get_diagnostics_center() -> DiagnosticsCenter:
    global _center
    if _center is None:
        _center = DiagnosticsCenter()
    return _center
