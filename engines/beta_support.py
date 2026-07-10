"""Поддержка бета-тестирования: диагностика, отчёты об ошибках, отзывы."""

from __future__ import annotations

import importlib.util
import json
import platform
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engines.app_version import version_info
from engines.ffmpeg_paths import find_ffmpeg, find_ffprobe
from engines.ocr_engine import ocr_available


def _has(pkg: str) -> bool:
    return importlib.util.find_spec(pkg) is not None


def _ram_gb() -> float:
    try:
        import psutil

        return round(psutil.virtual_memory().total / 1024**3, 1)
    except Exception:
        return -1.0


def _ram_free_gb() -> float:
    try:
        import psutil

        return round(psutil.virtual_memory().available / 1024**3, 1)
    except Exception:
        return -1.0


def _whisper_status() -> dict[str, Any]:
    ok_fw = _has("faster_whisper")
    ok_ow = _has("whisper")
    backend = "faster-whisper" if ok_fw else ("openai-whisper" if ok_ow else "")
    return {
        "id": "whisper",
        "label": "Whisper (распознавание речи)",
        "ok": ok_fw or ok_ow,
        "detail": backend or "не установлен",
        "hint": "pip install faster-whisper" if not (ok_fw or ok_ow) else "",
        "critical": False,
    }


def _tts_status() -> dict[str, Any]:
    ok = _has("edge_tts")
    return {
        "id": "tts",
        "label": "Edge-TTS (озвучка)",
        "ok": ok,
        "detail": "edge-tts" if ok else "не установлен",
        "hint": "pip install edge-tts" if not ok else "",
        "critical": True,
    }


def _translation_status() -> dict[str, Any]:
    modes = []
    if _has("deep_translator"):
        modes.append("deep-translator")
    if _has("googletrans"):
        modes.append("googletrans")
    if _has("argostranslate"):
        modes.append("argostranslate")
    ok = bool(modes)
    return {
        "id": "translation",
        "label": "Переводчик",
        "ok": ok,
        "detail": ", ".join(modes) if modes else "не установлен",
        "hint": "pip install deep-translator" if not ok else "",
        "critical": False,
    }


def run_diagnostics(app_dir: Path) -> dict[str, Any]:
    ff = find_ffmpeg()
    fp = find_ffprobe()
    ffmpeg_ok = bool(ff)

    try:
        usage = shutil.disk_usage(str(app_dir))
        disk_free_gb = round(usage.free / 1024**3, 1)
        disk_total_gb = round(usage.total / 1024**3, 1)
    except Exception:
        disk_free_gb = disk_total_gb = -1.0

    ocr_ok, ocr_detail = ocr_available()

    checks: list[dict[str, Any]] = [
        {
            "id": "ffmpeg",
            "label": "FFmpeg",
            "ok": ffmpeg_ok,
            "detail": ff or "не найден",
            "hint": "Положите ffmpeg в tools/ffmpeg/ или добавьте в PATH" if not ffmpeg_ok else "",
            "critical": True,
        },
        {
            "id": "ffprobe",
            "label": "FFprobe",
            "ok": bool(fp),
            "detail": fp or "не найден",
            "hint": "" if fp else "Идёт вместе с FFmpeg",
            "critical": False,
        },
        _whisper_status(),
        _tts_status(),
        _translation_status(),
        {
            "id": "ocr",
            "label": "OCR (текст с экрана)",
            "ok": ocr_ok,
            "detail": ocr_detail,
            "hint": "pip install pytesseract + Tesseract OCR" if not ocr_ok else "",
            "critical": False,
        },
        {
            "id": "pydub",
            "label": "Pydub (тайминг)",
            "ok": _has("pydub"),
            "detail": "pydub" if _has("pydub") else "не установлен",
            "hint": "pip install pydub" if not _has("pydub") else "",
            "critical": False,
        },
        {
            "id": "langdetect",
            "label": "Langdetect",
            "ok": _has("langdetect"),
            "detail": "langdetect" if _has("langdetect") else "не установлен",
            "hint": "",
            "critical": False,
        },
        {
            "id": "disk",
            "label": "Свободное место на диске",
            "ok": disk_free_gb >= 2.0 if disk_free_gb >= 0 else False,
            "detail": f"{disk_free_gb} ГБ свободно из {disk_total_gb} ГБ" if disk_free_gb >= 0 else "неизвестно",
            "hint": "Освободите минимум 2 ГБ для дубляжа" if 0 <= disk_free_gb < 2 else "",
            "critical": True,
        },
        {
            "id": "ram",
            "label": "Оперативная память",
            "ok": _ram_gb() >= 4.0 if _ram_gb() >= 0 else True,
            "detail": (
                f"{_ram_gb()} ГБ всего, {_ram_free_gb()} ГБ свободно"
                if _ram_gb() >= 0
                else "установите psutil для точной проверки"
            ),
            "hint": "Рекомендуется 8+ ГБ RAM для длинных видео" if 0 <= _ram_gb() < 8 else "",
            "critical": False,
        },
    ]

    problems = [c for c in checks if not c["ok"]]
    critical_fail = any(c["ok"] is False and c.get("critical") for c in checks)
    ready = not critical_fail and ffmpeg_ok and _has("edge_tts")

    if ready and not problems:
        summary = "🟢 Всё готово к работе"
        level = "ok"
    elif ready:
        summary = "🟡 Готово с предупреждениями"
        level = "warn"
    else:
        summary = "🔴 Есть проблемы — см. список ниже"
        level = "error"

    return {
        "ready": ready,
        "level": level,
        "summary": summary,
        "checks": checks,
        "problems": [{"id": p["id"], "label": p["label"], "hint": p.get("hint", ""), "detail": p.get("detail", "")} for p in problems],
        "disk_free_gb": disk_free_gb,
        "platform": platform.platform(),
        "python_ver": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "app": version_info(),
    }


