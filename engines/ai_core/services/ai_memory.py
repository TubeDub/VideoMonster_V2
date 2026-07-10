"""Standalone AI Memory service (TZ Stage 3) — not embedded inside agents."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.ai_core.ai_memory")

_LOCK = threading.RLock()
_SERVICES: dict[str, AIMemoryService] = {}


class AIMemoryService:
    """Per-run segment history: attempts, errors, shortenings, timings, scores."""

    def __init__(self, run_id: str) -> None:
        self.run_id = str(run_id or "")
        self._segments: dict[str, list[dict[str, Any]]] = {}

    def record(
        self,
        segment_index: int,
        *,
        agent: str = "",
        event: str = "",
        text_before: str = "",
        text_after: str = "",
        error: str = "",
        quality_score: float | None = None,
        timing_ms: int | None = None,
        timing_error_ms: int | None = None,
        decision: str = "",
        retry_count: int = 0,
        extra: dict[str, Any] | None = None,
    ) -> None:
        entry: dict[str, Any] = {
            "agent": agent,
            "event": event,
            "retry_count": retry_count,
        }
        if text_before:
            entry["text_before"] = text_before[:500]
        if text_after:
            entry["text_after"] = text_after[:500]
        if error:
            entry["error"] = error[:300]
        if quality_score is not None:
            entry["quality_score"] = round(float(quality_score), 3)
        if timing_ms is not None:
            entry["timing_ms"] = int(timing_ms)
        if timing_error_ms is not None:
            entry["timing_error_ms"] = int(timing_error_ms)
        if decision:
            entry["decision"] = decision
        if extra:
            entry.update(extra)

        key = str(segment_index)
        with _LOCK:
            hist = self._segments.setdefault(key, [])
            hist.append(entry)
            if len(hist) > 100:
                self._segments[key] = hist[-100:]

        try:
            from engines.ai_core.platform import get_bus

            get_bus(self.run_id).remember_segment(segment_index, entry)
        except Exception:
            pass

    def record_agent_run(
        self,
        agent_id: str,
        segments: list[dict[str, Any]],
        *,
        status: str = "success",
        ms: float = 0.0,
        retries: int = 0,
        quality_score: float | None = None,
    ) -> None:
        for seg in segments:
            idx = int(seg.get("index", seg.get("segment_index", -1)))
            if idx < 0:
                continue
            self.record(
                idx,
                agent=agent_id,
                event=f"agent_{status}",
                quality_score=quality_score or seg.get("quality_score"),
                timing_ms=seg.get("timing_slot_ms") or seg.get("slot_ms"),
                retry_count=retries,
                extra={"execution_ms": round(ms, 1)},
            )

    def get_segment_history(self, segment_index: int) -> list[dict[str, Any]]:
        with _LOCK:
            return list(self._segments.get(str(segment_index), []))

    def shorten_count(self, segment_index: int) -> int:
        return sum(
            1
            for e in self.get_segment_history(segment_index)
            if e.get("event") in ("shorten", "compact_translation", "intelligent_shorten")
        )

    def should_use_compact_strategy(self, segment_index: int, threshold: int = 2) -> bool:
        """If segment was shortened multiple times, prefer compact translation (Master Spec §3)."""
        return self.shorten_count(segment_index) >= threshold

    def snapshot(self) -> dict[str, Any]:
        with _LOCK:
            return {
                "run_id": self.run_id,
                "segment_count": len(self._segments),
                "total_entries": sum(len(v) for v in self._segments.values()),
                "segments": dict(self._segments),
            }

    def save(self, app_dir: Path | None = None) -> Path | None:
        root = app_dir or Path(__file__).resolve().parents[3]
        out = root / "output" / "diagnostics" / self.run_id / "ai_memory.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            out.write_text(
                json.dumps(self.snapshot(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return out
        except Exception as exc:
            logger.debug("ai_memory save failed: %s", exc)
            return None


def get_memory_service(run_id: str) -> AIMemoryService:
    rid = str(run_id or "").strip()
    with _LOCK:
        if rid not in _SERVICES:
            _SERVICES[rid] = AIMemoryService(rid)
        return _SERVICES[rid]


def reset_memory_service(run_id: str) -> None:
    with _LOCK:
        _SERVICES.pop(str(run_id or ""), None)


def load_memory_snapshot(run_id: str, app_dir: Path | None = None) -> dict[str, Any]:
    root = app_dir or Path(__file__).resolve().parents[3]
    path = root / "output" / "diagnostics" / str(run_id) / "ai_memory.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
