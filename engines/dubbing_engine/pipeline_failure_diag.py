"""
Pipeline Failure Diagnostics — Error Diagnostics v1.0 (full pipeline).

Every pipeline failure carries stage, error code, reason, and context.
Forbidden user message: «Произошла ошибка. Попробуйте ещё раз.» (without details).
"""

from __future__ import annotations

import json
import logging
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.pipeline_failure")

# ── Pipeline stages (TZ §2) ────────────────────────────────────────────────
STAGE_AUDIO_EXTRACTION = "Audio Extraction"
STAGE_SOURCE_SEPARATION = "Source Separation"
STAGE_STT = "STT"
STAGE_TRANSLATION = "Translation"
STAGE_PRONUNCIATION = "Pronunciation Normalizer"
STAGE_TTS = "TTS"
STAGE_CONFLICT_RESOLVER = "Conflict Resolver"
STAGE_TIMING = "Timing Engine"
STAGE_AUDIO_MIX = "Audio Mix"
STAGE_FFMPEG = "FFmpeg Render"

STEP_TO_STAGE: dict[str, str] = {
    "preparing": STAGE_AUDIO_EXTRACTION,
    "extract_audio": STAGE_AUDIO_EXTRACTION,
    "source_separation": STAGE_SOURCE_SEPARATION,
    "transcribe": STAGE_STT,
    "translate": STAGE_TRANSLATION,
    "translation": STAGE_TRANSLATION,
    "ai_core": STAGE_TRANSLATION,
    "translation_review": STAGE_TRANSLATION,
    "tts": STAGE_TTS,
    "timing": STAGE_TIMING,
    "dub": STAGE_FFMPEG,
}

ERROR_CODE_MAP: dict[str, str] = {
    "empty_stt": "STT_EMPTY",
    "pipeline_aborted": "PIPELINE_ABORTED",
    "translate_failed": "TRANSLATION_FAILED",
    "translate_not_prepared": "TRANSLATION_MODEL_MISSING",
    "long_processing": "TRANSLATION_TIMEOUT",
    "export_error": "AUDIO_EXPORT_FAILED",
    "timed_missing": "TIMED_AUDIO_MISSING",
    "contract_broken": "INTEGRITY_CONTRACT_BROKEN",
    "dub_missing": "OUTPUT_MISSING",
    "ffmpeg_missing": "FFMPEG_MISSING",
    "tts_failed": "TTS_FAILED",
    "segment_mismatch": "SEGMENT_MISMATCH",
}

_REASON_SHORT_RU: dict[str, str] = {
    "STT_EMPTY": "Распознавание речи не вернуло текст.",
    "TRANSLATION_FAILED": "Не удалось выполнить перевод.",
    "TRANSLATION_MODEL_MISSING": "Модель перевода не подготовлена.",
    "TRANSLATION_TIMEOUT": "Перевод занял слишком много времени.",
    "TTS_EMPTY_AUDIO": "Не удалось сгенерировать аудио.",
    "TTS_TIMEOUT": "Озвучка превысила лимит времени.",
    "TTS_FAILED": "Не удалось сгенерировать аудио.",
    "FFMPEG_EXTRACT_FAILED": "Не удалось извлечь аудио из видео.",
    "FFMPEG_NOT_FOUND": "FFmpeg не найден в PATH.",
    "NO_AUDIO_TRACK": "В видео нет аудиодорожки.",
    "AUDIO_WRITE_FAILED": "Не удалось записать файл: проверьте права.",
    "AUDIO_FILE_NOT_FOUND": "Исходный видеофайл не найден.",
    "FFMPEG_NOT_FOUND": "FFmpeg не найден в PATH.",
    "NO_AUDIO_TRACK": "В видео нет аудиодорожки.",
    "WRITE_FAILED": "Не удалось записать файл: проверьте права.",
    "FILE_NOT_FOUND": "Файл видео не найден.",
    "AUDIO_FILE_NOT_FOUND": "Файл видео не найден.",
    "AUDIO_WRITE_FAILED": "Не удалось записать файл: проверьте права.",
    "AUDIO_EXPORT_FAILED": "Не удалось экспортировать аудиодорожку.",
    "TIMED_AUDIO_MISSING": "Отсутствует синхронизированная аудиодорожка.",
    "INTEGRITY_CONTRACT_BROKEN": "Нарушена целостность данных пайплайна.",
    "OUTPUT_MISSING": "Итоговый файл не был создан.",
    "PIPELINE_ABORTED": "Пайплайн прерван.",
    "PIPELINE_CRITICAL": "Критическая ошибка пайплайна.",
    "SEGMENT_MISMATCH": "Не удалось согласовать сегменты с таймингом.",
    "STAGE_SNAPSHOT_INTEGRITY": "Недопустимое изменение данных сегмента.",
}