def _latest_dev_logs(app_dir: Path, task_id: str | None = None) -> list[Path]:
    dev_dir = app_dir / "output" / "dev"
    if not dev_dir.is_dir():
        return []
    if task_id:
        matched = sorted(dev_dir.glob(f"*_{task_id}.log"))
        if matched:
            return matched
    return sorted(dev_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)[:20]


def _collect_task_diagnostics(app_dir: Path, task_id: str | None) -> dict[str, Any]:
    """Gather pipeline / TTS / session artifacts for error-report ZIP."""
    out: dict[str, Any] = {"task_id": task_id, "found": False}
    if not task_id:
        return out
    try:
        from engines.dubbing_engine.pipeline_failure_diag import (
            build_pipeline_state_snapshot,
            build_task_config_snapshot,
        )

        out["pipeline_state"] = build_pipeline_state_snapshot(task_id)
        out["task_config"] = build_task_config_snapshot(task_id)
        out["found"] = bool(out["pipeline_state"].get("found"))
    except ImportError:
        try:
            from engines.dubbing_engine.tts_failure_diag import build_pipeline_state_snapshot

            out["pipeline_state"] = build_pipeline_state_snapshot(task_id)
            out["found"] = bool(out["pipeline_state"].get("found"))
        except ImportError:
            out["pipeline_state"] = {}

    try:
        from engines.dub_task_state import AUTO_TASKS, STATE_LOCK

        with STATE_LOCK:
            task = AUTO_TASKS.get(task_id)
            if task:
                info = task.get("info") or {}
                out["voice_engine"] = {
                    "voice": info.get("voice") or info.get("tts_voice"),
                    "engine_id": info.get("tts_engine_id") or "edge-offline",
                    "target_lang": info.get("target_lang"),
                }
                out["tts_failures"] = list(info.get("tts_failures") or [])
                out["pipeline_failures"] = list(info.get("pipeline_failures") or [])
                out["pipeline_error"] = info.get("pipeline_error")
                out["pipeline_error_developer"] = info.get("pipeline_error_developer")
                out["openddf_artifacts"] = info.get("openddf_artifacts")
                out["runtime_diagnostics"] = list(info.get("runtime_diagnostics") or [])
                out["session_dir"] = info.get("session_dir")
    except ImportError:
        pass

    studio_path = app_dir / "output" / "studio_sessions" / f"{Path(task_id).name}.json"
    out["studio_session_path"] = str(studio_path) if studio_path.is_file() else None
    return out


