"""Monitoring Center — unified real-time observability hub (TZ #8 §1–§17).

Central system that aggregates live state from all platform layers:
Orchestrator, Pipeline Engine, LLM Dispatcher, Performance Monitor, Recovery,
AI Memory — without modifying any of them.

Public API (§16):
    monitor.get_pipeline()
    monitor.get_agents()
    monitor.get_resources()
    monitor.get_models()
    monitor.get_statistics()
    monitor.export_report()
    monitor.get_history()
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.analytics_db import AnalyticsDB, get_analytics_db
from core.bottleneck_analyzer import BottleneckAnalyzer, get_bottleneck_analyzer
from core.diagnostics import DiagnosticsCenter, get_diagnostics_center
from core.report_exporter import export_html, export_json, export_pdf, export_zip, save_report

logger = logging.getLogger("tubedub.monitoring_center")

PIPELINE_STAGES = (
    "whisper", "cleaner", "translator", "review",
    "timing", "voice", "mix", "export",
)

_STAGE_ALIASES = {
    "translation": "translator",
    "ai_adaptation": "review",
    "tts": "voice",
}


def monitoring_enabled() -> bool:
    return str(os.getenv("VM_MONITORING", "1")).strip().lower() not in (
        "0", "false", "no", "off",
    )


@dataclass
class QueueStats:
    name: str
    current_size: int = 0
    max_size: int = 0
    avg_wait_s: float = 0.0
    peak_load: int = 0
    dropped_tasks: int = 0
    retry_count: int = 0
    _wait_samples: deque = field(default_factory=lambda: deque(maxlen=64))

    def record(self, size: int, *, wait_s: float = 0.0) -> None:
        self.current_size = size
        if size > self.peak_load:
            self.peak_load = size
        if wait_s > 0:
            self._wait_samples.append(wait_s)
            self.avg_wait_s = sum(self._wait_samples) / len(self._wait_samples)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "current_size": self.current_size,
            "max_size": self.max_size,
            "avg_wait_s": round(self.avg_wait_s, 2),
            "peak_load": self.peak_load,
            "dropped_tasks": self.dropped_tasks,
            "retry_count": self.retry_count,
        }


class MonitoringCenter:
    """Central monitoring, analytics, and diagnostics (TZ #8)."""

    def __init__(
        self,
        *,
        app_dir: str | Path | None = None,
        analytics: AnalyticsDB | None = None,
    ) -> None:
        self.app_dir = Path(app_dir) if app_dir else Path.cwd()
        self.db = analytics or get_analytics_db(self.app_dir)
        self.diagnostics = get_diagnostics_center()
        self.bottleneck = get_bottleneck_analyzer()
        self._lock = threading.RLock()
        self._project_id = ""
        self._started_at = 0.0
        self._current_stage = ""
        self._current_chunk = -1
        self._chunks_total = 0
        self._chunks_done = 0
        self._segments_total = 0
        self._segments_done = 0
        self._queues: dict[str, QueueStats] = {
            s: QueueStats(name=s) for s in PIPELINE_STAGES
        }
        self._stage_times: dict[str, float] = defaultdict(float)
        self._timeline: deque[dict[str, Any]] = deque(maxlen=2000)
        self._agent_stats: dict[str, dict[str, Any]] = {}

    # ── Event recording (§8) ───────────────────────────────────────

    def set_project(
        self,
        project_id: str,
        *,
        segments_total: int = 0,
        chunks_total: int = 0,
    ) -> None:
        with self._lock:
            self._project_id = project_id
            self._started_at = time.time()
            self._segments_total = segments_total
            self._chunks_total = chunks_total
            self._chunks_done = 0
            self._segments_done = 0
        self.record_event(f"Project {project_id} started", event_type="start")

    def record_event(
        self,
        message: str,
        *,
        event_type: str = "info",
        stage: str = "",
        chunk_id: int = -1,
    ) -> None:
        at = time.time()
        entry = {
            "message": message,
            "event_type": event_type,
            "stage": stage,
            "chunk_id": chunk_id,
            "recorded_at": at,
            "time": time.strftime("%H:%M:%S", time.localtime(at)),
        }
        with self._lock:
            self._timeline.append(entry)
            if stage:
                self._current_stage = stage
            if chunk_id >= 0:
                self._current_chunk = chunk_id
        if self._project_id:
            try:
                self.db.add_timeline(
                    self._project_id, message,
                    event_type=event_type, stage=stage, chunk_id=chunk_id, recorded_at=at,
                )
            except Exception:
                pass

    def update_progress(
        self,
        *,
        chunks_done: int | None = None,
        segments_done: int | None = None,
        stage: str = "",
        chunk_id: int = -1,
    ) -> None:
        with self._lock:
            if chunks_done is not None:
                self._chunks_done = chunks_done
            if segments_done is not None:
                self._segments_done = segments_done
            if stage:
                self._current_stage = stage
            if chunk_id >= 0:
                self._current_chunk = chunk_id

    # ── Data collectors (read-only) ──────────────────────────────────

    def _collect_orchestrator(self) -> dict[str, Any]:
        try:
            from core.orchestrator import get_orchestrator

            orch = get_orchestrator()
            if orch is None:
                return {}
            return orch.get_status()
        except Exception:
            return {}

    def _collect_pipeline(self) -> dict[str, Any]:
        try:
            from core.pipeline_engine import get_pipeline_engine

            eng = get_pipeline_engine()
            if eng is None:
                return {}
            return eng.get_status()
        except Exception:
            return {}

    def _collect_llm(self) -> dict[str, Any]:
        try:
            from core.llm_dispatcher import get_dispatcher

            return get_dispatcher().get_status()
        except Exception:
            return {}

    def _collect_recovery(self) -> dict[str, Any]:
        try:
            from core.recovery_manager import get_recovery_manager

            return get_recovery_manager().get_status()
        except Exception:
            return {}

    def _collect_memory(self) -> dict[str, Any]:
        try:
            from core.ai_memory import get_memory

            return get_memory(self._project_id, app_dir=self.app_dir).get_status()
        except Exception:
            return {}

    def _collect_resources(self) -> dict[str, Any]:
        try:
            from core.performance_monitor import get_performance_monitor

            mon = get_performance_monitor()
            mon.sample()
            last = mon.last().to_dict()
            history = mon.history(limit=30)
            return {"current": last, "history": history, "averages": mon.averages()}
        except Exception:
            try:
                from core.resource_monitor import ResourceMonitor

                s = ResourceMonitor().sample()
                return {"current": s.to_dict(), "history": [], "averages": {}}
            except Exception:
                return {}

    def _collect_perf_optimizer(self) -> dict[str, Any]:
        try:
            from core.performance_optimizer import get_performance_optimizer, optimizer_enabled

            if not optimizer_enabled():
                return {}
            return get_performance_optimizer(app_dir=self.app_dir).get_status()
        except Exception:
            return {}

    def _merge_queue_stats(self, pipeline_status: dict[str, Any]) -> None:
        engine = pipeline_status.get("engine") or {}
        metrics = engine.get("metrics") or {}
        planner = engine.get("planner") or {}
        stage_plans = planner.get("stages") or {}

        for stage_name, m in metrics.items():
            key = _STAGE_ALIASES.get(stage_name, stage_name)
            if key not in self._queues:
                self._queues[key] = QueueStats(name=key)
            qs = self._queues[key]
            depth = int(m.get("queue_depth") or 0)
            plan = stage_plans.get(stage_name) or stage_plans.get(key) or {}
            qs.max_size = int(plan.get("queue_size") or qs.max_size or 64)
            wait_s = float(m.get("wait_ms") or 0) / 1000.0
            qs.record(depth, wait_s=wait_s)
            qs.retry_count = int(m.get("errors") or qs.retry_count)
            self._stage_times[key] += float(m.get("busy_ms") or 0) / 1000.0

        recovery = pipeline_status.get("recovery") or {}
        parking = int(recovery.get("parking_count") or 0)
        if parking:
            self._queues.setdefault("parking", QueueStats(name="parking"))
            self._queues["parking"].current_size = parking

    # ── Public API (§16) ─────────────────────────────────────────────

    def get_dashboard(self, *, developer: bool = False) -> dict[str, Any]:
        """Live dashboard data (§2)."""
        orch = self._collect_orchestrator()
        pipeline = self._collect_pipeline()
        self._merge_queue_stats(pipeline)

        progress = self._progress_percent()
        eta = self._estimate_eta()
        speed = self._processing_speed(pipeline)

        agents_active, agents_idle = self._agent_counts(orch)

        dash = {
            "project_id": self._project_id,
            "current_stage": self._current_stage,
            "current_chunk": self._current_chunk,
            "progress_percent": progress,
            "eta_seconds": eta,
            "processing_speed": speed,
            "active_agents": agents_active,
            "idle_agents": agents_idle,
            "chunks_done": self._chunks_done,
            "chunks_total": self._chunks_total,
            "segments_done": self._segments_done,
            "segments_total": self._segments_total,
            "running": bool((pipeline.get("engine") or {}).get("running")),
        }

        if developer:
            dash["orchestrator"] = orch
            dash["pipeline"] = pipeline
            dash["recovery"] = self._collect_recovery()
            dash["memory"] = self._collect_memory()
            dash["optimizer"] = self._collect_perf_optimizer()
            dash["timeline"] = list(self._timeline)[-100:]
        else:
            # User mode (§15) — only progress, ETA, warnings.
            diag = self.diagnostics.run_full_scan(self._full_state())
            dash["warnings"] = [
                i.to_dict() for i in diag.issues
                if i.severity in ("warning", "critical")
            ][:5]
            dash["recommendations"] = [
                r.get("action", "") for r in (self.get_bottleneck().get("recommendations") or [])
            ][:3]

        return dash

    def get_pipeline(self) -> dict[str, Any]:
        """Pipeline visualization with per-stage stats (§3)."""
        pipeline = self._collect_pipeline()
        self._merge_queue_stats(pipeline)
        engine = pipeline.get("engine") or {}
        metrics = engine.get("metrics") or {}
        chunk_summary = engine.get("chunk_summary") or {}

        stages: list[dict[str, Any]] = []
        for stage in PIPELINE_STAGES:
            m = metrics.get(stage) or metrics.get(_STAGE_ALIASES.get(stage, "")) or {}
            qs = self._queues.get(stage, QueueStats(name=stage))
            busy_ms = float(m.get("busy_ms") or 0)
            processed = int(m.get("processed") or 0)
            stages.append({
                "stage": stage,
                "waiting": qs.current_size,
                "running": 1 if m.get("utilization", 0) > 0.1 else 0,
                "avg_time_s": round(busy_ms / max(1, processed) / 1000.0, 2),
                "speed": round(processed / max(0.001, busy_ms / 1000.0), 3),
                "errors": int(m.get("errors") or 0),
                "load_percent": round(float(m.get("utilization") or 0) * 100, 1),
                "queue": qs.to_dict(),
            })

        return {
            "stages": stages,
            "chunk_summary": chunk_summary,
            "chunk_size": engine.get("chunk_size", 0),
            "running": engine.get("running", False),
            "paused": engine.get("paused", False),
            "errors": engine.get("errors") or [],
        }

    def get_agents(self) -> dict[str, Any]:
        """Agent monitor (§7)."""
        orch = self._collect_orchestrator()
        agents_raw = orch.get("agents") or {}
        agents: list[dict[str, Any]] = []

        for name, info in agents_raw.items():
            if not isinstance(info, dict):
                continue
            processed = int(info.get("chunks_processed") or info.get("processed") or 0)
            errors = int(info.get("errors") or 0)
            total = processed + errors
            success_rate = (processed / total * 100.0) if total else 100.0
            agents.append({
                "name": name,
                "state": info.get("state", "unknown"),
                "current_task": info.get("current_chunk_id", info.get("task", "")),
                "chunks_processed": processed,
                "avg_time_s": round(float(info.get("avg_duration_ms") or 0) / 1000.0, 2),
                "errors": errors,
                "success_rate": round(success_rate, 1),
            })
            self._agent_stats[name] = agents[-1]

        # Pipeline engine stage workers as agents too.
        pipeline = self._collect_pipeline()
        metrics = (pipeline.get("engine") or {}).get("metrics") or {}
        for stage, m in metrics.items():
            key = _STAGE_ALIASES.get(stage, stage)
            if any(a["name"] == key for a in agents):
                continue
            processed = int(m.get("processed") or 0)
            errors = int(m.get("errors") or 0)
            total = processed + errors
            agents.append({
                "name": key,
                "state": "working" if m.get("utilization", 0) > 0.1 else "idle",
                "current_task": "",
                "chunks_processed": processed,
                "avg_time_s": round(float(m.get("busy_ms") or 0) / max(1, processed) / 1000.0, 2),
                "errors": errors,
                "success_rate": round((processed / total * 100) if total else 100.0, 1),
            })

        active = [a for a in agents if a["state"] in ("working", "busy", "running")]
        idle = [a for a in agents if a["state"] in ("idle", "ready", "paused")]
        return {"agents": agents, "active": active, "idle": idle}

    def get_resources(self) -> dict[str, Any]:
        """Resource monitor (§5)."""
        res = self._collect_resources()
        current = res.get("current") or {}
        hw = {}
        try:
            from core.hardware_profiler import get_hardware_profile

            hw = get_hardware_profile().to_dict()
        except Exception:
            pass

        cpu = hw.get("cpu") or {}
        gpu = hw.get("gpu") or {}
        mem = hw.get("memory") or {}
        disk = hw.get("disk") or {}

        return {
            "cpu": {
                "percent": current.get("cpu_percent", 0),
                "temperature_c": current.get("cpu_temp_c", 0),
                "frequency_mhz": cpu.get("frequency_mhz", 0),
                "cores": cpu.get("logical_cores", 0),
            },
            "gpu": {
                "percent": current.get("gpu_percent", 0),
                "vram_percent": current.get("vram_percent", 0),
                "vram_used_gb": current.get("vram_used_gb", 0),
                "vram_total_gb": current.get("vram_total_gb", 0),
                "temperature_c": current.get("gpu_temp_c", 0),
                "model": gpu.get("model", ""),
                "available": current.get("gpu_available", False),
            },
            "ram": {
                "percent": current.get("ram_percent", 0),
                "used_gb": current.get("ram_used_gb", 0),
                "total_gb": current.get("ram_total_gb", mem.get("total_gb", 0)),
                "free_gb": round(
                    float(current.get("ram_total_gb", 0)) - float(current.get("ram_used_gb", 0)), 2
                ),
            },
            "disk": {
                "kind": disk.get("kind", "unknown"),
                "free_gb": disk.get("free_gb", 0),
                "total_gb": disk.get("total_gb", 0),
            },
            "network": self._network_status(),
            "history": res.get("history") or [],
            "averages": res.get("averages") or {},
        }

    def get_models(self) -> dict[str, Any]:
        """LLM monitor (§6)."""
        llm = self._collect_llm()
        models: list[dict[str, Any]] = []
        active = llm.get("active_model") or ""

        for name, info in (llm.get("models") or {}).items():
            if not isinstance(info, dict):
                continue
            stats = info.get("stats") or {}
            requests = int(stats.get("requests") or 0)
            errors = int(stats.get("errors") or 0)
            timeouts = int(stats.get("timeouts") or 0)
            avg_ms = float(stats.get("avg_latency_ms") or 0)
            status = str(info.get("status") or "unknown")
            in_use = name == active or status == "busy"
            models.append({
                "name": name,
                "status": status,
                "in_use": in_use,
                "free": not in_use and status not in ("offline", "stalled"),
                "avg_latency_ms": round(avg_ms, 1),
                "requests": requests,
                "errors": errors,
                "timeouts": timeouts,
                "avg_tokens": int(stats.get("avg_tokens") or 0),
                "success_rate": round(stats.get("success_rate", 1.0) * 100, 1),
                "provider": info.get("provider", ""),
                "tier": info.get("tier", ""),
            })

        return {
            "active_model": active,
            "failover_chain": llm.get("failover_chain") or [],
            "models": models,
        }

    def get_queues(self) -> dict[str, Any]:
        """Queue monitor (§4)."""
        pipeline = self._collect_pipeline()
        self._merge_queue_stats(pipeline)
        orch = self._collect_orchestrator()
        orch_queues = orch.get("queues") or {}

        queues = []
        for name, qs in self._queues.items():
            d = qs.to_dict()
            if name in orch_queues:
                d["current_size"] = max(d["current_size"], int(orch_queues[name]))
            queues.append(d)
        return {"queues": queues}

    def get_statistics(self) -> dict[str, Any]:
        """Aggregate statistics."""
        pipeline = self._collect_pipeline()
        engine = pipeline.get("engine") or {}
        recovery = self._collect_recovery()
        llm = self._collect_llm()
        resources = self._collect_resources()

        total_errors = len(engine.get("errors") or [])
        recovery_stats = recovery.get("stats") or {}
        total_retries = int(recovery_stats.get("total_retries") or recovery_stats.get("retries") or 0)

        return {
            "project_id": self._project_id,
            "uptime_s": round(time.time() - self._started_at, 1) if self._started_at else 0,
            "chunks_processed": self._chunks_done,
            "segments_processed": self._segments_done,
            "total_errors": total_errors,
            "total_retries": total_retries,
            "parking_count": int(recovery.get("parking_count") or 0),
            "llm_requests": sum(
                int((m.get("stats") or {}).get("requests") or 0)
                for m in (llm.get("models") or {}).values()
                if isinstance(m, dict)
            ),
            "processing_speed": self._processing_speed(pipeline),
            "resource_averages": resources.get("averages") or {},
        }

    def get_timeline(self, *, limit: int = 200) -> list[dict[str, Any]]:
        """Processing timeline (§8)."""
        if self._project_id:
            db_timeline = self.db.get_timeline(self._project_id, limit=limit)
            if db_timeline:
                return db_timeline
        with self._lock:
            return list(self._timeline)[-limit:]

    def get_bottleneck(self) -> dict[str, Any]:
        """Bottleneck analysis (§10)."""
        pipeline = self._collect_pipeline()
        metrics = (pipeline.get("engine") or {}).get("metrics") or {}
        queue_data = {q.name: q.to_dict() for q in self._queues.values()}
        report = self.bottleneck.analyze(metrics, queue_stats=queue_data)
        return report.to_dict()

    def get_diagnostics(self) -> dict[str, Any]:
        """Run diagnostics scan (§9)."""
        return self.diagnostics.run_full_scan(self._full_state()).to_dict()

    def get_history(self, *, limit: int = 50, project_id: str = "") -> list[dict[str, Any]]:
        """Project history (§12)."""
        return self.db.get_history(limit=limit, project_id=project_id or self._project_id)

    def get_developer_events(self) -> dict[str, Any]:
        """Developer mode: all subsystem events (§14)."""
        return {
            "orchestrator": self._collect_orchestrator(),
            "pipeline": self._collect_pipeline(),
            "llm_dispatcher": self._collect_llm(),
            "recovery": self._collect_recovery(),
            "memory": self._collect_memory(),
            "optimizer": self._collect_perf_optimizer(),
            "timeline": self.get_timeline(limit=100),
            "bottleneck": self.get_bottleneck(),
            "diagnostics": self.get_diagnostics(),
        }

    def export_report(
        self,
        *,
        fmt: str = "zip",
        output_dir: str | Path | None = None,
    ) -> dict[str, Any]:
        """Export diagnostic report (§13)."""
        data = self._build_full_report()
        out_dir = Path(output_dir) if output_dir else self.app_dir / "data" / "reports"
        path = save_report(data, out_dir, fmt=fmt, title=f"report_{self._project_id or 'system'}")
        return {"ok": True, "path": str(path), "format": fmt}

    def export_report_bytes(self, *, fmt: str = "json") -> bytes:
        data = self._build_full_report()
        if fmt == "html":
            return export_html(data)
        if fmt == "pdf":
            return export_pdf(data)
        if fmt == "zip":
            return export_zip(data)
        return export_json(data)

    def finalize_project(
        self,
        project_id: str,
        *,
        errors: list[str] | None = None,
        duration_s: float = 0.0,
        models: list[str] | None = None,
        speed: float = 0.0,
        status: str = "completed",
    ) -> dict[str, Any]:
        """Self-diagnosis after project completion (§17)."""
        self._project_id = project_id
        bottleneck = self.get_bottleneck()
        diag_report = self.diagnostics.run_full_scan(self._full_state())
        ai_report = self.diagnostics.build_ai_report(bottleneck, diag_report)

        # Forward to Performance Optimizer (read-only public API).
        try:
            from core.bottleneck_analyzer import BottleneckReport, StageBottleneck

            br = BottleneckReport(
                primary=bottleneck.get("primary", ""),
                primary_percent=float(bottleneck.get("primary_percent") or 0),
                recommendations=bottleneck.get("recommendations") or [],
            )
            for s in bottleneck.get("stages") or []:
                br.stages.append(StageBottleneck(
                    stage=s.get("stage", ""),
                    label=s.get("label", ""),
                    duration_s=float(s.get("duration_s") or 0),
                    percent=float(s.get("percent") or 0),
                ))
            self.bottleneck.apply_to_optimizer(br)
        except Exception:
            pass

        export_result = self.export_report(fmt="zip")
        stats = self.get_statistics()
        run_id = self.db.save_run(
            project_id,
            started_at=self._started_at or time.time() - duration_s,
            finished_at=time.time(),
            duration_s=duration_s or stats.get("uptime_s", 0),
            models=models or self._active_models(),
            performance={
                "bottleneck": bottleneck,
                "statistics": stats,
                "resources": self.get_resources().get("averages"),
            },
            errors=errors or [],
            recommendations=ai_report.get("summary") or bottleneck.get("recommendations") or [],
            speed=speed or stats.get("processing_speed", 0),
            status=status,
            report_path=export_result.get("path", ""),
        )

        self.record_event(f"Project {project_id} completed — diagnostics saved", event_type="complete")
        return {
            "run_id": run_id,
            "ai_report": ai_report,
            "bottleneck": bottleneck,
            "diagnostics": diag_report.to_dict(),
            "report": export_result,
        }

    # ── Helpers ──────────────────────────────────────────────────────

    def _full_state(self) -> dict[str, Any]:
        pipeline = self._collect_pipeline()
        resources = self._collect_resources()
        return {
            "agents": self._collect_orchestrator(),
            "recovery": self._collect_recovery(),
            "queues": {q.name: q.to_dict() for q in self._queues.values()},
            "resource_history": resources.get("history") or [],
            "statistics": self.get_statistics(),
            "pipeline_running": bool((pipeline.get("engine") or {}).get("running")),
        }

    def _build_full_report(self) -> dict[str, Any]:
        bottleneck = self.get_bottleneck()
        diag = self.get_diagnostics()
        ai_report = self.diagnostics.build_ai_report(bottleneck, self.diagnostics.run_full_scan(self._full_state()))
        return {
            "project_id": self._project_id,
            "generated_at": time.time(),
            "dashboard": self.get_dashboard(developer=True),
            "pipeline": self.get_pipeline(),
            "agents": self.get_agents(),
            "resources": self.get_resources(),
            "models": self.get_models(),
            "queues": self.get_queues(),
            "statistics": self.get_statistics(),
            "timeline": self.get_timeline(),
            "bottleneck": bottleneck,
            "diagnostics": diag,
            "ai_report": ai_report,
            "developer": self.get_developer_events(),
        }

    def _progress_percent(self) -> float:
        if self._segments_total > 0:
            return min(100.0, self._segments_done / self._segments_total * 100.0)
        if self._chunks_total > 0:
            return min(100.0, self._chunks_done / self._chunks_total * 100.0)
        return 0.0

    def _estimate_eta(self) -> float:
        speed = self._processing_speed(self._collect_pipeline())
        remaining = max(0, self._segments_total - self._segments_done)
        if speed > 0 and remaining > 0:
            return remaining / speed
        return 0.0

    def _processing_speed(self, pipeline: dict[str, Any]) -> float:
        try:
            from core.performance_monitor import get_performance_monitor

            return get_performance_monitor().last().processing_speed
        except Exception:
            pass
        metrics = (pipeline.get("engine") or {}).get("metrics") or {}
        total_processed = sum(int(m.get("processed") or 0) for m in metrics.values())
        total_busy = sum(float(m.get("busy_ms") or 0) for m in metrics.values()) / 1000.0
        return total_processed / max(0.001, total_busy)

    @staticmethod
    def _agent_counts(orch: dict[str, Any]) -> tuple[int, int]:
        agents = orch.get("agents") or {}
        active = idle = 0
        for info in agents.values():
            if not isinstance(info, dict):
                continue
            state = str(info.get("state") or "")
            if state in ("working", "busy", "running"):
                active += 1
            elif state in ("idle", "ready", "paused"):
                idle += 1
        return active, idle

    def _active_models(self) -> list[str]:
        llm = self._collect_llm()
        active = llm.get("active_model")
        models = [active] if active else []
        for name, info in (llm.get("models") or {}).items():
            if isinstance(info, dict) and (info.get("stats") or {}).get("requests"):
                if name not in models:
                    models.append(name)
        return models

    @staticmethod
    def _network_status() -> dict[str, Any]:
        return {
            "api_status": "ok",
            "latency_ms": 0,
            "speed_mbps": 0,
        }

    def get_status(self) -> dict[str, Any]:
        return {
            "enabled": monitoring_enabled(),
            "project_id": self._project_id,
            "dashboard": self.get_dashboard(developer=True),
            "pipeline": self.get_pipeline(),
            "bottleneck": self.get_bottleneck(),
            "diagnostics": self.get_diagnostics(),
        }


_monitor: MonitoringCenter | None = None
_monitor_lock = threading.Lock()


def get_monitor(*, app_dir: str | Path | None = None) -> MonitoringCenter:
    global _monitor
    if _monitor is None:
        with _monitor_lock:
            if _monitor is None:
                _monitor = MonitoringCenter(app_dir=app_dir)
    return _monitor


def reset_monitor() -> None:
    global _monitor
    with _monitor_lock:
        _monitor = None
