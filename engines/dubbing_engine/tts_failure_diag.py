"""
TTS Failure Diagnostics — Error Diagnostics v1.0 + Stage 3A.2.

No generic errors: every TTS failure carries full segment context.
Forbidden user message: «Произошла ошибка. Попробуйте ещё раз.»
"""

from __future__ import annotations

import json
import logging
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.tts_failure")

STAGE_TTS = "TTS"

ENGINE_LABELS: dict[str, str] = {
    "edge-offline": "Edge-TTS",
    "edge_tts": "Edge-TTS",
    "xtts": "XTTS",
    "coqui": "Coqui TTS",
    "openai": "OpenAI TTS",
}


def engine_display_name(engine_id: str) -> str:
    eid = (engine_id or "edge-offline").strip()
    return ENGINE_LABELS.get(eid, eid.upper() if eid else "Edge-TTS")


@dataclass
class TTSFailureReport:
    segment_id: str
    segment_index: int
    current: int
    total: int
    original_text: str
    tts_text: str
    voice: str
    language: str
    tts_file_path: str
    error_code: str
    error_message: str
    traceback: str
    duration_ms: float
    engine_id: str = "edge-offline"
    task_id: str = ""
    timestamp_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    error_type: str = "VoiceGenerationError"
    stage: str = STAGE_TTS
    pipeline_state: str = "PARTIAL"
    reason: str = ""

    @property
    def timestamp_iso(self) -> str:
        return datetime.fromtimestamp(self.timestamp_ms / 1000.0, tz=timezone.utc).isoformat()

    @property
    def engine_label(self) -> str:
        return engine_display_name(self.engine_id)

    def to_dict(self) -> dict[str, Any]:
        reason = self.reason or self.error_message
        return {
            "error_type": self.error_type,
            "segment_id": self.segment_id,
            "segment_index": self.segment_index,
            "segment_number": self.current,
            "current": self.current,
            "total": self.total,
            "text": self.tts_text,
            "original_text": self.original_text,
            "tts_text": self.tts_text,
            "voice": self.voice,
            "engine": self.engine_label,
            "engine_id": self.engine_id,
            "language": self.language,
            "tts_file_path": self.tts_file_path,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "reason": reason,
            "traceback": self.traceback,
            "duration_ms": round(self.duration_ms, 2),
            "task_id": self.task_id,
            "timestamp_ms": self.timestamp_ms,
            "timestamp": self.timestamp_iso,
            "stage": self.stage,
            "pipeline_state": self.pipeline_state,
        }