def _memory_mb() -> float:
    try:
        import psutil

        return round(psutil.virtual_memory().used / 1024**2, 1)
    except Exception:
        return -1.0


def _ffmpeg_version() -> str:
    try:
        from engines.ffmpeg_paths import find_ffmpeg
        import subprocess

        ff = find_ffmpeg()
        if not ff:
            return "not found"
        res = subprocess.run(
            [ff, "-version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        line = (res.stdout or res.stderr or "").splitlines()
        return line[0] if line else "unknown"
    except Exception:
        return "unknown"


@dataclass
class PipelineFailureReport:
    stage: str
    error_type: str
    error_code: str
    reason: str
    reason_short: str = ""
    traceback: str = ""
    timestamp_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    pipeline_state: str = "STOPPED"
    task_id: str = ""
    segment_id: str | None = None
    segment_number: str | None = None
    voice_engine: str | None = None
    voice: str | None = None
    language: str | None = None
    tts_text: str | None = None
    tts_file_path: str | None = None
    tts_rate: str | None = None
    duration_ms: float | None = None
    project_session_state: dict[str, Any] = field(default_factory=dict)

    @property
    def timestamp_iso(self) -> str:
        return datetime.fromtimestamp(self.timestamp_ms / 1000.0, tz=timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "error_type": self.error_type,
            "error_code": self.error_code,
            "reason": self.reason,
            "reason_short": self.reason_short or self.reason,
            "traceback": self.traceback,
            "timestamp": self.timestamp_iso,
            "timestamp_ms": self.timestamp_ms,
            "pipeline_state": self.pipeline_state,
            "task_id": self.task_id,
            "segment_id": self.segment_id,
            "segment_number": self.segment_number,
            "voice_engine": self.voice_engine,
            "voice": self.voice,
            "language": self.language,
            "tts_text": self.tts_text,
            "tts_file_path": self.tts_file_path,
            "tts_rate": self.tts_rate,
            "duration_ms": self.duration_ms,
            "project_session_state": self.project_session_state,
        }

    def user_summary(self) -> dict[str, Any]:
        """Structured payload for UI (TZ §4)."""
        out: dict[str, Any] = {
            "title": "Ошибка дубляжа",
            "stage": self.stage,
            "error_type": self.error_type,
            "error_code": self.error_code,
            "reason_short": self.reason_short or self.reason,
            "reason": self.reason,
            "pipeline_state": self.pipeline_state,
            "timestamp": self.timestamp_iso,
        }
        if self.segment_id:
            out["segment_id"] = self.segment_id
        if self.segment_number:
            out["segment"] = self.segment_number
        if self.voice_engine:
            out["voice_engine"] = self.voice_engine
        if self.voice:
            out["voice"] = self.voice
        out["detail_block"] = format_detail_block(self)
        return out


def infer_error_code(message: str, exc: BaseException | None = None) -> str:
    if exc is not None:
        from engines.dubbing_engine.tts_failure_diag import VoiceGenerationError

        if isinstance(exc, VoiceGenerationError) and exc.report:
            return exc.report.error_code
        if isinstance(exc, TimeoutError):
            return "TTS_TIMEOUT"
        name = type(exc).__name__
        if name in ("TranslationTimeoutError",):
            return "TRANSLATION_TIMEOUT"
    low = (message or "").lower()
    if "empty" in low and ("stt" in low or "распозна" in low or "speech" in low):
        return "STT_EMPTY"
    if "ffmpeg" in low and ("extract" in low or "extraction" in low):
        return "FFMPEG_EXTRACT_FAILED"
    if "translate" in low or "перевод" in low or "переклад" in low:
        return "TRANSLATION_FAILED"
    if "tts" in low or "voice" in low or "озвуч" in low:
        return "TTS_FAILED"
    if "timed" in low or "timing" in low:
        return "TIMED_AUDIO_MISSING"
    if "contract" in low or "integrity" in low:
        return "INTEGRITY_CONTRACT_BROKEN"
    if exc is not None:
        return type(exc).__name__.upper()
    return "PIPELINE_ERROR"


def infer_error_type(exc: BaseException | None, message: str) -> str:
    if exc is not None:
        from engines.dubbing_engine.tts_failure_diag import VoiceGenerationError
        from engines.pipeline_integrity.exceptions import StageSnapshotIntegrityError

        if isinstance(exc, StageSnapshotIntegrityError):
            return "StageSnapshotIntegrityError"
        if isinstance(exc, VoiceGenerationError):
            return "VoiceGenerationError"
        return type(exc).__name__
    if "StageSnapshotIntegrityError" in message:
        return "StageSnapshotIntegrityError"
    if "VoiceGenerationError" in message:
        return "VoiceGenerationError"
    return "PipelineError"


def reason_short_for_code(code: str, fallback: str = "") -> str:
    return _REASON_SHORT_RU.get(code, fallback or "Ошибка выполнения пайплайна.")


def build_failure_report(
    message: str,
    *,
    stage: str,
    task_id: str = "",
    exc: BaseException | None = None,
    error_code: str | None = None,
    error_type: str | None = None,
    segment_id: str | None = None,
    segment_number: str | None = None,
    voice_engine: str | None = None,
    voice: str | None = None,
    language: str | None = None,
    tts_text: str | None = None,
    tts_file_path: str | None = None,
    tts_rate: str | None = None,
    duration_ms: float | None = None,
    pipeline_state: str = "STOPPED",
) -> PipelineFailureReport:
    code = error_code or infer_error_code(message, exc)
    etype = error_type or infer_error_type(exc, message)
    tb = traceback.format_exc() if exc is not None else ""
    if exc and not tb.strip():
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    reason = str(exc).strip() if exc else message
    if not reason:
        reason = message
    return PipelineFailureReport(
        stage=stage,
        error_type=etype,
        error_code=code,
        reason=reason,
        reason_short=reason_short_for_code(code, message[:200]),
        traceback=tb,
        task_id=task_id,
        segment_id=segment_id,
        segment_number=segment_number,
        voice_engine=voice_engine,
        voice=voice,
        language=language,
        tts_text=tts_text,
        tts_file_path=tts_file_path,
        tts_rate=tts_rate,
        duration_ms=duration_ms,
        pipeline_state=pipeline_state,
    )


def from_tts_failure_report(tts_report, *, pipeline_state: str = "STOPPED") -> PipelineFailureReport:
    """Convert TTSFailureReport → PipelineFailureReport."""
    from engines.dubbing_engine.tts_failure_diag import TTSFailureReport, engine_display_name

    if isinstance(tts_report, dict):
        data = tts_report
    elif isinstance(tts_report, TTSFailureReport):
        data = tts_report.to_dict()
    else:
        data = {}

    seg_num = None
    cur = data.get("current") or data.get("segment_number")
    total = data.get("total")
    if cur and total:
        seg_num = f"{cur}/{total}"

    return PipelineFailureReport(
        stage=STAGE_TTS,
        error_type=data.get("error_type", "VoiceGenerationError"),
        error_code=data.get("error_code", "TTS_FAILED"),
        reason=data.get("reason") or data.get("error_message") or "Voice generation failed.",
        reason_short=reason_short_for_code(
            data.get("error_code", "TTS_FAILED"),
            "Не удалось сгенерировать аудио.",
        ),
        traceback=data.get("traceback") or "",
        task_id=data.get("task_id", ""),
        segment_id=data.get("segment_id"),
        segment_number=seg_num,
        voice_engine=data.get("engine") or engine_display_name(data.get("engine_id", "")),
        voice=data.get("voice"),
        language=data.get("language"),
        tts_text=data.get("tts_text") or data.get("text"),
        tts_file_path=data.get("tts_file_path"),
        tts_rate=data.get("tts_rate"),
        duration_ms=data.get("duration_ms"),
        pipeline_state=pipeline_state,
        timestamp_ms=int(data.get("timestamp_ms") or time.time() * 1000),
    )


def format_detail_block(report: PipelineFailureReport | dict[str, Any]) -> str:
    """Technical block for «Подробнее» and logs."""
    data = report.to_dict() if isinstance(report, PipelineFailureReport) else dict(report)
    lines = [
        f"Stage: {data.get('stage', '?')}",
    ]
    if data.get("segment_id"):
        lines.append(f"segment_id: {data['segment_id']}")
    if data.get("segment_number"):
        lines.append(f"segment_number: {data['segment_number']}")
    if data.get("voice_engine"):
        lines.append(f"engine: {data['voice_engine']}")
    if data.get("voice"):
        lines.append(f"voice: {data['voice']}")
    if data.get("language"):
        lines.append(f"language: {data['language']}")
    if data.get("tts_rate"):
        lines.append(f"rate: {data['tts_rate']}")
    if data.get("tts_text"):
        text = str(data["tts_text"])[:500]
        lines.extend([f'text:\n"{text}"'])
    lines.extend([
        f"Exception:\n{data.get('error_type', 'PipelineError')}",
        f"Error code:\n{data.get('error_code', '?')}",
        f"Reason:\n{data.get('reason', '')}",
    ])
    if data.get("tts_file_path"):
        lines.append(f"Output:\n{data['tts_file_path']}")
    if data.get("duration_ms") is not None:
        lines.append(f"duration_ms: {data['duration_ms']}")
    lines.extend([
        f"Pipeline:\n{data.get('pipeline_state', 'STOPPED')}",
        f"timestamp:\n{data.get('timestamp', '')}",
    ])
    return "\n".join(lines)


def format_ui_line(report: PipelineFailureReport | dict[str, Any]) -> str:
    """Single-line message that must bypass generic vmFriendlyError masking."""
    data = report.to_dict() if isinstance(report, PipelineFailureReport) else dict(report)
    parts = [
        f"DubbingError stage={data.get('stage', '?')}",
        f"error={data.get('error_type', '?')}",
        f"code={data.get('error_code', '?')}",
    ]
    if data.get("segment_id"):
        parts.append(f"segment_id={data['segment_id']}")
    parts.append(f"reason={data.get('reason_short') or data.get('reason', '')[:120]}")
    return " · ".join(parts)


def is_pipeline_diagnostic_message(msg: str | None) -> bool:
    if not msg:
        return False
    m = str(msg)
    markers = (
        "DubbingError stage=",
        "VoiceGenerationError",
        "segment_id=",
        "Ошибка дубляжа",
        "engine=",
    )
    return any(x in m for x in markers)


def log_pipeline_failure(report: PipelineFailureReport) -> None:
    block = format_detail_block(report)
    logger.error(
        "[PIPELINE FAILURE]\n%s\n--- stack trace ---\n%s",
        block,
        (report.traceback or "(no traceback)").rstrip(),
    )


def save_pipeline_failure(
    report: PipelineFailureReport,
    *,
    session_dir: str | Path | None = None,
    task_id: str | None = None,
) -> Path | None:
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
    fname = f"pipeline_failure_{report.error_code}_{report.timestamp_ms}.json"
    path = base / fname
    payload = report.to_dict()
    payload["diagnostic_block"] = format_detail_block(report)
    payload["ui_summary"] = report.user_summary()
    payload["ui_line"] = format_ui_line(report)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def build_project_session_snapshot(task_id: str) -> dict[str, Any]:
    try:
        from api.studio_api import build_session_from_auto_dub_task

        state = build_session_from_auto_dub_task(task_id)
        return state or {}
    except Exception as exc:
        logger.debug("ProjectSession snapshot skipped: %s", exc)
        return {"task_id": task_id, "error": str(exc)}


def build_pipeline_state_snapshot(task_id: str) -> dict[str, Any]:
    try:
        from engines.dub_task_state import AUTO_TASKS, STATE_LOCK

        with STATE_LOCK:
            task = AUTO_TASKS.get(task_id)
            if not task:
                return {"task_id": task_id, "found": False}
            info = dict(task.get("info") or {})
            return {
                "task_id": task_id,
                "found": True,
                "status": task.get("status"),
                "step": task.get("step"),
                "progress": task.get("progress"),
                "errors": list(task.get("errors") or []),
                "pipeline_error": info.get("pipeline_error"),
                "pipeline_failures": list(info.get("pipeline_failures") or []),
                "tts_failures": list(info.get("tts_failures") or []),
                "runtime_diagnostics": list(info.get("runtime_diagnostics") or []),
                "pipeline_integrity": info.get("pipeline_integrity"),
                "session_dir": info.get("session_dir"),
                "voice": info.get("voice") or info.get("tts_voice"),
                "target_lang": info.get("target_lang"),
                "segments_count": len(info.get("segments_data") or []),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
    except ImportError:
        return {"task_id": task_id, "found": False}


def build_task_config_snapshot(task_id: str) -> dict[str, Any]:
    try:
        from engines.dub_task_state import AUTO_TASKS, STATE_LOCK

        with STATE_LOCK:
            task = AUTO_TASKS.get(task_id)
            if not task:
                return {"task_id": task_id, "found": False}
            info = task.get("info") or {}
            keys = (
                "target_lang",
                "source_lang",
                "voice",
                "tts_voice",
                "tts_engine_id",
                "model_size",
                "dub_style",
                "segmentation_mode",
                "tts_rate",
                "tts_pitch",
                "content_mode",
                "ocr_enabled",
                "keep_original_track",
            )
            return {"task_id": task_id, **{k: info.get(k) for k in keys if k in info}}
    except ImportError:
        return {"task_id": task_id, "found": False}


def build_engine_info_snapshot(app_dir: Path | None = None) -> dict[str, Any]:
    from engines.app_version import version_info

    info = version_info()
    info["python"] = sys.version
    info["platform"] = __import__("platform").platform()
    info["ffmpeg"] = _ffmpeg_version()
    if app_dir:
        try:
            from engines.model_cache import cache_status

            cs = cache_status(app_dir)
            info["installed_models"] = cs.get("models") or []
            info["model_count"] = cs.get("model_count", 0)
        except Exception:
            info["installed_models"] = []
    return info


def fail_pipeline(
    task_id: str,
    message: str,
    *,
    stage: str | None = None,
    exc: BaseException | None = None,
    error_code: str | None = None,
    report: PipelineFailureReport | None = None,
    ui_lang: str = "ru",
    editing_pause: bool = False,
) -> str:
    """
    Fail-fast: stop pipeline, persist diagnostics, snapshot ProjectSession.
    Returns UI summary line (never generic-only message).
    """
    from engines.dub_task_state import AUTO_TASK_CONTROLS, AUTO_TASKS, STATE_LOCK
    from engines.pipeline_integrity.exceptions import StageSnapshotIntegrityError

    dev_payload: dict[str, Any] | None = None
    openddf_arts: dict[str, str] | None = None

    if report is None:
        resolved_stage = stage or STAGE_AUDIO_EXTRACTION
        with STATE_LOCK:
            task = AUTO_TASKS.get(task_id)
            if task:
                step = task.get("step") or ""
                resolved_stage = stage or STEP_TO_STAGE.get(step, resolved_stage)
                info = task.get("info") or {}
                voice = info.get("voice") or info.get("tts_voice")
                engine_id = info.get("tts_engine_id") or "edge-offline"
                from engines.dubbing_engine.tts_failure_diag import engine_display_name

                voice_engine = engine_display_name(str(engine_id))
            else:
                voice = None
                voice_engine = None
        report = build_failure_report(
            message,
            stage=resolved_stage,
            task_id=task_id,
            exc=exc,
            error_code=error_code,
            voice=voice,
            voice_engine=voice_engine,
        )

        if isinstance(exc, StageSnapshotIntegrityError):
            from engines.pipeline_integrity.openddf_diagnostics import (
                developer_block_from_exc,
                developer_payload_from_exc,
                release_summary_from_exc,
            )

            report.error_type = "StageSnapshotIntegrityError"
            report.error_code = error_code or "STAGE_SNAPSHOT_INTEGRITY"
            report.stage = exc.stage or resolved_stage
            report.segment_id = exc.segment_id or None
            report.reason = str(exc)
            release = release_summary_from_exc(exc)
            report.reason_short = release.get("reason_short") or exc.format_user_reason()
            if exc.segment_id and exc.field:
                report.segment_number = exc.segment_id
            ui_summary = release
            detail = developer_block_from_exc(exc)
            dev_payload = developer_payload_from_exc(exc)
            openddf_arts = ((exc.details or {}).get("openddf") or {}).get("artifacts")
        else:
            ui_summary = report.user_summary()
            detail = format_detail_block(report)
            dev_payload = None
            openddf_arts = None
            if exc is not None and hasattr(exc, "format_diagnostic_block"):
                try:
                    detail = exc.format_diagnostic_block()
                except Exception:
                    pass
    else:
        ui_summary = report.user_summary()
        detail = format_detail_block(report)
        dev_payload = None
        openddf_arts = None

    session_snap = build_project_session_snapshot(task_id)
    report.project_session_state = {"status": session_snap.get("task_status"), "segments": len(session_snap.get("segments") or [])}

    log_pipeline_failure(report)
    save_pipeline_failure(report, task_id=task_id)

    # P16.7 — enriched error envelope (Run ID / UUID / Recovery / ZIP)
    try:
        from engines.production_hardening.enriched_logging import (
            build_error_record,
            format_error_log_line,
        )

        with STATE_LOCK:
            _task = AUTO_TASKS.get(task_id) or {}
            _info = dict(_task.get("info") or {})
        _zip = ""
        if isinstance(exc, Exception) and getattr(exc, "details", None):
            _zip = str((exc.details or {}).get("diagnostic_zip") or "")
        _rec = build_error_record(
            run_id=str(_info.get("openddf_run_id") or task_id),
            stage=str(report.stage or stage or ""),
            message=str(message),
            exc=exc,
            segment_uuid=str(getattr(report, "segment_id", "") or ""),
            segment_id=str(getattr(report, "segment_id", "") or ""),
            error_code=str(error_code or getattr(report, "error_code", "") or ""),
            diagnostic_zip=_zip,
        )
        logger.error("%s", format_error_log_line(_rec))
        with STATE_LOCK:
            if task_id in AUTO_TASKS:
                AUTO_TASKS[task_id].setdefault("info", {})["p16_error_record"] = _rec
    except Exception:
        pass

    meta = None
    try:
        from engines.pipeline_integrity.passive_openddf import (
            capture_pipeline_exception,
            publish_task_diagnostic,
        )

        with STATE_LOCK:
            task = AUTO_TASKS.get(task_id)
            info = dict((task or {}).get("info") or {})
        if exc is not None and not isinstance(exc, StageSnapshotIntegrityError):
            arts = capture_pipeline_exception(
                task_id,
                exc,
                stage=report.stage or stage or STAGE_AUDIO_EXTRACTION,
                task_info=info,
            )
            if arts and not openddf_arts:
                openddf_arts = arts
        elif exc is None and (error_code or report.error_code):
            arts = capture_pipeline_exception(
                task_id,
                RuntimeError(str(message)),
                stage=report.stage or stage or STAGE_AUDIO_EXTRACTION,
                task_info=info,
            )
            if arts and not openddf_arts:
                openddf_arts = arts
        meta = publish_task_diagnostic(
            task_id,
            task_info=info,
            artifacts=openddf_arts,
        )
        if meta:
            with STATE_LOCK:
                if task_id in AUTO_TASKS:
                    info_block = AUTO_TASKS[task_id].setdefault("info", {})
                    info_block["passive_openddf"] = meta
                    info_block["openddf_run_id"] = meta.get("run_id")
                    if meta.get("diagnostic_zip"):
                        merged = dict(info_block.get("openddf_artifacts") or {})
                        merged["diagnostic_zip"] = meta["diagnostic_zip"]
                        info_block["openddf_artifacts"] = merged
    except ImportError:
        pass

    ui_line = format_ui_line(report)
    if isinstance(exc, StageSnapshotIntegrityError):
        ui_line = (
            f"DubbingError stage={report.stage} · error=StageSnapshotIntegrityError · "
            f"code=STAGE_SNAPSHOT_INTEGRITY · reason={report.reason_short[:120]}"
        )

    with STATE_LOCK:
        control = AUTO_TASK_CONTROLS.get(task_id)
        if control and control.get("editing") is True and editing_pause:
            if task_id in AUTO_TASKS:
                info = AUTO_TASKS[task_id].setdefault("info", {})
                info["pipeline_error"] = ui_summary
                info["last_pipeline_diagnostic"] = detail
                if dev_payload is not None:
                    info["pipeline_error_developer"] = dev_payload
                if openddf_arts:
                    info["openddf_artifacts"] = openddf_arts
                if meta:
                    info["passive_openddf"] = meta
                    info["openddf_run_id"] = meta.get("run_id")
                AUTO_TASKS[task_id]["errors"] = [ui_line]
            control["editor_error"] = True
            control["state"] = "paused"
            return ui_line

        if task_id in AUTO_TASKS:
            info = AUTO_TASKS[task_id].setdefault("info", {})
            failures = info.setdefault("pipeline_failures", [])
            payload = report.to_dict()
            payload["diagnostic_block"] = detail
            payload["ui_summary"] = ui_summary
            failures.append(payload)
            info["pipeline_error"] = ui_summary
            info["last_pipeline_diagnostic"] = detail
            if dev_payload is not None:
                info["pipeline_error_developer"] = dev_payload
            if openddf_arts:
                info["openddf_artifacts"] = openddf_arts
            if meta:
                info["passive_openddf"] = meta
                info["openddf_run_id"] = meta.get("run_id")
            info["last_tts_error"] = ui_line
            info["last_tts_diagnostic"] = detail
            errs = AUTO_TASKS[task_id].setdefault("errors", [])
            if ui_line not in errs:
                errs.append(ui_line)
            AUTO_TASKS[task_id].update({"status": "error", "errors": errs})
            try:
                from api.auto_dub_api import _update_progress_detail

                _update_progress_detail(
                    task_id,
                    last_tts_error=ui_line,
                    pipeline_error=ui_summary,
                )
            except ImportError:
                pass

        if control:
            AUTO_TASK_CONTROLS.pop(task_id, None)

    try:
        from api.studio_api import _save_session

        if session_snap:
            session_snap["task_status"] = "pipeline_failed"
            session_snap["pipeline_error"] = ui_summary
            _save_session(session_snap)
    except Exception as snap_err:
        logger.warning("Task %s: pipeline failure session snapshot skipped: %s", task_id, snap_err)

    return ui_line


class RuntimeDiagnosticsRecorder:
    """TZ §7 — per-stage runtime diagnostics."""

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        self._starts: dict[str, float] = {}

    def stage_begin(self, stage_name: str) -> None:
        self._starts[stage_name] = time.time()
        try:
            from engines.pipeline_integrity.passive_openddf import observe_stage_begin

            observe_stage_begin(self.task_id, stage_name)
        except ImportError:
            pass

    def stage_complete(
        self,
        stage_num: int,
        stage_name: str,
        *,
        segments_total: int = 0,
        segments_ok: int = 0,
        errors: int = 0,
        voice_engine: str = "",
        integrity_guard: str = "ok",
    ) -> dict[str, Any]:
        started = self._starts.pop(stage_name, None)
        duration_ms = round((time.time() - started) * 1000, 1) if started else 0.0
        record = {
            "stage_num": stage_num,
            "stage": stage_name,
            "duration_ms": duration_ms,
            "segments_total": segments_total,
            "segments_ok": segments_ok,
            "errors": errors,
            "memory_mb": _memory_mb(),
            "voice_engine": voice_engine,
            "integrity_guard": integrity_guard,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._persist(record)
        try:
            from engines.pipeline_integrity.passive_openddf import observe_stage_success

            observe_stage_success(self.task_id, stage_name)
        except ImportError:
            pass
        return record

    def _persist(self, record: dict[str, Any]) -> None:
        try:
            from engines.dub_task_state import AUTO_TASKS, STATE_LOCK

            with STATE_LOCK:
                task = AUTO_TASKS.get(self.task_id)
                if not task:
                    return
                info = task.setdefault("info", {})
                records = info.setdefault("runtime_diagnostics", [])
                records.append(record)
                session_dir = info.get("session_dir")
            if session_dir:
                path = Path(str(session_dir)) / "runtime_diagnostics.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                existing: list[Any] = []
                if path.is_file():
                    try:
                        existing = json.loads(path.read_text(encoding="utf-8"))
                    except Exception:
                        existing = []
                if not isinstance(existing, list):
                    existing = []
                existing.append(record)
                path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.debug("runtime diagnostics persist skipped: %s", exc)