def build_error_report(
    app_dir: Path,
    *,
    task_id: str | None = None,
    error_message: str = "",
    user_comment: str = "",
    page: str = "",
    diagnostic: str = "",
) -> dict[str, Any]:
    reports_dir = app_dir / "output" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    zip_name = f"error_report_{ts}.zip"
    zip_path = reports_dir / zip_name

    diag = run_diagnostics(app_dir)
    task_diag = _collect_task_diagnostics(app_dir, task_id)
    pipeline_state = task_diag.get("pipeline_state") or {}

    try:
        from engines.dubbing_engine.pipeline_failure_diag import build_engine_info_snapshot

        engine_info = build_engine_info_snapshot(app_dir)
    except ImportError:
        engine_info = {
            "app": version_info(),
            "python": sys.version,
            "platform": platform.platform(),
        }

    stack_parts: list[str] = []
    if diagnostic:
        stack_parts.append(diagnostic)
    for fail in task_diag.get("pipeline_failures") or []:
        if isinstance(fail, dict):
            block = fail.get("diagnostic_block") or ""
            tb = fail.get("traceback") or ""
            stack_parts.append(block)
            if tb:
                stack_parts.append("--- stack trace ---")
                stack_parts.append(tb)
    for fail in task_diag.get("tts_failures") or []:
        if isinstance(fail, dict):
            block = fail.get("diagnostic_block") or ""
            tb = fail.get("traceback") or ""
            if block and block not in "\n".join(stack_parts):
                stack_parts.append(block)
            if tb:
                stack_parts.append("--- stack trace ---")
                stack_parts.append(tb)
    if error_message and not stack_parts:
        stack_parts.append(str(error_message))

    meta = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "app": version_info(),
        "platform": platform.platform(),
        "windows_release": platform.win32_ver() if sys.platform == "win32" else None,
        "python": sys.version,
        "python_ver": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "ffmpeg": engine_info.get("ffmpeg"),
        "task_id": task_id,
        "error_message": error_message,
        "user_comment": user_comment,
        "page": page,
        "diagnostics_summary": diag.get("summary"),
        "voice_engine": task_diag.get("voice_engine"),
        "tts_failure_count": len(task_diag.get("tts_failures") or []),
        "pipeline_failure_count": len(task_diag.get("pipeline_failures") or []),
    }

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("report.json", json.dumps(meta, ensure_ascii=False, indent=2))
        zf.writestr(
            "pipeline_state.json",
            json.dumps(pipeline_state, ensure_ascii=False, indent=2),
        )
        zf.writestr(
            "engine_info.json",
            json.dumps(engine_info, ensure_ascii=False, indent=2),
        )
        zf.writestr(
            "config.json",
            json.dumps(task_diag.get("task_config") or {}, ensure_ascii=False, indent=2),
        )
        if task_diag.get("runtime_diagnostics"):
            zf.writestr(
                "runtime_diagnostics.json",
                json.dumps(task_diag["runtime_diagnostics"], ensure_ascii=False, indent=2),
            )
        if engine_info.get("installed_models") is not None:
            zf.writestr(
                "installed_models.json",
                json.dumps(
                    {
                        "models": engine_info.get("installed_models") or [],
                        "model_count": engine_info.get("model_count", 0),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
        if task_diag.get("tts_failures"):
            zf.writestr(
                "tts_failures.json",
                json.dumps(task_diag["tts_failures"], ensure_ascii=False, indent=2),
            )
        if task_diag.get("pipeline_failures"):
            zf.writestr(
                "pipeline_failures.json",
                json.dumps(task_diag["pipeline_failures"], ensure_ascii=False, indent=2),
            )
        openddf_dev = task_diag.get("pipeline_error_developer")
        if openddf_dev:
            zf.writestr(
                "openddf_developer_payload.json",
                json.dumps(openddf_dev, ensure_ascii=False, indent=2),
            )
        openddf_arts = task_diag.get("openddf_artifacts") or {}
        for key in ("snapshot_before", "snapshot_after", "snapshot_diff", "report", "pipeline_log", "stacktrace", "environment", "diagnostic_zip"):
            art_path = openddf_arts.get(key)
            if art_path and Path(str(art_path)).is_file():
                zf.write(str(art_path), f"OpenDDF/{Path(str(art_path)).name}")
        if task_diag.get("voice_engine"):
            zf.writestr(
                "voice_engine.json",
                json.dumps(task_diag["voice_engine"], ensure_ascii=False, indent=2),
            )
        if stack_parts:
            zf.writestr("stacktrace.txt", "\n\n".join(stack_parts))

        studio_path = task_diag.get("studio_session_path")
        if studio_path and Path(studio_path).is_file():
            zf.write(studio_path, "ProjectSession/studio_session.json")
            zf.write(studio_path, "ProjectSession.json")

        session_dir = task_diag.get("session_dir")
        if session_dir:
            sd = Path(str(session_dir))
            if sd.is_dir():
                for p in sorted(sd.glob("tts_failure_*.json")):
                    zf.write(p, f"ProjectSession/{p.name}")
                for p in sorted(sd.glob("pipeline_failure_*.json")):
                    zf.write(p, f"ProjectSession/{p.name}")
                for p in sorted(sd.glob("runtime_diagnostics.json")):
                    zf.write(p, f"ProjectSession/{p.name}")
                for p in sorted(sd.glob("conflict_resolver_report.json")):
                    zf.write(p, f"ProjectSession/{p.name}")

        logs_dir = "logs"
        for rel in (
            "output/logs/tubedub.log",
            "output/desktop_error.log",
            "output/dub_segment_log.txt",
            "output/dub_timing_fit_log.txt",
        ):
            p = app_dir / rel
            if p.is_file():
                arc = f"{logs_dir}/{p.name}"
                zf.write(p, arc)

        for log_path in _latest_dev_logs(app_dir, task_id):
            arc = f"{logs_dir}/dev/{log_path.name}"
            zf.write(log_path, arc)

    return {
        "ok": True,
        "filename": zip_name,
        "path": str(zip_path),
        "size_bytes": zip_path.stat().st_size if zip_path.is_file() else 0,
    }


def save_feedback(
    app_dir: Path,
    *,
    stars: int,
    liked: str = "",
    improve: str = "",
    task_id: str | None = None,
) -> dict[str, Any]:
    feedback_dir = app_dir / "output" / "feedback"
    feedback_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    entry = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "stars": max(1, min(5, int(stars))),
        "liked": liked.strip(),
        "improve": improve.strip(),
        "task_id": task_id,
        "app": version_info(),
        "platform": platform.platform(),
    }
    path = feedback_dir / f"feedback_{ts}.json"
    path.write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "path": str(path)}