class VoiceGenerationError(RuntimeError):
    """Structured TTS / voice engine failure (Error Diagnostics v1.0)."""

    def __init__(
        self,
        message: str,
        *,
        report: TTSFailureReport | None = None,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.report = report
        self.__cause__ = cause


# Backward-compatible alias
TTSGenerationError = VoiceGenerationError


_UI_TEMPLATES = {
    "ru": (
        "VoiceGenerationError · сегмент {current}/{total} · segment_id={segment_id} · "
        "engine={engine} · {reason}"
    ),
    "en": (
        "VoiceGenerationError · segment {current}/{total} · segment_id={segment_id} · "
        "engine={engine} · {reason}"
    ),
    "uk": (
        "VoiceGenerationError · сегмент {current}/{total} · segment_id={segment_id} · "
        "engine={engine} · {reason}"
    ),
}


def format_diagnostic_block(report: TTSFailureReport | dict[str, Any]) -> str:
    """Multi-line diagnostic block (TZ Error Diagnostics v1.0 example)."""
    data = report.to_dict() if isinstance(report, TTSFailureReport) else dict(report)
    text = (data.get("tts_text") or data.get("text") or "")[:500]
    return (
        f"{data.get('error_type', 'VoiceGenerationError')}\n"
        f"segment_id: {data.get('segment_id', '?')}\n"
        f"segment_number: {data.get('segment_number', data.get('current', '?'))}/{data.get('total', '?')}\n"
        f"engine: {data.get('engine', data.get('engine_id', '?'))}\n"
        f'text:\n"{text}"\n'
        f"reason:\n{data.get('reason', data.get('error_message', ''))}\n"
        f"stage:\n{data.get('stage', STAGE_TTS)}\n"
        f"pipeline_state:\n{data.get('pipeline_state', 'PARTIAL')}\n"
        f"timestamp:\n{data.get('timestamp', '')}"
    )


def format_ui_message(report: TTSFailureReport | dict[str, Any], lang: str = "ru") -> str:
    """Single-line UI message — must not be replaced by generic vmFriendlyError."""
    data = report.to_dict() if isinstance(report, TTSFailureReport) else dict(report)
    tpl = _UI_TEMPLATES.get(lang) or _UI_TEMPLATES["en"]
    return tpl.format(
        current=data.get("current", data.get("segment_index", 0) + 1),
        total=data.get("total", "?"),
        segment_id=data.get("segment_id", "?"),
        engine=data.get("engine", data.get("engine_id", "?")),
        reason=(data.get("reason") or data.get("error_message") or "")[:200],
    )


def is_tts_diagnostic_message(msg: str | None) -> bool:
    """True if message is a structured TTS diagnostic (must pass through to user)."""
    if not msg:
        return False
    m = str(msg)
    markers = (
        "VoiceGenerationError",
        "segment_id=",
        "TTS ошибка",
        "TTS error",
        "TTS помилка",
        "engine=Edge-TTS",
        "engine=XTTS",
    )
    return any(x in m for x in markers)


def _error_code(exc: BaseException) -> str:
    if isinstance(exc, TimeoutError):
        return "TTS_TIMEOUT"
    name = type(exc).__name__
    msg = str(exc).lower()
    if "empty" in msg and "audio" in msg:
        return "TTS_EMPTY_AUDIO"
    if "empty file" in msg:
        return "TTS_EMPTY_OUTPUT"
    if "edge" in msg or "communicate" in msg:
        return "EDGE_TTS"
    return name or "VoiceGenerationError"


def _human_reason(exc: BaseException) -> str:
    msg = str(exc).strip()
    low = msg.lower()
    if "empty file" in low or "empty audio" in low:
        return "TTS engine returned empty audio."
    if isinstance(exc, TimeoutError) or "timeout" in low:
        return "TTS engine exceeded time limit."
    return msg or "Voice generation failed."


def build_failure_report(
    exc: BaseException,
    *,
    segment_id: str,
    segment_index: int,
    current: int,
    total: int,
    original_text: str,
    tts_text: str,
    voice: str,
    language: str,
    tts_file_path: str | Path,
    duration_ms: float,
    task_id: str = "",
    engine_id: str = "edge-offline",
    pipeline_state: str = "PARTIAL",
) -> TTSFailureReport:
    return TTSFailureReport(
        segment_id=segment_id or "?",
        segment_index=segment_index,
        current=current,
        total=total,
        original_text=original_text or "",
        tts_text=tts_text or "",
        voice=voice or "",
        language=language or "",
        tts_file_path=str(tts_file_path),
        error_code=_error_code(exc),
        error_message=str(exc),
        reason=_human_reason(exc),
        traceback=traceback.format_exc(),
        duration_ms=duration_ms,
        engine_id=engine_id,
        task_id=task_id,
        pipeline_state=pipeline_state,
    )


def log_tts_failure(report: TTSFailureReport) -> None:
    """Write structured diagnostic log — full stack trace in log only."""
    block = format_diagnostic_block(report)
    logger.error("[TTS FAILURE]\n%s\n--- traceback ---\n%s", block, report.traceback.rstrip())


def save_failure_report(
    report: TTSFailureReport,
    *,
    session_dir: str | Path | None = None,
    task_id: str | None = None,
) -> Path | None:
    """Persist JSON report to session directory."""
    base = Path(session_dir) if session_dir else None
    if base is None and task_id:
        try:
            from engines.dub_task_state import AUTO_TASKS, STATE_LOCK

            with STATE_LOCK:
                task = AUTO_TASKS.get(task_id)
                if task:
                    sd = (task.get("info") or {}).get("session_dir")
                    if sd:
                        base = Path(str(sd))
        except ImportError:
            pass
    if base is None:
        return None
    base.mkdir(parents=True, exist_ok=True)
    fname = f"tts_failure_{report.segment_id[:8]}_{report.timestamp_ms}.json"
    path = base / fname
    payload = report.to_dict()
    payload["diagnostic_block"] = format_diagnostic_block(report)
    payload["ui_message_ru"] = format_ui_message(report, "ru")
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def mark_segment_tts_failed(segment: dict[str, Any], report: TTSFailureReport) -> None:
    """Annotate segment row for Studio highlight + pipeline continuation."""
    segment["file"] = None
    segment["tts_status"] = "failed"
    segment["tts_error"] = report.to_dict()
    segment["container_status"] = "red"
    if report.segment_id:
        segment["segment_id"] = report.segment_id


def build_pipeline_state_snapshot(task_id: str) -> dict[str, Any]:
    """Snapshot task state for error-report ZIP (pipeline_state.json)."""
    from engines.dubbing_engine.pipeline_failure_diag import (
        build_pipeline_state_snapshot as _full_snapshot,
    )

    return _full_snapshot(task_id)

