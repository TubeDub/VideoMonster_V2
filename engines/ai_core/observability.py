"""Agent observability collector (TZ Stage 16) — real metrics into diagnostics."""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.ai_core.observability")

_LOCK = threading.RLock()
_COLLECTORS: dict[str, ObservabilityCollector] = {}


def _hardware_snapshot() -> dict[str, Any]:
    out: dict[str, Any] = {"cpu": True, "gpu": False, "ram_mb": None}
    try:
        from engines.hardware_probe import probe_hardware

        hw = probe_hardware()
        out["gpu"] = bool(hw.get("cuda_available"))
        out["cuda_devices"] = hw.get("cuda_devices", 0)
        out["platform"] = hw.get("platform")
    except Exception:
        pass
    try:
        import psutil  # type: ignore[import-untyped]

        out["ram_mb"] = round(psutil.virtual_memory().used / (1024 * 1024), 1)
        out["cpu_percent"] = psutil.cpu_percent(interval=None)
    except Exception:
        pass
    return out


class ObservabilityCollector:
    """Per-run agent execution metrics."""

    def __init__(self, run_id: str) -> None:
        self.run_id = str(run_id or "")
        self._agents: dict[str, dict[str, Any]] = {}
        self._hardware = _hardware_snapshot()

    def record_agent(
        self,
        agent_id: str,
        *,
        status: str = "success",
        ms: float = 0.0,
        retries: int = 0,
        processed: int = 0,
        rejected: int = 0,
        quality_score: float | None = None,
        timing_error_ms: float | None = None,
        confidence: float | None = None,
        model: str = "",
    ) -> None:
        with _LOCK:
            row = self._agents.setdefault(
                agent_id,
                {
                    "agent": agent_id,
                    "runs": 0,
                    "total_ms": 0.0,
                    "retry_count": 0,
                    "processed_segments": 0,
                    "rejected_segments": 0,
                    "quality_scores": [],
                    "timing_errors": [],
                    "confidences": [],
                    "last_status": status,
                    "model": model,
                },
            )
            row["runs"] += 1
            row["total_ms"] += float(ms)
            row["retry_count"] += int(retries)
            row["processed_segments"] += int(processed)
            row["rejected_segments"] += int(rejected)
            row["last_status"] = status
            if model:
                row["model"] = model
            if quality_score is not None:
                row["quality_scores"].append(float(quality_score))
            if timing_error_ms is not None:
                row["timing_errors"].append(float(timing_error_ms))
            if confidence is not None:
                row["confidences"].append(float(confidence))

    def snapshot(self) -> dict[str, Any]:
        with _LOCK:
            agents_out: list[dict[str, Any]] = []
            for row in self._agents.values():
                qs = row.get("quality_scores") or []
                te = row.get("timing_errors") or []
                cf = row.get("confidences") or []
                runs = max(1, int(row.get("runs") or 1))
                agents_out.append(
                    {
                        **row,
                        "execution_time_ms": round(float(row.get("total_ms") or 0), 1),
                        "avg_ms": round(float(row.get("total_ms") or 0) / runs, 1),
                        "average_quality": round(sum(qs) / len(qs), 3) if qs else None,
                        "average_timing_error_ms": round(sum(te) / len(te), 1) if te else None,
                        "confidence": round(sum(cf) / len(cf), 3) if cf else None,
                    }
                )
            return {
                "run_id": self.run_id,
                "hardware": self._hardware,
                "agents": agents_out,
                "active_agent": agents_out[-1]["agent"] if agents_out else None,
            }

    def save(self, app_dir: Path | None = None) -> Path | None:
        root = app_dir or Path(__file__).resolve().parents[2]
        out = root / "output" / "diagnostics" / self.run_id / "observability.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            out.write_text(
                json.dumps(self.snapshot(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return out
        except Exception as exc:
            logger.debug("observability save failed: %s", exc)
            return None


def get_observability(run_id: str) -> ObservabilityCollector:
    rid = str(run_id or "").strip()
    with _LOCK:
        if rid not in _COLLECTORS:
            _COLLECTORS[rid] = ObservabilityCollector(rid)
        return _COLLECTORS[rid]


def reset_observability(run_id: str) -> None:
    with _LOCK:
        _COLLECTORS.pop(str(run_id or ""), None)


def record_agent_execution(
    run_id: str,
    agent_id: str,
    *,
    status: str = "success",
    ms: float = 0.0,
    retries: int = 0,
    segments: list[dict[str, Any]] | None = None,
    quality_score: float | None = None,
    model: str = "",
) -> None:
    """Convenience hook for orchestrator — lightweight, no heavy compute."""
    segs = segments or []
    processed = len(segs)
    rejected = sum(1 for s in segs if s.get("quality_passed") is False)
    get_observability(run_id).record_agent(
        agent_id,
        status=status,
        ms=ms,
        retries=retries,
        processed=processed,
        rejected=rejected,
        quality_score=quality_score,
        model=model,
    )


def load_observability(run_id: str, app_dir: Path | None = None) -> dict[str, Any]:
    root = app_dir or Path(__file__).resolve().parents[2]
    path = root / "output" / "diagnostics" / str(run_id) / "observability.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
