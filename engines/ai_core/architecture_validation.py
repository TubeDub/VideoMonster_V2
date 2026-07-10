"""AI Core 4.3 — architecture_validation.json for OpenDDF / diagnostics."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_APP_DIR = Path(__file__).resolve().parents[2]
AI_CORE_VERSION = "4.3"

UX_CHECKLIST = (
    "back_navigation",
    "cancel_supported",
    "params_preserved_on_cancel",
    "change_lang_without_full_restart",
    "change_voice_without_full_restart",
    "safe_stop_long_operations",
    "restart_from_checkpoint",
)


@dataclass
class UxValidationMetrics:
    """Global UX Standard (TZ §9–§10) — capability + runtime events."""

    back_navigation: bool = True
    cancel_supported: bool = True
    params_preserved_on_cancel: bool = True
    change_lang_without_full_restart: bool = True
    change_voice_without_full_restart: bool = True
    safe_stop_long_operations: bool = True
    restart_from_checkpoint: bool = True
    cancel_events: int = 0
    restart_events: int = 0
    last_checkpoint: str = ""
    last_cancel_at: float | None = None

    def checks_passed(self) -> int:
        flags = (
            self.back_navigation,
            self.cancel_supported,
            self.params_preserved_on_cancel,
            self.change_lang_without_full_restart,
            self.change_voice_without_full_restart,
            self.safe_stop_long_operations,
            self.restart_from_checkpoint,
        )
        return sum(1 for f in flags if f)

    def to_dict(self) -> dict[str, Any]:
        passed = self.checks_passed()
        total = len(UX_CHECKLIST)
        return {
            "back_navigation": self.back_navigation,
            "cancel_supported": self.cancel_supported,
            "params_preserved_on_cancel": self.params_preserved_on_cancel,
            "change_lang_without_full_restart": self.change_lang_without_full_restart,
            "change_voice_without_full_restart": self.change_voice_without_full_restart,
            "safe_stop_long_operations": self.safe_stop_long_operations,
            "restart_from_checkpoint": self.restart_from_checkpoint,
            "cancel_events": self.cancel_events,
            "restart_events": self.restart_events,
            "last_checkpoint": self.last_checkpoint,
            "last_cancel_at": self.last_cancel_at,
            "checks_passed": passed,
            "checks_total": total,
            "passed": passed >= total,
        }


@dataclass
class AgentMetrics:
    name: str
    execution_time_ms: float = 0.0
    status: str = "success"
    peer_validation_passed: bool = True
    peer_returns: int = 0
    retry_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "execution_time_ms": round(self.execution_time_ms, 1),
            "status": self.status,
            "peer_validation_passed": self.peer_validation_passed,
            "peer_returns": self.peer_returns,
            "retry_count": self.retry_count,
        }


@dataclass
class ArchitectureMetrics:
    """Mutable collector during orchestrator run."""

    task_id: str = ""
    started_at: float = field(default_factory=time.perf_counter)
    active_agents: list[str] = field(default_factory=list)
    agent_metrics: dict[str, AgentMetrics] = field(default_factory=dict)
    peer_returns: list[dict[str, Any]] = field(default_factory=list)
    contract_violations: list[str] = field(default_factory=list)
    pipeline_status: str = "running"
    ux: UxValidationMetrics = field(default_factory=UxValidationMetrics)

    def record_agent(
        self,
        name: str,
        *,
        execution_time_ms: float = 0.0,
        status: str = "success",
        peer_ok: bool = True,
        peer_returns: int = 0,
    ) -> None:
        if name not in self.active_agents:
            self.active_agents.append(name)
        self.agent_metrics[name] = AgentMetrics(
            name=name,
            execution_time_ms=execution_time_ms,
            status=status,
            peer_validation_passed=peer_ok,
            peer_returns=peer_returns,
        )

    def record_peer_return(self, entry: dict[str, Any]) -> None:
        self.peer_returns.append(entry)

    def record_cancel(self, checkpoint: str) -> None:
        self.ux.cancel_events += 1
        self.ux.last_checkpoint = checkpoint
        self.ux.last_cancel_at = time.time()

    def record_restart(self, checkpoint: str) -> None:
        self.ux.restart_events += 1
        self.ux.last_checkpoint = checkpoint

    def to_summary(self) -> dict[str, Any]:
        elapsed = (time.perf_counter() - self.started_at) * 1000
        agents = list(self.agent_metrics.values())
        avg_ms = sum(a.execution_time_ms for a in agents) / max(1, len(agents))
        peer_pass = sum(1 for a in agents if a.peer_validation_passed)
        total_retries = sum(a.retry_count for a in agents)
        return {
            "ai_core_version": AI_CORE_VERSION,
            "architecture": "Streaming Peer Validation Pipeline",
            "task_id": self.task_id,
            "pipeline_status": self.pipeline_status,
            "total_execution_time_ms": round(elapsed, 1),
            "active_agent_count": len(self.active_agents),
            "active_agents": self.active_agents,
            "peer_validation": {
                "total_returns": len(self.peer_returns),
                "agents_passed": peer_pass,
                "agents_total": len(agents),
                "success_rate": round(peer_pass / max(1, len(agents)), 4),
            },
            "agent_utilization": {
                a.name: {
                    "execution_time_ms": a.execution_time_ms,
                    "status": a.status,
                    "peer_returns": a.peer_returns,
                }
                for a in agents
            },
            "retry_count_total": total_retries,
            "avg_agent_time_ms": round(avg_ms, 1),
            "contract_violations": self.contract_violations,
            "agents": [a.to_dict() for a in agents],
            "peer_returns": self.peer_returns,
            "ux_validation": self.ux.to_dict(),
        }


def merge_ux_event(
    task_id: str,
    *,
    event: str,
    checkpoint: str = "",
    app_dir: Path | None = None,
) -> None:
    """Append cancel/restart UX event to existing architecture_validation.json."""
    base = app_dir or _APP_DIR
    path = base / "output" / "diagnostics" / task_id / "architecture_validation.json"
    payload: dict[str, Any] = {}
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
    ux = payload.get("ux_validation") or UxValidationMetrics().to_dict()
    if event == "cancel":
        ux["cancel_events"] = int(ux.get("cancel_events") or 0) + 1
        ux["last_cancel_at"] = time.time()
    elif event == "restart":
        ux["restart_events"] = int(ux.get("restart_events") or 0) + 1
    if checkpoint:
        ux["last_checkpoint"] = checkpoint
    passed = sum(1 for key in UX_CHECKLIST if ux.get(key) is True)
    ux["checks_passed"] = passed
    ux["checks_total"] = len(UX_CHECKLIST)
    ux["passed"] = passed >= len(UX_CHECKLIST)
    payload["ux_validation"] = ux
    payload["ai_core_version"] = AI_CORE_VERSION
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_architecture_validation(
    task_id: str,
    metrics: ArchitectureMetrics,
    *,
    app_dir: Path | None = None,
) -> Path:
    base = app_dir or _APP_DIR
    payload = metrics.to_summary()
    diag = base / "output" / "diagnostics" / task_id
    diag.mkdir(parents=True, exist_ok=True)
    path = diag / "architecture_validation.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        from engines.open_ddf import open_ddf

        open_ddf.record_agent(
            task_id,
            "ArchitectureValidation/4.3",
            called=True,
            success=metrics.pipeline_status != "failed",
            output_metrics={
                "peer_returns": len(metrics.peer_returns),
                "active_agents": len(metrics.active_agents),
                "contract_violations": len(metrics.contract_violations),
                "ux_validation_passed": metrics.ux.checks_passed()
                >= len(UX_CHECKLIST),
            },
        )
        open_ddf.save(task_id)
    except Exception:
        pass

    return path


def pipeline_checkpoint(info: dict[str, Any]) -> str:
    """Last safe resume point for UX restart without full redo."""
    if info.get("mix_agent_path") or info.get("mix_ok"):
        return "post_mix"
    if info.get("voice_agent_path") or info.get("tts_segments_done"):
        return "post_voice"
    if info.get("quality_agent_path"):
        return "post_ai_core_text"
    if info.get("translation_agent_path"):
        return "post_translation"
    if info.get("source_segments"):
        return "post_stt"
    return "start"
