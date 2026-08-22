# auto_dub_api.py
import os
import re
import time
import uuid
import json
import shutil
import logging
import copy
import subprocess
import threading
from contextlib import contextmanager
from pathlib import Path
from flask import Blueprint, request, jsonify
from pydub import AudioSegment
import ffmpeg

from data.languages import LANG_CODE_TO_NAME
from engines.dub_task_state import (
    AUTO_TASK_CONTROLS,
    AUTO_TASKS,
    STATE_LOCK,
    evict_expired_auto_tasks,
    init_auto_task,
    touch_task,
)
from engines.locale_utils import resolve_server_locale
from engines.open_ddf import open_ddf as _open_ddf

# Настройки логирования и директорий
logger = logging.getLogger(__name__)
bp = Blueprint("auto_dub_api", __name__)
APP_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "output"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
_DEBUG_LOG_PATH = APP_DIR / "debug-7e57dc.log"

_DEFAULT_EDGE_VOICE_BY_LANG = {
    "uk": "uk-UA-OstapNeural",
    "ru": "ru-RU-DmitryNeural",
    "en": "en-US-GuyNeural",
    "de": "de-DE-ConradNeural",
    "fr": "fr-FR-HenriNeural",
    "es": "es-ES-AlvaroNeural",
    "pl": "pl-PL-MarekNeural",
    "zh": "zh-CN-YunxiNeural",
}


def _default_edge_voice(lang: str | None, fallback: str = "uk-UA-OstapNeural") -> str:
    """Pick Edge TTS voice from target language — never default RU voice for UK dubs."""
    code = str(lang or "").split("-")[0].strip().lower()
    return _DEFAULT_EDGE_VOICE_BY_LANG.get(code, fallback)


def _debug_meaning_fit_log(
    hypothesis_id: str, location: str, message: str, data: dict
) -> None:
    """Opt-in NDJSON diagnostics (VM_DEBUG_NDJSON=1). Off in production."""
    if (os.getenv("VM_DEBUG_NDJSON") or "").strip().lower() not in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return
    try:
        payload = {
            "sessionId": "7e57dc",
            "runId": "meaning-fit-autodub-gate",
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with _DEBUG_LOG_PATH.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _launch_trace_stage(
    task,
    stage: str,
    *,
    status: str,
    reason: str,
    module: str = "api/auto_dub_api.py",
    line: int | None = None,
    data: dict | None = None,
) -> None:
    """Emit a launch-decision-trace stage record (best-effort).

    Records BOTH to the runtime NDJSON debug log AND into
    ``task['info']['launch_decision_trace']`` so studio/OpenDDF can pick
    it up. Failures are absorbed so tracing can never break the run.
    """
    try:
        from engines.semantic_v3.launch_decision_trace import record_stage

        info = None
        try:
            info = (task or {}).get("info") if isinstance(task, dict) else None
        except Exception:
            info = None
        record_stage(
            stage,
            status=status,
            reason=reason,
            module=module,
            line=line,
            data=data or {},
            task_info=info,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("launch_trace_stage failed for stage=%s: %s", stage, exc)


def _launch_trace_agent(
    task,
    agent: str,
    *,
    called: bool,
    called_by: str = "",
    skipped_reason: str = "",
    module: str = "api/auto_dub_api.py",
    line: int | None = None,
    data: dict | None = None,
) -> None:
    """Emit a launch-decision-trace AI-agent record (best-effort)."""
    try:
        from engines.semantic_v3.launch_decision_trace import record_agent

        info = None
        try:
            info = (task or {}).get("info") if isinstance(task, dict) else None
        except Exception:
            info = None
        record_agent(
            agent,
            called=called,
            called_by=called_by,
            skipped_reason=skipped_reason,
            module=module,
            line=line,
            data=data or {},
            task_info=info,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("launch_trace_agent failed for agent=%s: %s", agent, exc)


def _launch_trace_seed(task) -> None:
    """Seed the AI agent slot placeholders inside ``task['info']``."""
    try:
        from engines.semantic_v3.launch_decision_trace import seed_ai_agent_slots

        info = None
        try:
            info = (task or {}).get("info") if isinstance(task, dict) else None
        except Exception:
            info = None
        seed_ai_agent_slots(info)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("launch_trace_seed failed: %s", exc)


def _artifacts_dir(task_info: dict | None = None) -> Path:
    """Session-scoped artifact directory when ProjectSession is active."""
    from engines.dubbing_engine.session_adapter import get_active_artifacts_dir

    return get_active_artifacts_dir(OUTPUT_DIR, task_info=task_info)
PIPELINE_STEPS = [
    "preparing",
    "extract_audio",
    "transcribe",
    "translate",
    "tts",
    "timing",
    "dub",
]

STEP_LABELS = {
    "ru": {
        "preparing": "Подготовка",
        "extract_audio": "Извлечение аудио",
        "transcribe": "Whisper",
        "segment_prep": "Подготовка сегментов",
        "translate": "Перевод",
        "translation_review": "Проверка перевода",
        "tts": "TTS",
        "studio": "Студия",
        "timing": "Сведение",
        "dub": "Подготавливается MP4…",
        "done": "Готово",
    },
    "uk": {
        "preparing": "Підготовка",
        "extract_audio": "Витяг аудіо",
        "transcribe": "Whisper",
        "segment_prep": "Підготовка сегментів",
        "translate": "Переклад",
        "translation_review": "Перевірка перекладу",
        "tts": "TTS",
        "studio": "Студія",
        "timing": "Зведення",
        "dub": "Підготовка MP4…",
        "done": "Готово",
    },
    "en": {
        "preparing": "Preparing",
        "extract_audio": "Extracting audio",
        "transcribe": "Whisper",
        "segment_prep": "Segment prep",
        "translate": "Translation",
        "translation_review": "Translation review",
        "tts": "TTS",
        "studio": "Studio",
        "timing": "Mixing",
        "dub": "Preparing MP4…",
        "done": "Done",
    },
}


# Локализация уведомлений
LOCALIZATION = {
    "ru": {
        "not_found": "Задача не найдена.",
        "invalid_idx": "Неверный индекс сегмента.",
        "tts_failed": "Не удалось сгенерировать аудио для сегмента.",
        "timing_failed": "Ошибка генерации таймингов дорожки.",
        "ffmpeg_missing": "Критическая ошибка: FFmpeg не найден в системе.",
        "empty_stt": "Распознавание текста вернуло пустоту.",
        "timed_missing": "Критическая ошибка: Файл звуковой дорожки timed.mp3 отсутствует.",
        "dub_missing": "DubEngine сообщил об успехе, но выходной видеофайл MP4 отсутствует.",
        "contract_broken": "Критическая ошибка: Контракт DubEngine поврежден.",
        "export_error": "Критическая ошибка: не удалось физически записать timed.mp3 на диск.",
        "pipeline_aborted": "Пайплайн прерван (пауза или внутренняя ошибка).",
        "segment_mismatch": "Не удалось согласовать сегменты перевода с таймингом.",
        "tts_timeout": "TTS завис или превысил лимит времени. Проверьте интернет.",
        "output_file_blocked": "Файл с _OUTPUT_ в имени — это уже готовый дубляж. Выберите оригинальное видео без _OUTPUT_.",
        "translate_failed": "Ошибка перевода. Проверьте интернет или выберите язык оригинала вручную.",
        "translate_timeout": "Перевод занял слишком много времени (более 60 секунд). Попробуйте снова или уменьшите длину видео.",
        "translate_not_prepared": "Модель перевода не подготовлена. Дождитесь завершения этапа «Подготовка компонентов».",
        "long_processing": "Выполняется длительная обработка. Пожалуйста, подождите.",
    },
    "en": {
        "not_found": "Task not found.",
        "invalid_idx": "Invalid segment index.",
        "tts_failed": "Failed to generate audio for segment.",
        "timing_failed": "Timing engine track generation failed.",
        "ffmpeg_missing": "Critical error: FFmpeg is not found in the system.",
        "empty_stt": "Speech-to-text returned empty string.",
        "timed_missing": "Critical error: Timed audio file timed.mp3 is missing.",
        "dub_missing": "DubEngine reported success, but output MP4 file is missing.",
        "contract_broken": "Critical error: DubEngine contract format is broken.",
        "export_error": "Critical error: failed to physically write timed.mp3 to disk.",
        "pipeline_aborted": "Pipeline aborted (pause or internal error).",
        "segment_mismatch": "Could not align translated segments with timing map.",
        "tts_timeout": "TTS timed out. Check your internet connection.",
        "output_file_blocked": "Files with _OUTPUT_ in the name are already dubbed. Pick the original video without _OUTPUT_.",
        "translate_failed": "Translation failed. Check your internet or pick the source language manually.",
        "translate_timeout": "Translation took too long (over 60 seconds). Try again or use a shorter video.",
        "translate_not_prepared": "Translation model is not prepared. Wait for «Preparing components» to finish.",
        "long_processing": "Processing is taking longer than usual. Please wait.",
    },
    "uk": {
        "not_found": "Завдання не знайдено.",
        "invalid_idx": "Невірний індекс сегмента.",
        "tts_failed": "Не вдалося згенерувати аудіо для сегмента.",
        "timing_failed": "Помилка генерації таймінгів доріжки.",
        "ffmpeg_missing": "Критична помилка: FFmpeg не знайдено в системі.",
        "empty_stt": "Розпізнавання тексту повернуло порожнечу.",
        "timed_missing": "Критична помилка: файл timed.mp3 відсутній.",
        "dub_missing": "DubEngine повідомив про успіх, але MP4 відсутній.",
        "contract_broken": "Критична помилка: контракт DubEngine пошкоджено.",
        "export_error": "Критична помилка: не вдалося записати timed.mp3 на диск.",
        "pipeline_aborted": "Пайплайн перервано (пауза або внутрішня помилка).",
        "segment_mismatch": "Не вдалося узгодити сегменти перекладу з таймінгом.",
        "tts_timeout": "TTS завис або перевищив ліміт часу. Перевірте інтернет.",
        "output_file_blocked": "Файл з _OUTPUT_ у назві — це вже готовий дубляж. Оберіть оригінальне відео без _OUTPUT_.",
        "translate_failed": "Помилка перекладу. Перевірте інтернет або оберіть мову оригіналу вручну.",
        "translate_timeout": "Переклад зайняв занадто багато часу (понад 60 секунд). Спробуйте знову або скоротіть відео.",
        "translate_not_prepared": "Модель перекладу не підготовлена. Дочекайтеся завершення «Підготовка компонентів».",
        "long_processing": "Триває тривала обробка. Будь ласка, зачекайте.",
    },
}


def _watchdog_status_snapshot(task_id: str) -> dict | None:
    try:
        from engines.pipeline_watchdog import get_pipeline_watchdog

        wd = get_pipeline_watchdog(task_id)
        return wd.snapshot() if wd else None
    except Exception:
        return None


def _segment_tts_progress_meta(
    task_id: str,
    head_idx: int,
    segments_data: list,
    *,
    voice: str,
    tts_engine_id: str,
    text: str,
) -> dict:
    """Metadata for transparent TTS progress (voice, LLM, chars, slot)."""
    meta: dict = {
        "voice": voice,
        "tts_engine": tts_engine_id,
        "tts_engine_id": tts_engine_id,
        "char_count": len(str(text or "")),
        "text_chars": len(str(text or "")),
    }
    seg = segments_data[head_idx] if 0 <= head_idx < len(segments_data) else {}
    start = seg.get("start")
    end = seg.get("end")
    if start is not None and end is not None:
        try:
            meta["segment_duration_ms"] = max(0, int(end) - int(start))
            meta["slot_ms"] = meta["segment_duration_ms"]
        except (TypeError, ValueError):
            pass
    try:
        with STATE_LOCK:
            task = AUTO_TASKS.get(task_id)
            audits = (task.get("info") or {}).get("translation_audits") or []
        for a in audits:
            if int(a.get("index", -1)) == head_idx:
                meta["llm_model"] = a.get("engine") or a.get("model") or ""
                meta["translation_model"] = meta["llm_model"]
                break
        from engines.llm_adaptation_mode import detect_capabilities

        caps = detect_capabilities()
        if not meta.get("llm_model"):
            meta["llm_model"] = caps.get("model") or ""
    except Exception:
        pass
    return meta


def _resolve_ui_lang(lang: str | None) -> str:
    accept = ""
    try:
        accept = request.headers.get("Accept-Language", "")
    except Exception:
        accept = ""
    resolved = resolve_server_locale(lang, accept)
    if resolved in LOCALIZATION:
        return resolved
    return "en"


def _update_progress_detail(task_id: str, **fields) -> None:
    try:
        from engines.pipeline_progress_tracker import enrich_progress_fields

        fields = enrich_progress_fields(task_id, **fields)
    except Exception:
        fields = dict(fields)
        fields["last_heartbeat_at"] = time.time()

    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        if not task:
            return
        detail = task.setdefault("info", {}).setdefault("progress_detail", {})
        detail.update({k: v for k, v in fields.items() if v is not None})
    try:
        from engines.pipeline_watchdog import watchdog_heartbeat

        watchdog_heartbeat(task_id, **fields)
    except Exception:
        pass


@contextmanager
def _blocking_progress_heartbeat(
    task_id: str,
    phase: str,
    *,
    interval: float = 20.0,
    messages: list[str] | None = None,
):
    """Emit progress heartbeats while a stage blocks the pipeline thread (Whisper, init)."""
    stop = threading.Event()
    msgs = messages or [phase]

    def _loop() -> None:
        i = 0
        while not stop.wait(interval):
            msg = msgs[i % len(msgs)]
            i += 1
            _update_progress_detail(task_id, phase=phase, live_message=msg)

    _update_progress_detail(task_id, phase=phase, live_message=msgs[0])
    worker = threading.Thread(
        target=_loop,
        name=f"progress-hb-{task_id[:8]}-{phase}",
        daemon=True,
    )
    worker.start()
    try:
        yield
    finally:
        stop.set()
        worker.join(timeout=1.0)


@contextmanager
def _llm_inflight_heartbeat(task_id: str, *, interval: float = 20.0):
    """Reassure the pipeline watchdog while a SLOW-but-alive LLM translation call
    is in flight, so a legitimately slow local model (e.g. qwen2.5:7b on CPU) is
    not false-killed as a ``PIPELINE_STALLED`` while it is actually working.

    Quality path (user choice): let the model finish instead of aborting to fast
    MT. This does NOT weaken real stall detection:
      * it only heartbeats while an LLM call is genuinely in flight
        (``get_llm_inflight_snapshot`` is non-None) and within a sane age cap;
      * a dead worker thread is still caught by the watchdog's thread-alive check;
      * a hopeless/too-slow model is still aborted by translation_adapt's sticky
        slow-breaker + segment/project budgets, which then fall back to MT.
    """
    stop = threading.Event()

    def _cap() -> float:
        try:
            from engines.translation_adapt import _llm_call_timeout

            return max(900.0, float(_llm_call_timeout()) * 2.0 + 120.0)
        except Exception:
            return 900.0

    cap = _cap()

    def _loop() -> None:
        while not stop.wait(interval):
            try:
                from engines.translation_adapt import get_llm_inflight_snapshot

                snap = get_llm_inflight_snapshot()
            except Exception:
                snap = None
            if not snap:
                # No active LLM call right now → rely on the normal per-batch
                # progress callback / watchdog judgement (don't mask a real hang).
                continue
            started = float(snap.get("started_at") or 0)
            age = time.time() - started if started else 0.0
            if age > cap:
                # A single call has outlived any sane budget → stop reassuring so
                # the watchdog / breaker can act.
                continue
            try:
                _update_progress_detail(
                    task_id,
                    phase="translate",
                    llm_inflight=True,
                    llm_wait_sec=round(age, 1),
                    live_message=f"Перевод: модель обрабатывает текст… ({int(age)} с)",
                )
            except Exception:
                pass

    worker = threading.Thread(
        target=_loop,
        name=f"llm-hb-{task_id[:8]}",
        daemon=True,
    )
    worker.start()
    try:
        yield
    finally:
        stop.set()
        worker.join(timeout=1.0)


def _runtime_stage_record(
    task_id: str,
    recorder,
    stage_num: int,
    stage_name: str,
    *,
    segments_ok: int | None = None,
    errors: int = 0,
) -> None:
    if recorder is None:
        return
    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        info = (task or {}).get("info") or {}
        segs = info.get("segments_data") or []
        engine_id = info.get("tts_engine_id") or "edge-offline"
        integrity = str((info.get("pipeline_integrity") or {}).get("status") or "ok")
    from engines.dubbing_engine.tts_failure_diag import engine_display_name

    total = len(segs)
    ok = segments_ok if segments_ok is not None else total
    recorder.stage_complete(
        stage_num,
        stage_name,
        segments_total=total,
        segments_ok=ok,
        errors=errors,
        voice_engine=engine_display_name(str(engine_id)),
        integrity_guard=integrity,
    )


def _estimate_eta_sec(task_id: str, step_progress: float) -> int | None:
    """Rough ETA from step progress fraction 0..1 within current step."""
    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        if not task:
            return None
        started = float(task.get("info", {}).get("step_started_at") or 0)
    if started <= 0 or step_progress <= 0.01:
        return None
    elapsed = time.time() - started
    remaining = elapsed * (1.0 - step_progress) / max(step_progress, 0.01)
    return int(max(0, remaining))


@bp.get("/api/auto_dub/voice_catalog")
def api_voice_catalog():
    path = APP_DIR / "data" / "voice_catalog.json"
    voices = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            voices = data.get("voices") if isinstance(data, dict) else {}
        except Exception:
            pass
    return jsonify({"ok": True, "voices": voices or {}})


@bp.get("/api/auto_dub/content_modes")
def api_content_modes():
    """Return all supported content modes with localised labels."""
    lang = request.args.get("lang", "ru")
    try:
        from engines.dubbing_engine.content_mode import all_modes_for_ui
        modes = all_modes_for_ui(lang)
    except Exception as exc:
        logger.warning("[content_modes] %s", exc)
        modes = [{"value": "movie", "label": "🎬 Фільм / Movie"}]
    return jsonify({"ok": True, "modes": modes})


@bp.get("/api/auto_dub/event_bus/status")
def api_event_bus_status():
    """Event Bus diagnostics (TZ Stage 1)."""
    from core.event_bus import get_event_bus
    from core.event_pipeline import event_bus_enabled

    bus = get_event_bus()
    return jsonify(
        {
            "ok": True,
            "enabled": event_bus_enabled(),
            "running": bus.running,
            "recent_events": bus.history(limit=30),
        }
    )


@bp.get("/api/auto_dub/pipeline_orchestrator/status")
def api_pipeline_orchestrator_status():
    """Pipeline + LLM orchestrator capacity and dispatch status (diagnostics)."""
    from engines.llm_orchestrator import get_llm_orchestrator
    from engines.pipeline_orchestrator import get_planner

    return jsonify(
        {
            "ok": True,
            "planner": get_planner().to_dict(),
            "llm_orchestrator": get_llm_orchestrator().status(),
        }
    )


@bp.get("/api/auto_dub/adaptation_capabilities")
def api_adaptation_capabilities():
    """Report adaptation backends + recommended mode for the UI (TZ §3/§9).

    Lets the dub page warn the user when no LLM is configured instead of silently
    degrading quality.
    """
    from engines.llm_adaptation_mode import (
        MODE_STRICT,
        detect_capabilities,
        recommended_mode,
        resolve_adaptation_mode,
    )

    caps = detect_capabilities()
    flag_strict = resolve_adaptation_mode({}) == MODE_STRICT
    warning = None
    if not caps.get("llm_available"):
        warning = (
            "Для максимально качественного дубляжа рекомендуется установить AI-модуль TubeDub. "
            "Можно продолжить работу в упрощённом режиме."
        )
    elif caps.get("model_warning"):
        # LLM is present but too small for reliable multilingual adaptation.
        warning = caps.get("model_warning")
    try:
        from engines.ai_core.global_skill import skill_version, to_dict as skill_to_dict
        from engines.llm_providers.transport import list_cloud_profiles

        skill_meta = skill_to_dict()
        cloud_profiles = list_cloud_profiles()
    except Exception:
        skill_meta = {}
        cloud_profiles = []
    return jsonify(
        {
            "ok": True,
            "capabilities": caps,
            "recommended_mode": recommended_mode(caps),
            "feature_flag_strict_default": flag_strict,
            "warning": warning,
            "active_model": {
                "model": caps.get("model"),
                "provider": caps.get("provider"),
                "display_name": _model_display_name(caps.get("model") or ""),
                "adequate": caps.get("model_adequate"),
            },
            "global_skill_version": skill_meta.get("version") or skill_version(),
            "cloud_profiles": cloud_profiles,
        }
    )


def _model_display_name(model: str) -> str:
    low = str(model or "").lower()
    if "deepseek" in low:
        return "DeepSeek"
    if "qwen" in low:
        return "Qwen"
    if "llama" in low:
        return "Llama"
    if "gemma" in low:
        return "Gemma"
    if "gpt" in low:
        return "OpenAI GPT"
    return model or "—"


@bp.get("/api/auto_dub/tts_engines")
def api_tts_engines():
    from engines.tts_backends import voices_for_backend
    from engines.tts_engines.registry import list_engine_infos

    infos = list_engine_infos(APP_DIR)
    stage20 = [
        {
            "id": "edge",
            "engine_id": "edge-offline",
            "name": "Edge TTS",
            "recommended": False,
            "hint": "Стабільний fallback (Ostap / Polina)",
            "voices": voices_for_backend("edge"),
        },
        {
            "id": "tts_uk",
            "engine_id": "tts_uk",
            "name": "tts_uk (RAD-TTS++ / Vocos)",
            "recommended": True,
            "hint": "Найкраща природність українського голосу + контроль тривалості",
            "voices": voices_for_backend("tts_uk"),
        },
        {
            "id": "piper",
            "engine_id": "piper",
            "name": "Piper (uk_UA-*-high)",
            "recommended": True,
            "hint": "Швидкий локальний TTS, CPU-friendly",
            "voices": voices_for_backend("piper"),
        },
    ]
    return jsonify(
        {
            "ok": True,
            "engines": [
                {
                    "id": i.id,
                    "name": i.name,
                    "mode": i.mode,
                    "provider": i.provider,
                    "description": i.description,
                    "available": i.available,
                    "supports_stress": i.supports_stress,
                }
                for i in infos
            ],
            "uk_backends": stage20,
            "hint": "tts_uk і Piper дають більш природний український голос",
        }
    )


def _apply_translation_text_edits(
    info: dict,
    edits: list[dict] | None = None,
) -> list[str]:
    """Apply user text edits to segments_data + audits; return segment texts."""
    from engines.translation_review import build_translation_review

    segments_data = info.get("segments_data") or []
    audits = info.get("translation_audits") or []
    audit_by_idx = {int(a.get("index", -1)): a for a in audits}

    if not segments_data and audits:
        for i, row in enumerate(sorted(audits, key=lambda a: int(a.get("index", 0)))):
            idx = int(row.get("index", i))
            text = str(
                row.get("final_text")
                or row.get("tts_text")
                or row.get("naturalized_text")
                or row.get("raw_translation")
                or ""
            ).strip()
            segments_data.append(
                {
                    "index": idx,
                    "text": text,
                    "plain_text": text,
                    "translation_text": text,
                    "file": None,
                }
            )
        _stamp_segment_identity(segments_data)

    if edits:
        from engines.pipeline_integrity.identity_guard import resolve_row_by_identity
        from engines.pipeline_integrity.pipeline_state import assert_text_change_uses_revision
        from engines.pipeline_integrity.revision_manager import (
            ensure_tts_uuid,
            note_text_change,
        )

        for item in edits:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or item.get("new_text") or "").strip()
            if not text:
                continue
            sid = str(item.get("segment_id") or "").strip()
            idx = -1
            seg = None
            if sid:
                seg, how = resolve_row_by_identity(segments_data, segment_id=sid)
                if how == "segment_id" and seg is not None:
                    try:
                        idx = segments_data.index(seg)
                    except ValueError:
                        idx = next(
                            (
                                i
                                for i, s in enumerate(segments_data)
                                if isinstance(s, dict)
                                and str(s.get("segment_id") or "") == sid
                            ),
                            -1,
                        )
            if seg is None:
                # Display number only (1-based). Never the identity key.
                try:
                    display = int(item.get("index", 0))
                except (TypeError, ValueError):
                    continue
                idx = display - 1
                if idx < 0 or idx >= len(segments_data):
                    continue
                seg = segments_data[idx] if isinstance(segments_data[idx], dict) else None
            if not isinstance(seg, dict) or idx < 0:
                continue
            old_rev = str(
                seg.get("adaptation_uuid")
                or seg.get("translation_uuid")
                or ""
            ).strip()
            locked = bool(seg.get("translation_locked") or info.get("translation_locked"))
            note_text_change(seg, text, kind="adaptation")
            ensure_tts_uuid(seg, force_new=True)
            try:
                assert_text_change_uses_revision(
                    info, seg, text, old_revision=old_rev
                )
            except Exception:
                pass
            seg["text"] = text
            seg["plain_text"] = text
            seg["translation_text"] = text
            seg["needs_retts"] = True
            if locked:
                # Explicit reopen → edit → relock. Do not silently keep lock.
                seg["translation_locked"] = False
                seg["lock_reopened"] = True
            # Manual Review: re-approve as single ApprovedText (user is the owner)
            if info.get("tps") or seg.get("needs_manual_review"):
                try:
                    from engines.tps.approved_text import approve_segment

                    approve_segment(
                        seg,
                        text,
                        tqe_status="PASS",
                        path="manual",
                        task_id=str(info.get("task_id") or ""),
                        index=idx,
                    )
                    seg["needs_manual_review"] = False
                    seg["user_edited"] = True
                except Exception:
                    seg["approved_text"] = text
                    seg["needs_manual_review"] = False
            row = audit_by_idx.get(idx)
            if row:
                row["final_text"] = text
                row["tts_text"] = text
                row["approved_text"] = text
                row["user_edited"] = True
                row["tqe_status"] = "PASS"
                row["tps_path"] = "manual"
                try:
                    from engines.translation_router import record_manual_correction

                    src = info.get("detected_lang") or info.get("source_lang") or "en"
                    tgt = info.get("target_lang") or "ru"
                    engine = str(row.get("engine") or "unknown")
                    record_manual_correction(
                        APP_DIR,
                        src_lang=src,
                        tgt_lang=tgt,
                        engine=engine,
                    )
                except Exception:
                    pass

        edited_idx = []
        for item in edits:
            sid = str((item or {}).get("segment_id") or "").strip()
            if sid:
                for i, s in enumerate(segments_data):
                    if isinstance(s, dict) and str(s.get("segment_id") or "") == sid:
                        edited_idx.append(i)
                        break
                continue
            try:
                edited_idx.append(int(item.get("index", 0)) - 1)
            except (TypeError, ValueError):
                continue
    else:
        edited_idx = []

    texts = []
    for i, seg in enumerate(segments_data):
        row = audit_by_idx.get(i, {})
        texts.append(
            str(row.get("final_text") or seg.get("text") or "").strip()
        )
    info["segments_data"] = segments_data
    info["translation_audits"] = audits

    # P4: re-run DSAL on user-edited segments (pre-LOCK editorial)
    try:
        from engines.dsal.studio_editorial import refresh_dsal_on_edits

        if edited_idx:
            refresh_dsal_on_edits(info, indices=edited_idx, allow_llm=False)
    except Exception:
        pass

    info["translation_review"] = build_translation_review(info)
    return texts


def _raw_mt_texts_from_info(info: dict) -> list[str]:
    """Faithful raw MT per segment for AI Core Translation Agent / adaptation."""
    audits = info.get("translation_audits") or []
    segments_data = info.get("segments_data") or []
    source_segments = info.get("source_segments") or []
    by_idx = {int(a.get("index", -1)): a for a in audits}
    count = max(len(audits), len(segments_data), len(source_segments))
    out: list[str] = []
    for i in range(count):
        row = by_idx.get(i, {})
        text = str(
            row.get("raw_translation")
            or row.get("naturalized_text")
            or row.get("final_text")
            or row.get("tts_text")
            or ""
        ).strip()
        if not text and i < len(segments_data):
            text = str(
                segments_data[i].get("translation_text")
                or segments_data[i].get("plain_text")
                or segments_data[i].get("text")
                or ""
            ).strip()
        out.append(text)
    return out


def _translation_review_requires_manual_hold() -> bool:
    """Pause pipeline only when developer diagnostics / inspector need manual review."""
    from engines.translation_diagnostics import dev_diagnostics_enabled
    from engines.translation_inspector import inspector_enabled

    return dev_diagnostics_enabled() or inspector_enabled()


def _populate_translation_review_data(task_id: str, segments: list[str]) -> None:
    """Build translation review snapshot without changing pipeline control state."""
    from engines.translation_review import build_translation_review
    from engines.translation_diagnostics import (
        build_developer_diagnostics,
        dev_diagnostics_enabled,
    )

    _UNSAFE = frozenset(
        {
            "meaning_collapse",
            "cjk_meaning_collapse",
            "source_script_leak",
            "meaning_loss",
        }
    )

    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        if not task:
            return
        live = task["info"]
        live["task_id"] = task_id
        # Snapshot is a view (TZ §22): never write final_text/tts_text back
        # into live pipeline state merely because Review opened / populated.
        info = copy.deepcopy(live)
        # Last-resort align runs on the snapshot only.
        try:
            from engines.tts_review_align import align_info_for_translation_review

            align_info_for_translation_review(info)
        except Exception as _align_exc:
            logger.debug("review_align at populate skipped: %s", _align_exc)
        audits = info.get("translation_audits") or []
        audit_by_idx = {int(a.get("index", -1)): a for a in audits}
        prev_sd = list(info.get("segments_data") or [])
        segments_data = []
        for i, seg in enumerate(segments):
            text = str(seg or "").strip()
            row = audit_by_idx.get(i, {})
            prev_row = prev_sd[i] if i < len(prev_sd) and isinstance(prev_sd[i], dict) else {}
            trh = prev_row.get("trh") if isinstance(prev_row.get("trh"), dict) else {}
            reasons = set(prev_row.get("tps_reason_codes") or []) | set(
                trh.get("reason_codes") or []
            ) | set(row.get("reason_codes") or [])
            tqe = str(
                prev_row.get("tqe_status") or trh.get("tqe_status") or row.get("tqe_status") or ""
            ).upper()
            blocked = bool(
                prev_row.get("tts_blocked")
                or prev_row.get("skip_tts")
                or bool(reasons & _UNSAFE)
            )
            # Never resurrect collapsed MT / source leak into TTS-bound fields
            if blocked:
                text = ""
                polished = ""
            else:
                from engines.translation_validation import is_shared_mt_blob_reclaim

                # Stage 4: fitted/locked spoken text beats stale semantic audit blobs.
                fitted_snap = list(info.get("fitted_tts_texts") or [])
                locked = str(
                    (fitted_snap[i] if i < len(fitted_snap) else "")
                    or prev_row.get("final_tts_text")
                    or row.get("final_tts_text")
                    or ""
                ).strip()
                passed = str(seg or "").strip()
                if info.get("final_tts_locked") and (locked or passed):
                    text = locked or passed
                    polished = text
                    if row:
                        row["final_text"] = text
                        row["tts_text"] = text
                        row["final_tts_text"] = text
                        row["semantic_text"] = text
                        row["semantic_engine_text"] = text
                else:
                    final_owned = str(row.get("final_text") or "").strip()
                    semantic_polished = str(
                        row.get("semantic_text") or row.get("semantic_engine_text") or ""
                    ).strip()
                    naturalized = str(row.get("naturalized_text") or "").strip()
                    # Prefer debleeded Final over a stale multi-segment semantic blob.
                    if (
                        final_owned
                        and semantic_polished
                        and is_shared_mt_blob_reclaim(final_owned, semantic_polished)
                    ):
                        polished = final_owned
                        row["semantic_text"] = final_owned
                        row["semantic_engine_text"] = final_owned
                    elif locked:
                        polished = locked
                    else:
                        # Prefer Final / passed segment over longer semantic when Final
                        # was already shortened by text-slot fit.
                        if (
                            final_owned
                            and semantic_polished
                            and len(semantic_polished) > len(final_owned) + 12
                        ):
                            polished = final_owned
                        else:
                            polished = str(
                                semantic_polished
                                or final_owned
                                or naturalized
                                or passed
                                or ""
                            ).strip()
                    if polished and not polished.lstrip().startswith("<speak"):
                        text = polished
                    elif final_owned:
                        text = final_owned
                    elif passed:
                        text = passed
                # Stage 9: strip invented slot-pad fillers before Review UI.
                try:
                    from engines.text_slot_fit import strip_slot_pad_fillers

                    cleaned = strip_slot_pad_fillers(text)
                    if cleaned != text:
                        text = cleaned
                        polished = cleaned
                        if row:
                            for _rk in (
                                "final_text",
                                "tts_text",
                                "final_tts_text",
                                "semantic_text",
                                "semantic_engine_text",
                                "naturalized_text",
                            ):
                                if row.get(_rk):
                                    row[_rk] = strip_slot_pad_fillers(str(row.get(_rk) or ""))
                except Exception:
                    pass
                # Do NOT fall back to source language text for target-track TTS
            raw_keep = str(
                row.get("raw_translation")
                or prev_row.get("translated_text")
                or prev_row.get("raw_mt")
                or ""
            ).strip()
            rejected = str(
                prev_row.get("rejected_translation") or (raw_keep if blocked else "")
            ).strip()
            entry = {
                "index": i,
                "text": text,
                "plain_text": text,
                "translation_text": text,
                "translated_text": raw_keep or text,
                "final_text": text,
                "final_tts_text": text,
                "tts_text": text,
                "text_for_tts": text,
                "spoken_text_source": "final_tts_text",
                "file": None,
            }
            # Preserve TPS / TRH block flags across review rebuild
            for key in (
                "tts_blocked",
                "skip_tts",
                "tts_blocked_reason",
                "needs_manual_review",
                "tps_reason_codes",
                "tqe_status",
                "tps_path",
                "approved_text",
                "raw_mt",
                "naturalized_text",
                "trh",
                "segment_id",
                "final_tts_text",
                "tts_text_hash",
                "spoken_text_source",
                "final_tts_source",
                "text_slot_fit",
            ):
                if prev_row.get(key) not in (None, ""):
                    entry[key] = prev_row.get(key)
            if text and info.get("final_tts_locked"):
                entry["final_tts_text"] = text
                entry["spoken_text_source"] = "final_tts_text"
            if blocked:
                entry["tts_blocked"] = True
                entry["skip_tts"] = True
                entry["needs_manual_review"] = True
                for _bk in (
                    "approved_text",
                    "text",
                    "plain_text",
                    "translation_text",
                    "final_text",
                    "text_for_tts",
                    "voice_input",
                    "semantic_text",
                    "semantic_engine_text",
                    "grammar_text",
                    "timing_text",
                ):
                    entry[_bk] = ""
                entry["rejected_translation"] = rejected or raw_keep
                if reasons:
                    entry["tps_reason_codes"] = sorted(reasons)
                if "FAIL" not in str(entry.get("tqe_status") or "").upper():
                    entry["tqe_status"] = tqe or "FAIL_MANUAL_REVIEW"
            segments_data.append(entry)
            if i < len(info.get("source_word_maps") or []):
                segments_data[-1]["source_word_map"] = info["source_word_maps"][i]
            elif i < len(prev_sd):
                old = prev_sd[i].get("source_word_map") if isinstance(prev_sd[i], dict) else None
                if old:
                    segments_data[-1]["source_word_map"] = old
            if row and text and not blocked:
                from engines.translation_validation import (
                    prefer_semantic_authority,
                    stamp_authoritative_final_text,
                    texts_equivalent_for_ownership,
                )

                raw_mt = str(row.get("raw_translation") or "").strip()
                final_now = str(row.get("final_text") or "").strip()
                semantic = str(
                    row.get("semantic_text") or row.get("semantic_engine_text") or ""
                ).strip()
                if info.get("final_tts_locked"):
                    stamp_authoritative_final_text(
                        segments_data[-1],
                        text,
                        audit=row,
                        preserve_semantic_engine=False,
                    )
                    try:
                        from engines.tts_text_authority import stamp_final_tts_text

                        stamp_final_tts_text(
                            segments_data[-1],
                            text,
                            audit=row,
                            source="review_populate_locked",
                        )
                    except Exception:
                        segments_data[-1]["final_tts_text"] = text
                elif (
                    not final_now
                    or (raw_mt and texts_equivalent_for_ownership(final_now, raw_mt))
                    or prefer_semantic_authority(
                        semantic=semantic or text,
                        candidate=final_now or text,
                        raw_mt=raw_mt,
                    )
                ):
                    stamp_authoritative_final_text(
                        segments_data[-1],
                        text,
                        audit=row,
                        preserve_semantic_engine=True,
                    )
            elif blocked and row:
                row["final_text"] = ""
                row["tts_text"] = ""
                row["approved_text"] = ""
                row["tts_blocked"] = True
                if rejected or raw_keep:
                    row["rejected_translation"] = rejected or raw_keep
        # Snapshot-only: do not replace live segments_data / audits.
        info["segments_data"] = segments_data
        _stamp_segment_identity(segments_data)
        live["translation_review"] = build_translation_review(info)
        if dev_diagnostics_enabled():
            live["translation_diagnostics"] = build_developer_diagnostics(info)
            try:
                from engines.pipeline_platform.dev_view import build_dev_pipeline_view

                live["pipeline_platform_trace"] = build_dev_pipeline_view(
                    info, task_id=task_id, app_dir=str(Path(__file__).resolve().parent.parent)
                )
            except Exception:
                pass
        from engines.translation_inspector import build_translation_inspector, inspector_enabled

        if inspector_enabled():
            live["translation_inspector"] = build_translation_inspector(info)


def _enter_translation_review_pause(task_id: str) -> None:
    """Mark task paused at translation review (60%) until user approves."""
    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        control = AUTO_TASK_CONTROLS.get(task_id)
        if not task or not control:
            return
        task["status"] = "translation_review"
        task["step"] = "translation_review"
        task["progress"] = 60.0
        control["state"] = "paused"
        control["awaiting_translation_review"] = True
        control["editing"] = False
        control["editor_error"] = False


def _pause_for_translation_review(task_id: str, segments: list[str]) -> None:
    """Populate review data and pause pipeline until user approves."""
    _populate_translation_review_data(task_id, segments)
    _enter_translation_review_pause(task_id)


def _resume_from_translation_review(task_id: str) -> list[str] | None:
    """Clear review pause and return approved segment texts."""
    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        control = AUTO_TASK_CONTROLS.get(task_id)
        if not task or not control:
            return None
        info = task["info"]
        texts = _apply_translation_text_edits(info)
        control["awaiting_translation_review"] = False
        control["state"] = "running"
        control["editing"] = False
        control["editor_error"] = False
        task["status"] = "running"
        info["translation_review_approved"] = True
        return texts


def _step_label(step: str, ui_lang: str = "ru") -> str:
    labels = STEP_LABELS.get(ui_lang, STEP_LABELS["ru"])
    if step == "done" or step not in labels:
        return labels.get(step, labels.get("preparing", step))
    return labels.get(step, step)


def _get_lp(req):
    lang = _resolve_ui_lang(req.args.get("lang"))
    return LOCALIZATION.get(lang, LOCALIZATION["ru"])


def _normalize_pipeline_target_lang(raw) -> str:
    """Target dub language must be a concrete code — never '', auto, or None."""
    val = str(raw or "").strip().lower().replace("_", "-")
    if not val or val in {"auto", "авто", "none", "null", "undefined"}:
        return "ru"
    # UI sometimes sends display names; keep first token / ISO-ish code
    base = val.split("-")[0].split()[0]
    if len(base) > 8 or not base.isalpha():
        return "ru"
    return base


def _parse_timing(t_range):
    """Безопасное извлечение временных меток диапазона в миллисекундах."""
    try:
        if isinstance(t_range, (list, tuple)) and len(t_range) == 2:
            return int(t_range[0]), int(t_range[1])
        if isinstance(t_range, dict):
            return int(t_range.get("start", t_range.get("start_ms", 0)) or 0), int(
                t_range.get("end", t_range.get("end_ms", 0)) or 0
            )
    except (ValueError, TypeError):
        pass
    return 0, 0


def _safe_export_audio(audio_obj, path):
    """Унифицированный безопасный экспорт аудио-объекта на диск с обязательной проверкой."""
    try:
        if audio_obj is None:
            return False
        out = Path(path)
        duration_ms = len(audio_obj)
        try:
            from engines.ffmpeg_paths import find_ffmpeg

            ffmpeg = find_ffmpeg()
        except Exception:
            ffmpeg = shutil.which("ffmpeg")
        if ffmpeg and duration_ms > 30_000:
            import tempfile

            tmp = Path(tempfile.gettempdir()) / f"vm_timed_{uuid.uuid4().hex}.wav"
            try:
                audio_obj.export(str(tmp), format="wav")
                timeout = max(120, int(duration_ms / 1000) * 4)
                subprocess.run(
                    [
                        ffmpeg,
                        "-y",
                        "-i",
                        str(tmp),
                        "-codec:a",
                        "libmp3lame",
                        "-b:a",
                        "192k",
                        str(out),
                    ],
                    check=True,
                    capture_output=True,
                    timeout=timeout,
                )
                return out.is_file()
            finally:
                tmp.unlink(missing_ok=True)
        audio_obj.export(str(out), format="mp3")
        return out.is_file()
    except Exception as e:
        logger.error(f"Ошибка экспорта аудио: {e}")
        return False


DUB_SEGMENT_LOG = APP_DIR / "output" / "dub_segment_log.txt"
DUB_TIMING_FIT_LOG = APP_DIR / "output" / "dub_timing_fit_log.txt"


def _video_duration_ms(video_path: str) -> int | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe or not video_path:
        return None
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "quiet",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                video_path,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return int(float(result.stdout.strip()) * 1000)
    except Exception:
        return None


def _write_dub_segment_log(task_id: str, entries: list[str]) -> None:
    try:
        DUB_SEGMENT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(DUB_SEGMENT_LOG, "a", encoding="utf-8") as f:
            f.write(f"=== task={task_id} ===\n")
            for line in entries:
                f.write(line + "\n")
    except Exception as e:
        logger.warning("dub segment log write failed: %s", e)


def _regen_segment_tts_simple(
    text: str,
    voice: str,
    tts_rate: str | None = None,
    tts_pitch: str | None = None,
    *,
    segment_id: str | None = None,
    task_id: str | None = None,
    engine_id: str | None = None,
) -> str | None:
    """Simple TTS regen returning a unique filename bound to segment_id."""
    from engines.pipeline_integrity.audio_identity import (
        copy_to_unique_path,
        ensure_segment_uuid,
    )
    from engines.tts import generate_audio
    from engines.tts_backends import normalize_backend_name

    if not text.strip():
        return None
    meta = {"segment_id": segment_id or ""}
    suid = ensure_segment_uuid(meta)
    eid = normalize_backend_name(engine_id or "edge-offline")
    files = _normalize_tts_result(
        generate_audio(
            text=text,
            voice=voice,
            segments=[text],
            rate=tts_rate,
            pitch=tts_pitch,
            engine_id=eid,
            context={
                "segment_id": suid,
                "segment_uuid": suid,
                "task_id": task_id or "",
                "tts_backend": eid,
            },
        )
    )
    if not files:
        return None
    src = _artifacts_dir() / files[0]
    if not src.is_file():
        return files[0]
    dest = copy_to_unique_path(
        src,
        _artifacts_dir(),
        segment_uuid=suid,
        run_id=str(task_id or ""),
        purpose="tts_regen",
    )
    return dest.name


def _stamp_segment_identity(rows: list) -> list:
    """Guarantee UUID segment_id on every row (survives segments_data rebuilds)."""
    from engines.pipeline_integrity.audio_identity import ensure_all_segment_uuids

    segs = [r for r in (rows or []) if isinstance(r, dict)]
    if segs:
        ensure_all_segment_uuids(segs)
        try:
            from engines.pipeline_integrity.v2_gates import revision_manager_enabled
            from engines.pipeline_integrity.uuid_chain import ensure_all_uuids

            if revision_manager_enabled():
                for row in segs:
                    ensure_all_uuids(row)
        except Exception:
            pass
    return rows


def _identity_bind_after_regen(
    seg: dict,
    tts_text: str,
    audio_path: str | None,
    *,
    segments_data: list | None = None,
    stage: str = "regen",
    require_wav: bool = True,
) -> None:
    """Bind wav↔text↔tts_uuid after intentional regen (allow_rebind)."""
    if not isinstance(seg, dict):
        return
    from engines.pipeline_integrity.exceptions import IdentityMismatchError
    from engines.pipeline_integrity.identity_guard import (
        assert_consistent,
        bind_after_tts,
    )

    try:
        bind_after_tts(
            seg,
            tts_text=str(tts_text or ""),
            audio_path=audio_path,
            stage=stage,
            allow_rebind=True,
            segments_data=segments_data,
        )
    except IdentityMismatchError:
        raise
    except Exception as exc:
        logger.warning("IdentityGuard bind_after_tts skipped: %s", exc)
    if segments_data is not None:
        try:
            assert_consistent(
                segments_data, stage=stage, require_wav=require_wav
            )
        except IdentityMismatchError:
            raise
        except Exception as exc:
            logger.warning("IdentityGuard assert_consistent skipped: %s", exc)


def _apply_text_adaptation(
    seg: dict,
    issue: dict,
    source_segments: list,
    voice: str,
    tts_files: list,
    stage: str,
    tts_rate: str | None = None,
    tts_pitch: str | None = None,
    semantic_log=None,
    tgt_lang: str = "ru",
    src_lang: str = "en",
) -> bool:
    from engines.translation_adapt import adapt_for_duration
    from engines.semantic_adaptation import record_post_tts_adaptation

    idx = issue["idx"]
    src_hint = source_segments[idx] if idx < len(source_segments) else ""
    target_ms = max(200, int(issue["window_ms"]) - 40)
    original_text = str(seg.get("text") or "")
    new_text = adapt_for_duration(
        original_text,
        int(issue["tts_ms"]),
        target_ms,
        src_hint,
        stage=stage,
        tgt_lang=tgt_lang,
    )
    if new_text == seg.get("text"):
        return False

    old_file = seg.get("file")
    new_file = _regen_segment_tts_simple(
        new_text,
        voice,
        tts_rate=tts_rate,
        tts_pitch=tts_pitch,
        segment_id=str(seg.get("segment_id") or ""),
    )
    if not new_file:
        return False

    if old_file:
        (_artifacts_dir() / Path(old_file).name).unlink(missing_ok=True)
    seg["text"] = new_text
    seg["file"] = new_file
    tts_files.append(new_file)
    _identity_bind_after_regen(
        seg,
        new_text,
        new_file,
        stage="post_tts_adapt_regen",
        require_wav=False,
    )

    try:
        from pydub import AudioSegment

        tts_after = len(AudioSegment.from_file(str(_artifacts_dir() / new_file)))
    except Exception:
        tts_after = 0

    record_post_tts_adaptation(
        semantic_log,
        index=idx,
        source_hint=src_hint,
        original=original_text,
        adapted=new_text,
        reason=f"post_tts_overflow_{stage}",
        window_ms=target_ms,
        tts_ms_before=int(issue.get("tts_ms") or 0),
        tts_ms_after=tts_after,
        src_lang=src_lang,
        tgt_lang=tgt_lang,
    )
    return True


def _wtm_record_checkpoint(wtm_log, task_id: str, stage: str) -> None:
    """Phase 0: verify Word Timing Map integrity at pipeline stage (no dub changes)."""
    if wtm_log is None:
        return
    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        if not task:
            return
        wtm_log.record(task["info"], stage)
        task["info"]["word_timing_checkpoints"] = wtm_log.to_dict()


def _segments_data_entries(
    segments: list[str],
    info: dict,
) -> list[dict]:
    """Build segments_data rows; preserve source_word_map from task info (Phase 1 WTM)."""
    source_word_maps = info.get("source_word_maps") or []
    audits = info.get("translation_audits") or []
    audit_by_idx = {int(a.get("index", -1)): a for a in audits}
    old_by_idx = {
        int(s.get("index", i)): s
        for i, s in enumerate(info.get("segments_data") or [])
        if isinstance(s, dict)
    }
    _UNSAFE = frozenset(
        {
            "meaning_collapse",
            "cjk_meaning_collapse",
            "source_script_leak",
            "meaning_loss",
        }
    )
    out: list[dict] = []
    for i, seg in enumerate(segments):
        text = str(seg or "").strip()
        audit = audit_by_idx.get(i, {}) or {}
        old = old_by_idx.get(i) or {}
        trh = old.get("trh") if isinstance(old.get("trh"), dict) else {}
        reasons = set(old.get("tps_reason_codes") or []) | set(
            trh.get("reason_codes") or []
        ) | set(audit.get("reason_codes") or [])
        tqe = str(
            old.get("tqe_status") or trh.get("tqe_status") or audit.get("tqe_status") or ""
        ).upper()
        # Unsafe collapse/leak codes block regardless of PASS + approved_text
        # (production: TPS stamped PASS while approved still contained CJK leak).
        blocked = bool(
            old.get("tts_blocked")
            or old.get("skip_tts")
            or audit.get("tts_blocked")
            or bool(reasons & _UNSAFE)
        )
        if blocked:
            working = ""
        else:
            # Prefer live segment text; only use audit final when segment empty
            # and not a failed/manual hallucination.
            final = str(audit.get("final_text") or "").strip()
            working = text or final
        entry: dict = {
            "index": i,
            "text": working,
            "plain_text": working,
            "translation_text": working,
            "final_text": working,
            "file": None,
        }
        # Preserve TPS / block flags
        for key in (
            "segment_id",
            "tts_blocked",
            "skip_tts",
            "tts_blocked_reason",
            "needs_manual_review",
            "tps_reason_codes",
            "tqe_status",
            "tps_path",
            "approved_text",
            "rejected_translation",
            "raw_mt",
            "naturalized_text",
            "trh",
            "original",
            "original_text",
            "whisper_text",
            "source_text",
        ):
            if old.get(key) not in (None, ""):
                entry[key] = old.get(key)
        orig = str(
            entry.get("original")
            or old.get("original")
            or old.get("original_text")
            or old.get("whisper_text")
            or audit.get("original")
            or audit.get("source_text")
            or ""
        ).strip()
        if not orig:
            src_rows = (
                info.get("source_segments")
                or info.get("source_segments_snapshot")
                or []
            )
            if i < len(src_rows):
                orig = str(src_rows[i] or "").strip()
        if orig:
            entry["original"] = orig
        if blocked:
            entry["tts_blocked"] = True
            entry["skip_tts"] = True
            entry["needs_manual_review"] = True
            for _bk in (
                "approved_text",
                "text",
                "plain_text",
                "translation_text",
                "final_text",
                "text_for_tts",
                "voice_input",
                "semantic_text",
                "semantic_engine_text",
                "grammar_text",
                "timing_text",
            ):
                entry[_bk] = ""
            if not entry.get("rejected_translation"):
                entry["rejected_translation"] = str(
                    audit.get("raw_translation")
                    or audit.get("naturalized_text")
                    or old.get("rejected_translation")
                    or ""
                ).strip()
            if "FAIL" not in str(entry.get("tqe_status") or "").upper():
                entry["tqe_status"] = "FAIL_MANUAL_REVIEW"
            if reasons:
                entry["tps_reason_codes"] = sorted(reasons)
        if i < len(source_word_maps):
            entry["source_word_map"] = source_word_maps[i]
        elif old.get("source_word_map"):
            entry["source_word_map"] = old["source_word_map"]
        out.append(entry)
    _stamp_segment_identity(out)
    return out


_INTEGRITY_COORDINATORS: dict = {}


def _integrity_coordinator(task_id: str):
    from engines.pipeline_integrity import PipelineIntegrityCoordinator

    if task_id not in _INTEGRITY_COORDINATORS:
        _INTEGRITY_COORDINATORS[task_id] = PipelineIntegrityCoordinator(task_id=task_id)
    return _INTEGRITY_COORDINATORS[task_id]


def _drop_integrity_coordinator(task_id: str) -> None:
    _INTEGRITY_COORDINATORS.pop(task_id, None)


def _commit_tts_group_result(
    segments_data: list,
    indices: list[int],
    *,
    tts_text: str,
    audio_filename: str | None,
    task_info: dict | None,
) -> None:
    """Write only TTS-contract segment fields after synthesis."""
    from engines.pipeline_integrity.tts_segment_fields import (
        apply_tts_synthesis_result,
        mark_merged_tts_children,
        measure_playback_duration_ms,
    )

    if not indices:
        return
    head_idx = int(indices[0])
    if head_idx >= len(segments_data):
        return
    if audio_filename:
        audio_path = _resolve_segment_audio_path(audio_filename, task_info)
        try:
            abs_audio = (
                str(audio_path.resolve())
                if audio_path.is_file()
                else str(audio_path)
            )
        except OSError:
            abs_audio = str(audio_path)
        duration = measure_playback_duration_ms(audio_path)
        apply_tts_synthesis_result(
            segments_data[head_idx],
            tts_text=tts_text,
            tts_file_path=abs_audio if abs_audio else audio_filename,
            playback_duration=duration or None,
            status="generated",
        )
        try:
            from engines.tts_backends import (
                pop_last_synth_meta,
                stamp_tts_backend_meta,
            )
            from engines.tts_lang_lock import cyrillic_letter_ratio

            # Stage 26/30 — honour the actually-used backend/voice so a silent
            # Edge fallback never masquerades as tts_uk/Mykyta in the JSON.
            _synth_meta = pop_last_synth_meta(abs_audio if abs_audio else audio_path)
            _engine_eff = str(
                _synth_meta.get("tts_engine")
                or _synth_meta.get("tts_backend")
                or ""
            )
            _voice_eff = str(_synth_meta.get("tts_voice") or "")
            if not _engine_eff:
                _guess_v = str(
                    _voice_eff
                    or segments_data[head_idx].get("voice")
                    or ""
                )
                if _guess_v.startswith("uk-UA-"):
                    _engine_eff = "edge-offline"
                    _voice_eff = _guess_v
                else:
                    _engine_eff = str(
                        (task_info or {}).get("tts_engine")
                        or (task_info or {}).get("tts_backend")
                        or ""
                    )
            if not _voice_eff:
                _voice_eff = str(
                    segments_data[head_idx].get("voice")
                    or (task_info or {}).get("voice")
                    or ""
                )
            stamp_tts_backend_meta(
                segments_data[head_idx],
                engine_id=_engine_eff or None,
                voice=_voice_eff,
                synth_meta=_synth_meta,
                language=str(
                    (task_info or {}).get("tts_language")
                    or (task_info or {}).get("target_lang")
                    or "uk"
                ),
                cyrillic_ratio=cyrillic_letter_ratio(tts_text),
                controls={
                    "rate": (task_info or {}).get("mykyta_rate")
                    or (task_info or {}).get("tts_rate"),
                    "pitch": (task_info or {}).get("mykyta_pitch")
                    or (task_info or {}).get("tts_pitch"),
                    "volume": (task_info or {}).get("mykyta_volume")
                    or (task_info or {}).get("tts_volume"),
                    "length_scale": (task_info or {}).get("mykyta_length_scale")
                    or (task_info or {}).get("tts_length_scale"),
                },
            )
            if _synth_meta.get("tts_fallback_reason"):
                segments_data[head_idx]["tts_fallback_reason"] = str(
                    _synth_meta["tts_fallback_reason"]
                )
            if _synth_meta.get("tts_engine_requested"):
                segments_data[head_idx]["tts_engine_requested"] = str(
                    _synth_meta["tts_engine_requested"]
                )
            if _synth_meta.get("tts_voice_requested"):
                segments_data[head_idx]["tts_voice_requested"] = str(
                    _synth_meta["tts_voice_requested"]
                )
        except Exception:
            pass
        from engines.pipeline_integrity.tts_file_lifecycle import (
            log_tts_lifecycle,
            verify_tts_file_on_disk,
        )

        task_id = str((task_info or {}).get("task_id") or "")
        sid = str(segments_data[head_idx].get("segment_id") or "")
        log_tts_lifecycle(
            task_id or None,
            event="gen_end",
            segment_id=sid or None,
            segment_index=head_idx,
            filename=audio_filename,
            path=audio_path,
            stage="tts",
            success=True,
            detail=f"duration_ms={duration}",
        )
        verify_tts_file_on_disk(
            audio_path,
            task_id=task_id or None,
            segment_id=sid or None,
            segment_index=head_idx,
            filename=audio_filename,
            stage="tts",
            event="post_write_verify",
        )
        from engines.translation_stage_log import log_translation_stage

        log_translation_stage(
            task_id or None,
            stage="post_tts",
            segment_index=head_idx,
            segment_id=sid or None,
            text=tts_text,
            detail=f"file={audio_filename}",
        )
    else:
        apply_tts_synthesis_result(
            segments_data[head_idx],
            tts_text=tts_text,
            tts_file_path=None,
            status="empty",
        )
    # PSA2 IdentityGuard — bind spoken text + wav to segment UUID (flag-gated)
    from engines.pipeline_integrity.exceptions import IdentityMismatchError
    from engines.pipeline_integrity.identity_guard import bind_after_tts

    _allow_rebind = bool(
        (task_info or {}).get("identity_allow_rebind")
        or segments_data[head_idx].get("identity_allow_rebind")
    )
    _head_seg = segments_data[head_idx]
    _bind_audio = (
        str(_head_seg.get("tts_file_path") or "").strip()
        or str(_head_seg.get("resolved_path") or "").strip()
        or audio_filename
    )
    try:
        bind_after_tts(
            _head_seg,
            tts_text=tts_text,
            audio_path=_bind_audio,
            stage="post_tts",
            allow_rebind=_allow_rebind,
            segments_data=segments_data,
        )
    except IdentityMismatchError:
        raise
    except Exception as _ig_bind_exc:
        logger.warning(
            "IdentityGuard bind_after_tts skipped: %s", _ig_bind_exc
        )
    # PSA5 RevisionManager — wav sidecar with tts_uuid + translation_uuid
    try:
        from engines.pipeline_integrity.revision_manager import (
            ensure_revision_uuids,
            ensure_tts_uuid,
            stamp_text_revision,
            write_wav_sidecar,
        )
        from engines.pipeline_integrity.v2_gates import revision_manager_enabled

        if revision_manager_enabled() and audio_filename:
            _seg_rm = segments_data[head_idx]
            ensure_revision_uuids(_seg_rm)
            if tts_text and not str(_seg_rm.get("tts_uuid") or "").strip():
                stamp_text_revision(_seg_rm, kind="tts_prep", text=tts_text)
            elif tts_text and _allow_rebind:
                ensure_tts_uuid(_seg_rm, force_new=True)
            _audio_abs = _resolve_segment_audio_path(audio_filename, task_info)
            write_wav_sidecar(_audio_abs or audio_filename, _seg_rm)
    except Exception as _rm_exc:
        logger.warning("RevisionManager sidecar skipped: %s", _rm_exc)
    mark_merged_tts_children(segments_data, indices)
    if audio_filename and task_info and _dev_preview_enabled(task_info):
        tid = str(task_info.get("task_id") or "")
        if tid:
            _schedule_dev_preview(tid)


def _strict_llm_adaptation_enabled() -> bool:
    """TZ §3 strict gate toggle.

    Enabled via env VM_STRICT_LLM_ADAPTATION=1 or the 'strict_llm_adaptation'
    feature flag. Default OFF so the product still produces a dub when no LLM is
    configured (overflow handled by gap/video-adapt, never truncation).
    """
    val = str(os.getenv("VM_STRICT_LLM_ADAPTATION") or "").strip().lower()
    if val in ("1", "true", "yes", "on"):
        return True
    if val in ("0", "false", "no", "off"):
        return False
    try:
        from engines.core.feature_flags import is_enabled

        return bool(is_enabled("strict_llm_adaptation", developer_session=False))
    except Exception:
        return False


def _post_tts_max_retries(task_info: dict | None = None) -> int:
    """Fewer post-TTS LLM loops in fast mode (CPU dub runs).

    Happy Path: 0 → no LLM rewrite loops. Stage 19b rule expand/shorten
    (`fit_text_to_slot` / `expand_to_fill`) still runs in closed-loop when
    |TTS−slot| > 350 ms — LLM is optional, not a blocker.
    """
    try:
        from engines.happy_path import advanced_adaptation_enabled

        if not advanced_adaptation_enabled(task_info or {}):
            return 0
    except Exception:
        return 0
    try:
        from engines.segment_timing_qa import MAX_TEXT_ADAPTATION_ITERATIONS
        from engines.translation_adapt import MODE_FAST, adaptation_speed_mode

        if adaptation_speed_mode() == MODE_FAST:
            return 2
        return MAX_TEXT_ADAPTATION_ITERATIONS
    except Exception:
        return 5


def _slot_duration_ms_for_pad(seg: dict, idx: int = 0) -> int:
    """Best-effort slot length for silence-pad fallback."""
    for key in (
        "slot_ms",
        "original_duration_ms",
        "playback_duration",
        "tts_ms",
        "final_tts_duration_ms",
    ):
        try:
            val = int(seg.get(key) or 0)
            if val > 0:
                return max(200, min(val, 30_000))
        except (TypeError, ValueError):
            pass
    try:
        st = int(seg.get("start_ms") or 0)
        en = int(seg.get("end_ms") or 0)
        if en > st:
            return max(200, min(en - st, 30_000))
    except (TypeError, ValueError):
        pass
    return 1000


def _make_silence_pad(slot_ms: int, out_path: Path) -> Path:
    """TZ: silent wav of slot length (min 200ms) so mux never aborts on holes.

    Stage 26 §2 — prefer pydub; fall back to a pure-stdlib ``wave`` writer so
    a missing pydub/ffmpeg dependency cannot silently drop the silence pad
    (that was the root cause of ``padded_count=0`` in diagnostic 9681e559).
    """
    ms = max(200, int(slot_ms or 500))
    ms = min(ms, 30_000)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        AudioSegment.silent(duration=ms).export(str(out_path), format="wav")
    except Exception as exc:
        logger.warning(
            "silence_pad pydub export failed (%s) — using stdlib wave fallback",
            exc,
        )
        _write_stdlib_silence_wav(out_path, duration_ms=ms, sample_rate=24000)
    return out_path


def _write_stdlib_silence_wav(
    out_path: Path,
    *,
    duration_ms: int,
    sample_rate: int = 24000,
) -> Path:
    """Write a mono 16-bit PCM silence WAV without any external dependency."""
    import wave

    frames = max(1, int(round((int(duration_ms) / 1000.0) * int(sample_rate))))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(int(sample_rate))
        fh.writeframes(b"\x00\x00" * frames)
    return out_path


def _write_silence_pad_for_segment(
    *,
    work_dir: Path,
    task_id: str | None,
    idx: int,
    segment_id: str,
    duration_ms: int,
) -> tuple[str, int]:
    """Write a short silence wav so mux can continue without cutting the video end.

    Stage 28 §A2 — always returns an absolute resolved path so downstream
    stamping cannot drift back to a relative `output/sessions/...` form.
    """
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    dur = max(200, min(int(duration_ms or 1000), 30_000))
    sid = (segment_id or f"idx{idx}")[:24]
    name = f"pad_silence_{sid or idx}.wav"
    path = _make_silence_pad(dur, work_dir / name)
    try:
        abs_path = str(Path(path).resolve())
    except OSError:
        abs_path = str(path)
    return abs_path, dur


def _soft_pad_missing_segments(
    segments_data: list,
    *,
    task_info: dict | None,
    task_id: str | None,
    timing_map: list | None = None,
    resolve_path=None,
) -> dict:
    """Fill any remaining audio holes with silence pads — mux must always continue.

    Returns stats: padded_indices, padded_count, missing_before.
    """
    from engines.pipeline_integrity.audio_presence import (
        MIN_AUDIO_BYTES,
        audio_stat,
        resolve_segment_audio_path,
        stamp_audio_presence,
    )
    from engines.pipeline_integrity.tts_segment_fields import resolve_segment_text_for_tts

    info = task_info if isinstance(task_info, dict) else {}

    def _resolve(p: str) -> str:
        if resolve_path:
            try:
                return str(resolve_path(p) or p)
            except Exception:
                return p
        try:
            return str(_resolve_segment_audio_path(p) or p)
        except Exception:
            return p

    # Stage 28 §A1/B3 — ALWAYS write pads to session_dir/closed_loop/<task_id>/,
    # never bare session root or relative output/sessions/... paths. The census
    # searches this exact subtree, so pads land where the resolver can find them.
    session_root: Path | None = None
    try:
        raw_sd = info.get("session_dir")
        if raw_sd:
            session_root = Path(str(raw_sd)).resolve()
    except Exception:
        session_root = None
    if session_root is None:
        try:
            session_root = _artifacts_dir(info).resolve()
        except Exception:
            session_root = _artifacts_dir(info)
    if isinstance(info, dict):
        info["session_dir"] = str(session_root)
    tid = str(task_id or info.get("task_id") or "pad").strip() or "pad"
    session_dir = (session_root / "closed_loop" / tid).resolve()
    session_dir.mkdir(parents=True, exist_ok=True)

    padded_indices: list[int] = []
    missing_before: list[int] = []

    for idx, seg in enumerate(segments_data or []):
        if not isinstance(seg, dict):
            continue
        if seg.get("merged_into") is not None or seg.get("merged_into_id"):
            continue
        # Stage 29 §B3/B4 — pad timeline holes even when skip_tts/tts_blocked
        # (census counts every non-merged row; audio_missing must stay 0).
        text = str(
            resolve_segment_text_for_tts(seg)
            or seg.get("final_tts_text")
            or seg.get("text")
            or ""
        ).strip()
        # Pad speakable holes and also empty-text / blocked slots that still occupy timeline.
        path = resolve_segment_audio_path(seg, resolve_path=_resolve)
        ok, _size = audio_stat(path)
        try:
            tts_ms = int(
                seg.get("tts_ms")
                or seg.get("playback_duration")
                or seg.get("final_tts_duration_ms")
                or 0
            )
        except (TypeError, ValueError):
            tts_ms = 0
        if ok and tts_ms > 0 and not seg.get("needs_re_tts"):
            continue
        if not text and ok and not seg.get("needs_re_tts"):
            continue
        missing_before.append(idx)

        slot_ms = _slot_duration_ms_for_pad(seg, idx)
        if timing_map is not None and idx < len(timing_map):
            try:
                from engines.timing_fit import _parse_timing as _pt

                st, en = _pt(timing_map[idx])
                if en > st:
                    slot_ms = max(200, min(en - st, 30_000))
            except Exception:
                pass

        sid = str(seg.get("segment_id") or f"idx{idx}")
        out = session_dir / f"pad_silence_{sid}.wav"
        try:
            pad_path = _make_silence_pad(slot_ms, out)
            _assert_audio_file(pad_path, min_bytes=MIN_AUDIO_BYTES)
            try:
                abs_p = str(Path(pad_path).resolve())
            except OSError:
                abs_p = str(pad_path)
            seg["file"] = abs_p
            seg["tts_file_path"] = abs_p
            seg["fitted_file"] = abs_p
            seg["resolved_path"] = abs_p
            seg["playback_duration"] = slot_ms
            seg["tts_ms"] = slot_ms
            seg["actual_duration_ms"] = slot_ms
            seg["final_tts_duration_ms"] = slot_ms
            seg["audio_padded"] = True
            seg["silence_pad"] = True
            seg["pad_reason"] = "missing_tts_after_repair"
            seg["needs_re_tts"] = False
            seg["status"] = "silence_pad"
            seg["tts_status"] = "silence_pad"
            seg["duration_control_used"] = "soft_pad"
            meta = dict(seg.get("stage23") or {})
            meta["silence_pad"] = True
            meta["audio_padded"] = True
            meta["pad_reason"] = "missing_tts_after_repair"
            meta["silence_pad_ms"] = slot_ms
            meta["duration_control_used"] = "soft_pad"
            seg["stage23"] = meta
            stamp_audio_presence(seg, resolve_path=_resolve)
            padded_indices.append(idx)
            logger.warning(
                "Task %s: soft-pad idx=%s sid=%s ms=%s (mux continues)",
                task_id,
                idx,
                sid[:16],
                slot_ms,
            )
        except Exception as pad_exc:
            logger.error(
                "Task %s: soft-pad FAILED idx=%s: %s — mux still continues",
                task_id,
                idx,
                pad_exc,
            )

    stats = {
        "padded_indices": padded_indices,
        "padded_count": len(padded_indices),
        "missing_before": missing_before,
        "missing_before_count": len(missing_before),
    }
    if info is not None:
        info["padded_indices"] = list(
            dict.fromkeys(list(info.get("padded_indices") or []) + padded_indices)
        )
        info["padded_count"] = len(info["padded_indices"])
        if padded_indices:
            info["final_status"] = "ok_with_pads"
            # Never block export because of pads.
            info.pop("export_blocked_reason", None)
        elif not info.get("final_status") or info.get("final_status") in (
            "audio_missing_fatal",
            "silence_pad_used",
        ):
            info["final_status"] = "ok"
            info.pop("export_blocked_reason", None)
    return stats


def _closed_loop_pad_dir(task_info: dict | None, task_id: str | None) -> Path:
    """Absolute ``session_dir/closed_loop/<task_id>/`` — one tree for pad + census."""
    info = task_info if isinstance(task_info, dict) else {}
    session_root: Path | None = None
    try:
        raw_sd = info.get("session_dir")
        if raw_sd:
            session_root = Path(str(raw_sd)).resolve()
    except Exception:
        session_root = None
    if session_root is None:
        try:
            session_root = _artifacts_dir(info).resolve()
        except Exception:
            session_root = _artifacts_dir(info)
    if isinstance(info, dict):
        info["session_dir"] = str(session_root)
    tid = str(task_id or info.get("task_id") or "pad").strip() or "pad"
    pad_dir = (session_root / "closed_loop" / tid).resolve()
    pad_dir.mkdir(parents=True, exist_ok=True)
    return pad_dir


def _last_resort_pad_missing_segments(
    segments_data: list,
    *,
    task_info: dict | None,
    task_id: str | None,
    timing_map: list | None = None,
    resolve_path=None,
) -> dict:
    """Stage 30 §A4 — stdlib-wave pad for EVERY remaining hole before census/mux.

    Writes ``session_dir/closed_loop/<task_id>/pad_silence_{sid}.wav`` and stamps
    absolute ``file`` / ``resolved_path`` / ``tts_file_path``. Census must see
    these on the same ``session_dir``.
    """
    from engines.pipeline_integrity.audio_presence import (
        MIN_AUDIO_BYTES,
        audio_stat,
        resolve_segment_audio_path,
        stamp_audio_presence,
    )

    info = task_info if isinstance(task_info, dict) else {}

    def _resolve(p: str) -> str:
        if resolve_path:
            try:
                return str(resolve_path(p) or p)
            except Exception:
                return p
        try:
            return str(_resolve_segment_audio_path(p) or p)
        except Exception:
            return p

    session_dir = _closed_loop_pad_dir(info, task_id)
    padded_indices: list[int] = []

    for idx, seg in enumerate(segments_data or []):
        if not isinstance(seg, dict):
            continue
        if seg.get("merged_into") is not None or seg.get("merged_into_id"):
            continue
        path = resolve_segment_audio_path(seg, resolve_path=_resolve)
        ok, size = audio_stat(path)
        if ok and size >= MIN_AUDIO_BYTES:
            try:
                abs_ok = str(Path(path).resolve()) if path else ""
            except OSError:
                abs_ok = str(path or "")
            if abs_ok:
                seg["file"] = abs_ok
                seg["tts_file_path"] = abs_ok
                seg["resolved_path"] = abs_ok
            continue
        slot_ms = _slot_duration_ms_for_pad(seg, idx)
        if timing_map is not None and idx < len(timing_map):
            try:
                from engines.timing_fit import _parse_timing as _pt

                st, en = _pt(timing_map[idx])
                if en > st:
                    slot_ms = max(200, min(en - st, 30_000))
            except Exception:
                pass
        sid = str(seg.get("segment_id") or f"idx{idx}")[:24]
        out = session_dir / f"pad_silence_{sid}.wav"
        try:
            _write_stdlib_silence_wav(out, duration_ms=slot_ms, sample_rate=24000)
            _assert_audio_file(out, min_bytes=MIN_AUDIO_BYTES)
            try:
                abs_p = str(Path(out).resolve())
            except OSError:
                abs_p = str(out)
            seg["file"] = abs_p
            seg["tts_file_path"] = abs_p
            seg["fitted_file"] = abs_p
            seg["resolved_path"] = abs_p
            seg["playback_duration"] = int(slot_ms)
            seg["tts_ms"] = int(slot_ms)
            seg["actual_duration_ms"] = int(slot_ms)
            seg["final_tts_duration_ms"] = int(slot_ms)
            seg["audio_padded"] = True
            seg["silence_pad"] = True
            seg["pad_reason"] = seg.get("pad_reason") or "last_resort_pad"
            seg["needs_re_tts"] = False
            seg["status"] = "silence_pad"
            seg["tts_status"] = "silence_pad"
            seg["duration_control_used"] = "soft_pad"
            meta = dict(seg.get("stage23") or {})
            meta["silence_pad"] = True
            meta["audio_padded"] = True
            meta["pad_reason"] = seg["pad_reason"]
            meta["silence_pad_ms"] = int(slot_ms)
            meta["duration_control_used"] = "soft_pad"
            seg["stage23"] = meta
            stamp_audio_presence(seg, resolve_path=_resolve)
            padded_indices.append(idx)
            logger.warning(
                "Task %s: LAST-RESORT pad idx=%s sid=%s ms=%s path=%s (mux continues)",
                task_id,
                idx,
                sid,
                slot_ms,
                abs_p,
            )
        except Exception as pad_exc:
            logger.error(
                "Task %s: LAST-RESORT pad FAILED idx=%s: %s",
                task_id,
                idx,
                pad_exc,
            )

    stats = {
        "padded_indices": padded_indices,
        "padded_count": len(padded_indices),
        "last_resort_pad_indices": padded_indices,
        "last_resort_pad_count": len(padded_indices),
    }
    if padded_indices and isinstance(info, dict):
        info["padded_indices"] = list(
            dict.fromkeys(list(info.get("padded_indices") or []) + padded_indices)
        )
        info["padded_count"] = len(info["padded_indices"])
        info["final_status"] = "ok_with_pads"
        info["last_resort_pad_indices"] = padded_indices
        info["last_resort_pad_count"] = len(padded_indices)
        info.pop("export_blocked_reason", None)
    return stats


def _log_census_missing_after_pad(
    *,
    task_id: str | None,
    segments_data: list,
    census: dict,
    session_dir: str | Path | None,
) -> None:
    """Stage 30 — per-idx diagnostic when census still sees holes after pad."""
    sd = str(session_dir or "")
    rows = [r for r in (census or {}).get("segments") or [] if isinstance(r, dict)]
    missing_rows = [r for r in rows if not r.get("exists")]
    if not missing_rows:
        return
    logger.error(
        "Task %s: census still missing=%s after pad session_dir=%s",
        task_id,
        len(missing_rows),
        sd,
    )
    segs = list(segments_data or [])
    by_index = {
        int(s.get("index", i)): s
        for i, s in enumerate(segs)
        if isinstance(s, dict)
    }
    for row in missing_rows:
        idx = int(row.get("index") if row.get("index") is not None else -1)
        seg = by_index.get(idx)
        path = str(row.get("resolved_path") or row.get("file") or "")
        exists = False
        size = 0
        padded = bool(row.get("audio_padded"))
        if isinstance(seg, dict):
            padded = bool(seg.get("audio_padded") or seg.get("silence_pad") or padded)
            if not path:
                path = str(
                    seg.get("resolved_path")
                    or seg.get("file")
                    or seg.get("tts_file_path")
                    or ""
                )
        if path:
            try:
                p = Path(path)
                exists = p.is_file()
                size = int(p.stat().st_size) if exists else 0
            except OSError:
                exists = False
                size = 0
        logger.error(
            "Task %s: census-missing-after-pad idx=%s path=%s exists=%s size=%s "
            "audio_padded=%s session_dir=%s",
            task_id,
            idx,
            path,
            exists,
            size,
            padded,
            sd,
        )


def _sync_pad_census_fields(info: dict | None, block: dict | None) -> dict:
    """Keep census disk truth. Never clobber padded_count with 0; never degraded if missing==0."""
    info = info if isinstance(info, dict) else {}
    block = block if isinstance(block, dict) else {}
    census_padded = int(block.get("padded_count") or 0)
    info_padded = int(info.get("padded_count") or 0)
    padded = max(census_padded, info_padded)
    indices = list(
        dict.fromkeys(
            list(block.get("padded_indices") or [])
            + list(info.get("padded_indices") or [])
        )
    )
    block["padded_count"] = padded
    block["padded_indices"] = indices
    info["padded_count"] = padded
    info["padded_indices"] = indices
    missing = int(block.get("audio_missing") or 0)
    if missing == 0:
        status = "ok_with_pads" if padded > 0 else "ok"
        block["final_status"] = status
        prev = info.get("final_status")
        if prev in (
            None,
            "",
            "audio_missing_fatal",
            "degraded",
            "needs_soft_pad",
            "silence_pad_used",
        ) or (padded > 0 and prev == "ok"):
            info["final_status"] = status
        elif padded > 0:
            info["final_status"] = "ok_with_pads"
    elif info.get("final_status") == "degraded" and padded > 0:
        info["final_status"] = "ok_with_pads"
        block["final_status"] = "ok_with_pads"
    info.pop("export_blocked_reason", None)
    return block


def _repair_missing_tts_files(
    segments_data: list,
    *,
    voice: str,
    task_info: dict | None,
    task_id: str | None = None,
    tts_rate: str | None = None,
    tts_pitch: str | None = None,
    resolve_path=None,
    min_bytes: int | None = None,
) -> dict:
    """Stage 23b: repair missing/empty/tiny TTS before handoff and mux.

    Regenerates when path missing, file missing on disk, size < min_bytes,
    tts_ms==0, needs_re_tts, or split child. Mykyta → Edge, up to 2 rounds.
    After failures: silence pad of slot length + warning (do not abort mux).
    """
    from engines.pipeline_integrity.audio_presence import (
        MIN_AUDIO_BYTES,
        audio_stat,
        segment_needs_audio_repair,
        stamp_audio_presence,
    )
    from engines.pipeline_integrity.tts_segment_fields import resolve_segment_text_for_tts
    from engines.tts_backends import normalize_backend_name

    info = task_info or {}
    primary = normalize_backend_name(
        info.get("tts_engine") or info.get("tts_backend") or "tts_uk"
    )
    _tgt0 = str(info.get("target_lang") or info.get("lang") or "uk").split("-")[0].lower()
    # Stage 24 UK: always Mykyta → edge-offline uk-UA (never cs/sk/pl/ru).
    if _tgt0 == "uk":
        engines_try = ["tts_uk", "edge-offline"]
    else:
        engines_try = [primary]
        if primary != "edge-offline":
            engines_try.append("edge-offline")
        if "tts_uk" not in engines_try and primary == "edge-offline":
            engines_try = ["tts_uk", "edge-offline"]

    floor = int(min_bytes if min_bytes is not None else MIN_AUDIO_BYTES)
    repaired = 0
    failed = 0
    skipped = 0
    padded = 0
    warnings: list[dict] = []
    # Stage 28 §A1 — repair MUST write under session_dir/closed_loop/<task_id>/;
    # never raw OUTPUT_DIR or a stale thread-local. Census walks this exact tree.
    _repair_root: Path | None = None
    try:
        _raw_sd_repair = info.get("session_dir") if isinstance(info, dict) else None
        if _raw_sd_repair:
            _repair_root = Path(str(_raw_sd_repair)).resolve()
    except Exception:
        _repair_root = None
    if _repair_root is None:
        try:
            _repair_root = _artifacts_dir(info).resolve()
        except Exception:
            _repair_root = _artifacts_dir(info)
    if isinstance(info, dict):
        info["session_dir"] = str(_repair_root)
    _tid_repair = str(task_id or (info.get("task_id") if isinstance(info, dict) else "") or "repair").strip() or "repair"
    work_root = (_repair_root / "closed_loop" / _tid_repair).resolve()
    work_root.mkdir(parents=True, exist_ok=True)

    def _resolve(p: str) -> str:
        if resolve_path:
            try:
                return str(resolve_path(p) or p)
            except Exception:
                return p
        try:
            return str(_resolve_segment_audio_path(p) or p)
        except Exception:
            return p

    for idx, seg in enumerate(segments_data):
        if not isinstance(seg, dict):
            continue
        if seg.get("merged_into") is not None or seg.get("merged_into_id"):
            skipped += 1
            continue
        if seg.get("tts_blocked") or seg.get("skip_tts"):
            skipped += 1
            continue

        if not segment_needs_audio_repair(seg, resolve_path=_resolve):
            stamp_audio_presence(seg, resolve_path=_resolve)
            skipped += 1
            continue

        # Drop ghost paths so mux cannot pick size=0.
        for key in ("file", "tts_file_path", "fitted_file"):
            raw = str(seg.get(key) or "").strip()
            if not raw:
                continue
            ok_g, size_g = audio_stat(_resolve(raw))
            if not ok_g or size_g < floor:
                seg[key] = None

        text = str(
            resolve_segment_text_for_tts(seg)
            or seg.get("final_tts_text")
            or seg.get("plain_text")
            or seg.get("text")
            or ""
        ).strip()
        if not text:
            seg["status"] = "failed"
            seg["tts_status"] = "failed"
            seg["tts_blocked"] = True
            stamp_audio_presence(seg, resolve_path=_resolve)
            failed += 1
            continue

        got = None
        got_ms = 0
        mk_controls = {
            "rate": seg.get("tts_rate") or info.get("mykyta_rate") or tts_rate,
            "pitch": seg.get("tts_pitch") or info.get("mykyta_pitch") or tts_pitch,
            "volume": seg.get("tts_volume") or info.get("mykyta_volume"),
            "length_scale": seg.get("tts_length_scale")
            or info.get("mykyta_length_scale"),
        }
        _tgt_lang = str(
            seg.get("target_lang")
            or info.get("target_lang")
            or info.get("lang")
            or "uk"
        )
        # Up to 2 full engine rounds (Mykyta → edge-offline uk-UA).
        for _attempt in range(2):
            if got:
                break
            for eid in engines_try:
                try:
                    rr = _regen_segment_tts(
                        text,
                        voice=str(seg.get("voice") or voice or ""),
                        work_dir=work_root,
                        tts_rate=tts_rate if tts_rate is not None else (
                            str(mk_controls["rate"])
                            if mk_controls["rate"] is not None
                            else None
                        ),
                        tts_pitch=tts_pitch if tts_pitch is not None else (
                            str(mk_controls["pitch"])
                            if mk_controls["pitch"] is not None
                            else None
                        ),
                        length_scale=mk_controls.get("length_scale"),
                        volume=mk_controls.get("volume"),
                        mykyta_controls=mk_controls,
                        task_id=task_id,
                        segment_index=idx,
                        segment_id=str(seg.get("segment_id") or ""),
                        engine_id=eid,
                        target_lang=_tgt_lang,
                        stamp_seg=seg,
                    )
                    if isinstance(rr, tuple):
                        nf, nms = rr[0], int(rr[1] or 0)
                    else:
                        nf, nms = rr, 0
                    if not nf:
                        continue
                    abs_p = _resolve(str(nf))
                    try:
                        _assert_audio_file(abs_p, min_bytes=floor)
                    except FileNotFoundError:
                        logger.warning(
                            "Task %s: repair TTS idx=%s engine=%s tiny/missing file",
                            task_id,
                            idx,
                            eid,
                        )
                        continue
                    got, got_ms = nf, nms
                    break
                except Exception as exc:
                    logger.warning(
                        "Task %s: repair TTS idx=%s engine=%s attempt=%s failed: %s",
                        task_id,
                        idx,
                        eid,
                        _attempt + 1,
                        exc,
                    )

        if not got:
            # Silence pad — keep mux length; never silently drop the ending.
            pad_ms = _slot_duration_ms_for_pad(seg, idx)
            try:
                pad_path, pad_ms = _write_silence_pad_for_segment(
                    work_dir=work_root,
                    task_id=task_id,
                    idx=idx,
                    segment_id=str(seg.get("segment_id") or ""),
                    duration_ms=pad_ms,
                )
                _assert_audio_file(pad_path, min_bytes=floor)
                seg["file"] = pad_path
                seg["tts_file_path"] = pad_path
                seg["playback_duration"] = pad_ms
                seg["tts_ms"] = pad_ms
                seg["actual_duration_ms"] = pad_ms
                seg["final_tts_duration_ms"] = pad_ms
                seg["status"] = "silence_pad"
                seg["tts_status"] = "silence_pad"
                seg["needs_re_tts"] = False
                seg["silence_pad"] = True
                seg["audio_padded"] = True
                seg["pad_reason"] = "missing_tts_after_repair"
                seg["duration_control_used"] = "soft_pad"
                warn = {
                    "index": idx,
                    "code": "silence_pad_fallback",
                    "slot_ms": pad_ms,
                    "message": "TTS failed after 2 attempts — silence pad used",
                }
                warnings.append(warn)
                meta = dict(seg.get("stage23") or {})
                meta["silence_pad"] = True
                meta["audio_padded"] = True
                meta["pad_reason"] = "missing_tts_after_repair"
                meta["silence_pad_ms"] = pad_ms
                meta["duration_control_used"] = "soft_pad"
                seg["stage23"] = meta
                stamp_audio_presence(seg, resolve_path=_resolve)
                padded += 1
                logger.warning(
                    "Task %s: silence pad idx=%s ms=%s (TTS exhausted)",
                    task_id,
                    idx,
                    pad_ms,
                )
                continue
            except Exception as pad_exc:
                logger.error(
                    "Task %s: silence pad failed idx=%s: %s",
                    task_id,
                    idx,
                    pad_exc,
                )
                seg["needs_re_tts"] = True
                seg["status"] = "failed"
                seg["tts_status"] = "failed"
                seg["file"] = None
                seg["tts_file_path"] = None
                stamp_audio_presence(seg, resolve_path=_resolve)
                failed += 1
                continue

        # Prefer absolute path after successful regen.
        try:
            _got_abs = str(Path(_resolve(str(got))).resolve())
        except Exception:
            _got_abs = str(got)
        seg["file"] = _got_abs
        seg["tts_file_path"] = _got_abs
        seg["resolved_path"] = _got_abs
        seg["final_tts_text"] = text
        seg["status"] = "generated"
        seg["tts_status"] = "generated"
        seg["needs_re_tts"] = False
        seg.pop("silence_pad", None)
        seg.pop("audio_padded", None)
        if got_ms <= 0:
            try:
                got_ms = len(AudioSegment.from_file(str(_resolve(str(got)))))
            except Exception:
                got_ms = 0
        if got_ms > 0:
            seg["playback_duration"] = got_ms
            seg["tts_ms"] = got_ms
            seg["actual_duration_ms"] = got_ms
            seg["final_tts_duration_ms"] = got_ms
        try:
            info["identity_allow_rebind"] = True
            _commit_tts_group_result(
                segments_data,
                [idx],
                tts_text=text,
                audio_filename=str(got),
                task_info=info,
            )
        except Exception as exc:
            logger.debug("repair commit skipped idx=%s: %s", idx, exc)
        finally:
            info["identity_allow_rebind"] = False
        stamp_audio_presence(seg, resolve_path=_resolve)
        repaired += 1

    padded_indices = [
        int(w.get("index"))
        for w in warnings
        if isinstance(w, dict)
        and w.get("code") == "silence_pad_fallback"
        and w.get("index") is not None
    ]
    stats = {
        "repaired": repaired,
        "failed": failed,
        "skipped": skipped,
        "padded": padded,
        "padded_count": padded,
        "padded_indices": padded_indices,
        "warnings": warnings,
        "engines_tried": engines_try,
        "min_bytes": floor,
    }
    if info is not None and warnings:
        prev = list(info.get("audio_repair_warnings") or [])
        prev.extend(warnings)
        info["audio_repair_warnings"] = prev
    if info is not None and padded_indices:
        info["padded_indices"] = list(
            dict.fromkeys(list(info.get("padded_indices") or []) + padded_indices)
        )
        info["padded_count"] = len(info["padded_indices"])
        info["final_status"] = "ok_with_pads"
        info.pop("export_blocked_reason", None)
    if repaired or failed or padded:
        logger.info(
            "Task %s: missing-TTS repair repaired=%s padded=%s failed=%s",
            task_id,
            repaired,
            padded,
            failed,
        )
    return stats


def _assert_audio_file(path: str | Path, min_bytes: int = 1000) -> Path:
    """Hard gate: file must exist and be usable for mux."""
    from engines.pipeline_integrity.audio_presence import assert_audio_file

    return assert_audio_file(path, min_bytes=min_bytes)


def _assert_segments_audio_ready(
    segments_data: list,
    *,
    task_id: str | None = None,
    resolve_path=None,
    task_info: dict | None = None,
    voice: str | None = None,
    allow_repair: bool = True,
) -> dict:
    """Stage 23b pre-mux gate: every speakable segment must have real audio.

    Holes (no file / size<1000 / tts_ms==0 / needs_re_tts): re-TTS Mykyta→edge
    up to 2 attempts, re-assert. If still missing → silence pad + warning
    (do not abort mux / cut video end).
    """
    from engines.pipeline_integrity.audio_presence import (
        MIN_AUDIO_BYTES,
        audio_stat,
        resolve_segment_audio_path,
        segment_needs_audio_repair,
        stamp_audio_presence,
    )
    from engines.pipeline_integrity.tts_segment_fields import resolve_segment_text_for_tts
    from engines.tts_backends import normalize_backend_name

    info = task_info or {}

    def _resolve(p: str) -> str:
        if resolve_path:
            try:
                return str(resolve_path(p) or p)
            except Exception:
                return p
        try:
            return str(_resolve_segment_audio_path(p) or p)
        except Exception:
            return p

    missing: list[int] = []
    repaired = 0
    padded = 0
    warnings: list[dict] = []
    # Stage 29 §B1 — assert pads/regens MUST land under session_dir/closed_loop/<task_id>/
    # (same tree as repair/soft-pad). Prior Stage 28 leftover used bare
    # `_artifacts_dir()` without task_info → pads invisible to census.
    _assert_root: Path | None = None
    try:
        _raw_sd_assert = info.get("session_dir") if isinstance(info, dict) else None
        if _raw_sd_assert:
            _assert_root = Path(str(_raw_sd_assert)).resolve()
    except Exception:
        _assert_root = None
    if _assert_root is None:
        try:
            _assert_root = _artifacts_dir(info).resolve()
        except Exception:
            _assert_root = _artifacts_dir(info)
    if isinstance(info, dict):
        info["session_dir"] = str(_assert_root)
    _tid_assert = (
        str(task_id or (info.get("task_id") if isinstance(info, dict) else "") or "assert").strip()
        or "assert"
    )
    work_root = (_assert_root / "closed_loop" / _tid_assert).resolve()
    work_root.mkdir(parents=True, exist_ok=True)

    primary = normalize_backend_name(
        info.get("tts_engine") or info.get("tts_backend") or "tts_uk"
    )
    _tgt_assert = str(info.get("target_lang") or info.get("lang") or "uk").split("-")[
        0
    ].lower()
    if _tgt_assert == "uk":
        engines_try = ["tts_uk", "edge-offline"]
    else:
        engines_try = [primary]
        if primary != "edge-offline":
            engines_try.append("edge-offline")
        if "tts_uk" not in engines_try:
            engines_try.insert(0, "tts_uk")

    # Stage 40 — census-only: walk every non-merged row from absolute disk
    # paths. Re-TTS belongs in `_repair_missing_tts_files` (step 1); pads
    # belong in soft-pad / last-resort (steps 2–3). This gate only reports.
    if not allow_repair:
        for idx, seg in enumerate(segments_data or []):
            if not isinstance(seg, dict):
                continue
            if seg.get("merged_into") is not None or seg.get("merged_into_id"):
                continue
            path = resolve_segment_audio_path(seg, resolve_path=_resolve)
            try:
                abs_p = str(Path(path).resolve()) if path else ""
            except OSError:
                abs_p = str(path or "")
            if abs_p:
                seg["resolved_path"] = abs_p
                if not str(seg.get("file") or "").strip():
                    seg["file"] = abs_p
            ok, size = audio_stat(abs_p)
            stamp_audio_presence(seg, resolve_path=_resolve)
            if not ok or size < MIN_AUDIO_BYTES:
                missing.append(idx)
                logger.error(
                    "Task %s: census-only audio missing idx=%s path=%s size=%s",
                    task_id,
                    idx,
                    abs_p,
                    size,
                )
        result = {
            "missing_indices": missing,
            "missing_count": len(missing),
            "repaired": 0,
            "padded": 0,
            "padded_count": int(info.get("padded_count") or 0),
            "padded_indices": list(info.get("padded_indices") or []),
            "warnings": warnings,
            "ok": True,
            "final_status": (
                "ok_with_pads"
                if int(info.get("padded_count") or 0) > 0
                else ("needs_soft_pad" if missing else "ok")
            ),
        }
        return result

    for idx, seg in enumerate(segments_data or []):
        if not isinstance(seg, dict):
            continue
        if seg.get("merged_into") is not None or seg.get("merged_into_id"):
            continue
        if seg.get("tts_blocked") or seg.get("skip_tts"):
            continue
        text = str(
            resolve_segment_text_for_tts(seg)
            or seg.get("final_tts_text")
            or seg.get("text")
            or ""
        ).strip()
        if not text:
            continue

        needs = segment_needs_audio_repair(seg, resolve_path=_resolve)
        path = resolve_segment_audio_path(seg, resolve_path=_resolve)
        try:
            if path and not needs:
                _assert_audio_file(path, min_bytes=MIN_AUDIO_BYTES)
                # Enforce non-zero tts_ms in final JSON.
                if int(seg.get("tts_ms") or seg.get("playback_duration") or 0) <= 0:
                    try:
                        seg["tts_ms"] = len(AudioSegment.from_file(str(_resolve(path))))
                        seg["playback_duration"] = seg["tts_ms"]
                    except Exception:
                        raise FileNotFoundError("tts_ms==0")
                stamp_audio_presence(seg, resolve_path=_resolve)
                continue
            raise FileNotFoundError("Audio hole — needs re-TTS")
        except FileNotFoundError:
            pass

        if allow_repair:
            mk_controls = {
                "rate": seg.get("tts_rate") or info.get("mykyta_rate") or info.get("tts_rate"),
                "pitch": seg.get("tts_pitch") or info.get("mykyta_pitch") or info.get("tts_pitch"),
                "volume": seg.get("tts_volume") or info.get("mykyta_volume"),
                "length_scale": seg.get("tts_length_scale")
                or info.get("mykyta_length_scale"),
            }
            got = None
            got_ms = 0
            for _attempt in range(2):
                if got:
                    break
                for engine_id in engines_try:
                    try:
                        rr = _regen_segment_tts(
                            text,
                            voice=str(
                                seg.get("voice") or voice or info.get("voice") or ""
                            ),
                            work_dir=work_root,
                            tts_rate=(
                                str(mk_controls["rate"])
                                if mk_controls["rate"] is not None
                                else None
                            ),
                            tts_pitch=(
                                str(mk_controls["pitch"])
                                if mk_controls["pitch"] is not None
                                else None
                            ),
                            length_scale=mk_controls.get("length_scale"),
                            volume=mk_controls.get("volume"),
                            mykyta_controls=mk_controls,
                            task_id=task_id,
                            segment_index=idx,
                            segment_id=str(seg.get("segment_id") or ""),
                            engine_id=engine_id,
                            target_lang=str(
                                seg.get("target_lang")
                                or info.get("target_lang")
                                or info.get("lang")
                                or "uk"
                            ),
                            stamp_seg=seg,
                        )
                        if isinstance(rr, tuple):
                            nf, nms = rr[0], int(rr[1] or 0)
                        else:
                            nf, nms = rr, 0
                        if not nf:
                            continue
                        abs_p = _resolve(str(nf))
                        _assert_audio_file(abs_p, min_bytes=MIN_AUDIO_BYTES)
                        got, got_ms = nf, nms
                        break
                    except Exception as exc:
                        logger.error(
                            "Task %s: assert re-TTS idx=%s engine=%s attempt=%s: %s",
                            task_id,
                            idx,
                            engine_id,
                            _attempt + 1,
                            exc,
                        )
            if got:
                seg["file"] = got
                seg["tts_file_path"] = got
                seg["needs_re_tts"] = False
                seg["status"] = "generated"
                seg["tts_status"] = "generated"
                if got_ms <= 0:
                    try:
                        got_ms = len(AudioSegment.from_file(str(_resolve(str(got)))))
                    except Exception:
                        got_ms = 0
                if got_ms > 0:
                    seg["playback_duration"] = got_ms
                    seg["tts_ms"] = got_ms
                    seg["actual_duration_ms"] = got_ms
                    seg["final_tts_duration_ms"] = got_ms
                stamp_audio_presence(seg, resolve_path=_resolve)
                repaired += 1
                continue

            # Silence pad fallback — keep video end intact.
            try:
                pad_ms = _slot_duration_ms_for_pad(seg, idx)
                pad_path, pad_ms = _write_silence_pad_for_segment(
                    work_dir=work_root,
                    task_id=task_id,
                    idx=idx,
                    segment_id=str(seg.get("segment_id") or ""),
                    duration_ms=pad_ms,
                )
                _assert_audio_file(pad_path, min_bytes=MIN_AUDIO_BYTES)
                try:
                    abs_pad = str(Path(pad_path).resolve())
                except OSError:
                    abs_pad = str(pad_path)
                seg["file"] = abs_pad
                seg["tts_file_path"] = abs_pad
                seg["fitted_file"] = abs_pad
                seg["resolved_path"] = abs_pad
                seg["playback_duration"] = pad_ms
                seg["tts_ms"] = pad_ms
                seg["actual_duration_ms"] = pad_ms
                seg["final_tts_duration_ms"] = pad_ms
                seg["status"] = "silence_pad"
                seg["tts_status"] = "silence_pad"
                seg["needs_re_tts"] = False
                seg["silence_pad"] = True
                seg["audio_padded"] = True
                seg["pad_reason"] = "missing_tts_after_repair"
                seg["duration_control_used"] = "soft_pad"
                warn = {
                    "index": idx,
                    "code": "silence_pad_fallback",
                    "slot_ms": pad_ms,
                    "message": "TTS failed after 2 attempts — silence pad used",
                }
                warnings.append(warn)
                meta = dict(seg.get("stage23") or {})
                meta["silence_pad"] = True
                meta["audio_padded"] = True
                meta["pad_reason"] = "missing_tts_after_repair"
                meta["silence_pad_ms"] = pad_ms
                seg["stage23"] = meta
                stamp_audio_presence(seg, resolve_path=_resolve)
                padded += 1
                continue
            except Exception as pad_exc:
                logger.error(
                    "Task %s: silence pad failed idx=%s: %s",
                    task_id,
                    idx,
                    pad_exc,
                )

        path = resolve_segment_audio_path(seg, resolve_path=_resolve)
        ok, size = audio_stat(path)
        stamp_audio_presence(seg, resolve_path=_resolve)
        if not ok or int(seg.get("tts_ms") or 0) <= 0:
            missing.append(idx)
            seg["needs_re_tts"] = True
            logger.error(
                "Task %s: audio still missing idx=%s path=%s size=%s tts_ms=%s",
                task_id,
                idx,
                path,
                size,
                seg.get("tts_ms"),
            )
    padded_indices = [
        int(w.get("index"))
        for w in warnings
        if isinstance(w, dict) and w.get("code") == "silence_pad_fallback"
        and w.get("index") is not None
    ]
    result = {
        "missing_indices": missing,
        "missing_count": len(missing),
        "repaired": repaired,
        "padded": padded,
        "padded_count": padded,
        "padded_indices": padded_indices,
        "warnings": warnings,
        # Soft-ok when only pads remain; residual missing is handled by
        # _soft_pad_missing_segments before mux (never EXPORT_BLOCKED).
        "ok": True,
    }
    if info is not None and warnings:
        prev = list(info.get("audio_repair_warnings") or [])
        prev.extend(warnings)
        info["audio_repair_warnings"] = prev
        if padded_indices:
            info["padded_indices"] = list(
                dict.fromkeys(list(info.get("padded_indices") or []) + padded_indices)
            )
            info["padded_count"] = len(info["padded_indices"])
    if padded:
        result["final_status"] = "ok_with_pads"
        if info is not None:
            info["final_status"] = "ok_with_pads"
            info.pop("export_blocked_reason", None)
    elif missing:
        # Residual holes — still soft; outer soft-pad will fill before mux.
        result["final_status"] = "needs_soft_pad"
        result["ok"] = False  # signal outer pad step; does NOT block mux
    else:
        result["final_status"] = "ok"
        if info is not None and info.get("final_status") in (
            None,
            "",
            "audio_missing_fatal",
        ):
            info["final_status"] = "ok"
            info.pop("export_blocked_reason", None)
    return result


def _prepare_segments_audio_before_mux(
    segments_data: list,
    *,
    task_info: dict | None,
    task_id: str | None,
    timing_map: list | None = None,
    voice: str | None = None,
    resolve_path=None,
) -> dict:
    """Single pre-mux order for Simple and main (Stage 40).

    1) repair missing TTS
    2) soft-pad every hole
    3) last-resort stdlib-wave pad
    4) census from absolute disk paths
    5) if still missing → soft-pad + last-resort again
    6) sync audio_missing / padded_count / final_status
    Never abort mux because of pads. final_status is ok | ok_with_pads.
    """
    info = task_info if isinstance(task_info, dict) else {}
    segs = list(segments_data or [])

    def _mux_resolve(p: str) -> str:
        if resolve_path:
            try:
                return str(resolve_path(p) or p)
            except Exception:
                return p
        try:
            return str(_resolve_segment_audio_path(p) or p)
        except Exception:
            return p

    voice0 = str(
        voice
        or info.get("voice")
        or info.get("pipeline_voice")
        or info.get("tts_voice")
        or ""
    )

    repair_stats: dict = {}
    try:
        repair_stats = _repair_missing_tts_files(
            segs,
            voice=voice0,
            task_info=info,
            task_id=task_id,
            tts_rate=info.get("tts_rate"),
            tts_pitch=info.get("tts_pitch"),
            resolve_path=_mux_resolve,
        )
        info["stage23b_pre_mux_repair"] = repair_stats
    except Exception as _rep_exc:
        logger.warning("Task %s: pre-mux audio repair failed: %s", task_id, _rep_exc)
        repair_stats = {}

    pad_stats: dict = {}
    try:
        pad_stats = _soft_pad_missing_segments(
            segs,
            task_info=info,
            task_id=task_id,
            timing_map=timing_map,
            resolve_path=_mux_resolve,
        )
    except Exception as _pad_exc:
        logger.error(
            "Task %s: soft-pad failed: %s (last-resort still runs)",
            task_id,
            _pad_exc,
        )
        pad_stats = {}

    lr_stats: dict = {}
    try:
        lr_stats = _last_resort_pad_missing_segments(
            segs,
            task_info=info,
            task_id=task_id,
            timing_map=timing_map,
            resolve_path=_mux_resolve,
        )
    except Exception as _lr_exc:
        logger.error(
            "Task %s: last-resort pad loop failed: %s (mux still continues)",
            task_id,
            _lr_exc,
        )
        lr_stats = {}

    try:
        _sd = info.get("session_dir") or info.get("artifacts_dir")
        if _sd:
            try:
                info["session_dir"] = str(Path(str(_sd)).resolve())
                _sd = info["session_dir"]
            except OSError:
                pass
        _absolutize_segment_audio_paths(segs, _sd, task_id=task_id)
    except Exception:
        pass

    try:
        from engines.oss_production import canonicalize_session_artifacts

        canonicalize_session_artifacts(
            segs,
            info.get("session_dir"),
            task_info=info,
            resolve_path=_mux_resolve,
        )
    except Exception as _oss_exc:
        logger.debug("Task %s: oss segs canonicalize skipped: %s", task_id, _oss_exc)

    gate = _assert_segments_audio_ready(
        segs,
        task_id=task_id,
        resolve_path=_mux_resolve,
        task_info=info,
        voice=voice0,
        allow_repair=False,
    )
    gate = dict(gate or {})
    gate["ok"] = True
    gate["padded_indices"] = list(
        dict.fromkeys(
            list(pad_stats.get("padded_indices") or [])
            + list(lr_stats.get("padded_indices") or [])
            + list(gate.get("padded_indices") or [])
        )
    )
    gate["padded_count"] = len(gate["padded_indices"])
    gate["missing_before_pad"] = list(pad_stats.get("missing_before") or [])
    if gate["padded_count"] > 0 or int(repair_stats.get("padded") or 0) > 0:
        gate["final_status"] = "ok_with_pads"
        info["final_status"] = "ok_with_pads"
    else:
        gate["final_status"] = "ok"

    from_segs = [
        int(s.get("index") if s.get("index") is not None else i)
        for i, s in enumerate(segs)
        if isinstance(s, dict) and (s.get("audio_padded") or s.get("silence_pad"))
    ]
    info["padded_indices"] = list(
        dict.fromkeys(
            list(info.get("padded_indices") or [])
            + list(gate["padded_indices"])
            + from_segs
        )
    )
    info["padded_count"] = len(info["padded_indices"])
    if info["padded_count"] > 0:
        info["final_status"] = "ok_with_pads"
        gate["final_status"] = "ok_with_pads"
        gate["padded_count"] = info["padded_count"]
        gate["padded_indices"] = list(info["padded_indices"])
    elif info.get("final_status") in (
        None,
        "",
        "audio_missing_fatal",
        "silence_pad_used",
        "degraded",
    ):
        info["final_status"] = "ok"
    info.pop("export_blocked_reason", None)
    info["stage23b_audio_gate"] = gate
    info["segments_data"] = list(segs)

    census: dict = {}
    try:
        from engines.segment_timing_qa import _build_openddf_tts_pipeline_block

        census = _build_openddf_tts_pipeline_block(info, segments_data=segs)
        _sync_pad_census_fields(info, census)
        if int(census.get("audio_missing") or 0) > 0:
            _log_census_missing_after_pad(
                task_id=task_id,
                segments_data=segs,
                census=census,
                session_dir=info.get("session_dir"),
            )
            _soft_pad_missing_segments(
                segs,
                task_info=info,
                task_id=task_id,
                timing_map=timing_map,
                resolve_path=_mux_resolve,
            )
            try:
                _last_resort_pad_missing_segments(
                    segs,
                    task_info=info,
                    task_id=task_id,
                    timing_map=timing_map,
                    resolve_path=_mux_resolve,
                )
            except Exception:
                pass
            try:
                _absolutize_segment_audio_paths(
                    segs, info.get("session_dir"), task_id=task_id
                )
            except Exception:
                pass
            try:
                from engines.oss_production import canonicalize_session_artifacts as _canon2

                _canon2(
                    segs,
                    info.get("session_dir"),
                    task_info=info,
                    resolve_path=_mux_resolve,
                )
            except Exception:
                pass
            info["segments_data"] = list(segs)
            census = _build_openddf_tts_pipeline_block(info, segments_data=segs)
            _sync_pad_census_fields(info, census)
            if int(census.get("audio_missing") or 0) > 0:
                _log_census_missing_after_pad(
                    task_id=task_id,
                    segments_data=segs,
                    census=census,
                    session_dir=info.get("session_dir"),
                )
        info["tts_pipeline"] = census
        if info.get("final_status") in ("audio_missing_fatal", "degraded"):
            info["final_status"] = (
                "ok_with_pads" if int(info.get("padded_count") or 0) > 0 else "ok"
            )
        if int((info.get("tts_pipeline") or {}).get("audio_missing") or 0) == 0:
            if info.get("final_status") == "degraded":
                info["final_status"] = (
                    "ok_with_pads" if int(info.get("padded_count") or 0) > 0 else "ok"
                )
        info.pop("export_blocked_reason", None)
    except Exception as _cen_exc:
        logger.debug("Task %s: pre-mux census skipped: %s", task_id, _cen_exc)
        census = dict(info.get("tts_pipeline") or {})

    if int(pad_stats.get("padded_count") or 0) > 0 or int(
        lr_stats.get("padded_count") or 0
    ) > 0:
        logger.warning(
            "Task %s: pre-mux pad soft=%s last_resort=%s — mux continues",
            task_id,
            pad_stats.get("padded_count"),
            lr_stats.get("padded_count"),
        )
    return {
        "ok": True,
        "repair": repair_stats,
        "soft_pad": pad_stats,
        "last_resort": lr_stats,
        "gate": gate,
        "census": census or dict(info.get("tts_pipeline") or {}),
        "final_status": str(info.get("final_status") or "ok"),
    }


def _post_tts_timing_qa(
    task_id: str,
    segments_data: list,
    timing_map: list,
    task_info: dict,
    *,
    voice: str,
    target_lang: str,
    src_lang: str,
    tts_rate: str | None = None,
    tts_pitch: str | None = None,
) -> tuple[list, dict]:
    """
    Closed Loop Timing: TTS → measure → pause trim → rewrite → TTS → measure.
    Decisions use actual audio duration only — never predicted estimates.
    """
    from engines.closed_loop_timing import (
        build_timing_report,
        run_closed_loop_timing,
        validate_timeline,
        write_timing_report,
    )
    from engines.segment_timing_qa import normalize_timing_map_joints

    normalized_map, joint_fixes = normalize_timing_map_joints(timing_map)
    if joint_fixes:
        logger.info(
            "Task %s: timing joint fixes applied: %d",
            task_id,
            len(joint_fixes),
        )
        task_info["timing_joint_fixes"] = joint_fixes

    def _regen(text, **kwargs):
        from engines.pipeline_segment_watchdog import run_segment_bounded

        seg_idx = int(kwargs.get("segment_index") or 0)
        work_root = _artifacts_dir() / "closed_loop" / task_id
        work_root.mkdir(parents=True, exist_ok=True)

        def _do_regen():
            return _regen_segment_tts(
                text,
                voice=kwargs.get("voice") or voice,
                work_dir=work_root,
                tts_rate=kwargs.get("tts_rate") or tts_rate,
                tts_pitch=kwargs.get("tts_pitch") or tts_pitch,
                task_id=kwargs.get("task_id") or task_id,
                segment_index=kwargs.get("segment_index"),
                segment_id=kwargs.get("segment_id") or "",
                engine_id=kwargs.get("engine_id")
                or task_info.get("tts_engine")
                or task_info.get("tts_engine_id")
                or "edge-offline",
                length_scale=kwargs.get("length_scale"),
                volume=kwargs.get("volume"),
                mykyta_controls=kwargs.get("mykyta_controls"),
                target_lang=kwargs.get("target_lang")
                or task_info.get("target_lang")
                or target_lang,
            )

        watch = run_segment_bounded(
            task_id=task_id or "",
            phase="closed_loop_regen",
            segment_index=seg_idx,
            stage="closed_loop_regen",
            fn=_do_regen,
            fallback=lambda: (None, 0),
        )
        return watch.value

    def _commit(segs, indices, *, tts_text, audio_filename):
        # Closed-loop regen intentionally rebinds TTS for the same UUID.
        if isinstance(task_info, dict):
            task_info["identity_allow_rebind"] = True
        try:
            _commit_tts_group_result(
                segments_data,
                list(indices or []),
                tts_text=tts_text,
                audio_filename=audio_filename,
                task_info=task_info,
            )
        finally:
            if isinstance(task_info, dict):
                task_info["identity_allow_rebind"] = False

    work_root = _artifacts_dir() / "closed_loop" / task_id
    work_root.mkdir(parents=True, exist_ok=True)

    def _resolve_path(p: str) -> str:
        try:
            return str(_resolve_segment_audio_path(p) or p)
        except Exception:
            return p

    try:
        _vid_ms = int(task_info.get("target_duration_ms") or 0)
        if _vid_ms <= 0:
            try:
                _vid_ms = int(
                    _video_duration_ms(str(task_info.get("video_path") or "")) or 0
                )
            except Exception:
                _vid_ms = 0
        retry_stats = run_closed_loop_timing(
            segments_data,
            normalized_map,
            source_segments=task_info.get("source_segments") or [],
            voice=voice,
            target_lang=target_lang,
            src_lang=src_lang,
            work_dir=work_root,
            regen_fn=_regen,
            commit_fn=_commit,
            audits=task_info.get("translation_audits") or [],
            task_id=task_id,
            tts_rate=tts_rate,
            tts_pitch=tts_pitch,
            max_iterations=_post_tts_max_retries(task_info),
            resolve_path=_resolve_path,
            video_duration_ms=_vid_ms or None,
        )
    except UnboundLocalError as exc:
        # Belt-and-suspenders: Stage23 constant scoping must never kill the dub job.
        logger.error(
            "Task %s: Stage23 UnboundLocalError suppressed in closed_loop: %s",
            task_id,
            exc,
        )
        retry_stats = {
            "error": "stage23_unboundlocal_suppressed",
            "message": str(exc),
        }

    # Stage 22: force-split may keep children without audio — repair before mix/handoff.
    repair_stats = _repair_missing_tts_files(
        segments_data,
        voice=voice,
        task_info=task_info,
        task_id=task_id,
        tts_rate=tts_rate,
        tts_pitch=tts_pitch,
    )
    retry_stats["missing_tts_repair"] = repair_stats

    # Re-run closed loop only on timeline problem segments (no cascade shift).
    timeline = validate_timeline(segments_data, normalized_map)
    problem = list(timeline.get("problem_indices") or [])
    if problem:
        logger.info(
            "Task %s: timeline validation found %d problem segment(s) — re-optimizing",
            task_id,
            len(problem),
        )
        for idx in problem:
            if idx < 0 or idx >= len(segments_data):
                continue
            from engines.closed_loop_timing import run_closed_loop_segment

            src_segs = task_info.get("source_segments") or []
            audits = task_info.get("translation_audits") or []
            audit_by = {int(a.get("index", -1)): a for a in audits}
            try:
                run_closed_loop_segment(
                    segments_data[idx],
                    idx,
                    normalized_map,
                    source_hint=src_segs[idx] if idx < len(src_segs) else "",
                    target_lang=target_lang,
                    src_lang=src_lang,
                    voice=voice,
                    work_dir=work_root,
                    regen_fn=_regen,
                    commit_fn=_commit,
                    audit=audit_by.get(idx),
                    max_iterations=_post_tts_max_retries(task_info),
                    tts_rate=tts_rate,
                    tts_pitch=tts_pitch,
                    task_id=task_id,
                    resolve_path=_resolve_path,
                )
            except UnboundLocalError as exc:
                logger.error(
                    "Task %s: Stage23 UnboundLocalError suppressed on problem seg=%s: %s",
                    task_id,
                    idx,
                    exc,
                )
            except Exception as exc:
                logger.exception(
                    "Task %s: closed_loop re-optimize failed seg=%s: %s",
                    task_id,
                    idx,
                    exc,
                )
        timeline = validate_timeline(segments_data, normalized_map)

    report = build_timing_report(
        segments_data,
        normalized_map,
        closed_loop_stats=retry_stats,
        timeline_validation=timeline,
    )
    try:
        report_path = write_timing_report(report, app_dir=APP_DIR, task_id=task_id)
        task_info["timing_report_path"] = str(report_path)
    except Exception as rep_exc:
        logger.debug("timing_report write skipped: %s", rep_exc)
    task_info["timing_report"] = report
    task_info["timeline_validation"] = timeline
    task_info["post_tts_qa"] = retry_stats
    # Persist list mutations from Adaptive Seg post-TTS resegment (TZ §11)
    if int(retry_stats.get("resegmented") or 0) > 0:
        task_info["timing_map_backup"] = copy.deepcopy(normalized_map)
        task_info["adaptive_resegment_post_tts"] = {
            "resegmented": int(retry_stats.get("resegmented") or 0),
            "segment_count": len(segments_data),
        }

    try:
        from engines.translation_adapt import get_llm_calls, get_llm_status

        task_info["llm_calls"] = get_llm_calls()
        task_info["llm_status"] = get_llm_status()
    except Exception:
        pass
    return normalized_map, retry_stats


def _sync_tts_audits_from_groups(
    audits: list,
    segments_data: list,
    tts_groups: list,
    *,
    trace,
    prosody_only: bool,
) -> None:
    """Update translation audit rows from TTS groups — no segment.plain_text mutation.

    Multi-member groups: never stamp the merged group blob onto every member.
    Each member audit gets its own segment plain_text; only the head may carry
    group-level SSML / combined plain when the member text is empty.
    """
    from engines.pipeline_integrity.tts_segment_fields import resolve_tts_input_text

    audit_by_idx = {int(a.get("index", -1)): a for a in audits}

    def _strip_ssml(value: str) -> str:
        out = str(value or "").strip()
        if out.lstrip().startswith("<speak"):
            out = re.sub(r"<[^>]+>", " ", out)
            out = re.sub(r"[ \t]+", " ", out).strip()
        try:
            from engines.stress_marks import strip_stress_marks

            out = strip_stress_marks(out)
        except Exception:
            pass
        return out

    for group in tts_groups:
        indices = [int(i) for i in (group.get("indices") or [])]
        if not indices:
            continue
        head = indices[0]
        tts_input = resolve_tts_input_text(group)
        tts_ssml = str(group.get("text") or "").strip()
        group_plain = _strip_ssml(
            str(
                group.get("plain_text")
                or tts_input
                or ""
            )
        )
        for idx in indices:
            row = audit_by_idx.get(idx)
            if not row or idx >= len(segments_data):
                continue
            seg = segments_data[idx] if isinstance(segments_data[idx], dict) else {}
            member_plain = _strip_ssml(
                str(
                    seg.get("plain_text")
                    or seg.get("tts_text")
                    or seg.get("translation_text")
                    or seg.get("text")
                    or ""
                )
            )
            # Non-head members keep their own text — never the merged group blob.
            if idx != head:
                plain = member_plain
            else:
                plain = member_plain or group_plain
            if not plain:
                continue
            if idx == head and tts_ssml.lstrip().startswith("<speak"):
                row["tts_ssml"] = tts_ssml
            row["tts_text"] = plain
            if prosody_only or tts_ssml.lstrip().startswith("<speak"):
                if plain and not plain.lstrip().startswith("<speak"):
                    row.setdefault("final_text", plain)
            if trace:
                trace.upsert_from_audit(row)


def _resolve_segment_audio_path(filename: str | None, task_info: dict | None = None) -> Path:
    from engines.dubbing_engine.session_adapter import resolve_session_audio

    return resolve_session_audio(filename, task_info=task_info, default_dir=OUTPUT_DIR)


def _log_tts_synthesis_requests(
    task_id: str,
    *,
    tts_groups: list,
    segments_data: list,
    voice: str,
    target_lang: str,
    provider: str,
) -> None:
    from engines.pipeline_integrity.tts_segment_fields import resolve_tts_input_text
    from engines.translation_stage_log import log_tts_request

    for g_idx, group in enumerate(tts_groups):
        text = resolve_tts_input_text(group)
        if not text:
            continue
        indices = group.get("indices") or []
        head_idx = int(indices[0]) if indices else 0
        seg_id = ""
        if 0 <= head_idx < len(segments_data):
            seg_id = str(segments_data[head_idx].get("segment_id") or "")
        log_tts_request(
            task_id,
            segment_index=head_idx,
            segment_id=seg_id or None,
            language_code=target_lang,
            voice_id=voice,
            provider=provider,
            text=text,
            group_index=g_idx,
        )


def _tts_context_for_segment(
    *,
    task_id: str,
    segment_id: str,
    segment_index: int,
    current: int,
    total: int,
    original_text: str,
    tts_text: str,
    voice: str,
    target_lang: str,
    tts_file_path: str | Path,
    engine_id: str | None = None,
) -> dict:
    eid = engine_id or "tts_uk"
    v = voice
    lang = str(target_lang or "uk").split("-")[0].lower()
    if lang == "uk":
        try:
            from engines.tts_lang_lock import force_uk_tts_identity

            ident = force_uk_tts_identity(
                target_lang="uk", engine_id=eid, voice=voice
            )
            eid = str(ident.get("engine_id") or eid)
            v = str(ident.get("voice") or voice)
            lang = "uk"
        except Exception:
            eid = "tts_uk"
            v = "mykyta"
            lang = "uk"
    return {
        "task_id": task_id,
        "segment_id": segment_id,
        "segment_index": segment_index,
        "current": current,
        "total": total,
        "original_text": original_text,
        "tts_text": tts_text,
        "voice": v,
        "language": lang,
        "target_lang": lang,
        "tts_language": lang,
        "tts_voice": v,
        "tts_backend": eid,
        "tts_file_path": str(tts_file_path),
        "engine_id": eid,
    }


def _snapshot_project_on_tts_failure(task_id: str) -> None:
    """Save studio session snapshot on TTS failure without stopping the task."""
    try:
        from api.studio_api import _save_session, build_session_from_auto_dub_task

        state = build_session_from_auto_dub_task(task_id)
        if state:
            state["task_status"] = "tts_partial_failure"
            _save_session(state)
            logger.info("Task %s: project snapshot saved after TTS failure", task_id)
    except Exception as exc:
        logger.warning("Task %s: TTS failure snapshot skipped: %s", task_id, exc)


def _record_tts_segment_failure(
    task_id: str,
    segments_data: list,
    indices: list[int],
    report,
    *,
    ui_lang: str = "ru",
) -> str:
    """Mark failed segments, persist report, fail-fast stop. Returns UI message."""
    from engines.dubbing_engine.pipeline_failure_diag import fail_pipeline, from_tts_failure_report
    from engines.dubbing_engine.tts_failure_diag import (
        TTSFailureReport,
        format_diagnostic_block,
        mark_segment_tts_failed,
        save_failure_report,
    )

    if isinstance(report, dict):
        fields = {f: report.get(f) for f in TTSFailureReport.__dataclass_fields__}
        if not fields.get("reason") and report.get("error_message"):
            fields["reason"] = report.get("error_message")
        fields["pipeline_state"] = "STOPPED"
        fields["stage"] = "TTS"
        report = TTSFailureReport(**fields)
    else:
        report.pipeline_state = "STOPPED"
        report.stage = "TTS"

    diag_block = format_diagnostic_block(report)

    for idx in indices:
        if 0 <= idx < len(segments_data):
            mark_segment_tts_failed(segments_data[idx], report)

    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        if task:
            info = task.setdefault("info", {})
            failures = info.setdefault("tts_failures", [])
            payload = report.to_dict()
            payload["diagnostic_block"] = diag_block
            failures.append(payload)
            info["segments_data"] = segments_data
            touch_task(task_id)

    save_failure_report(report, task_id=task_id)
    pipe_report = from_tts_failure_report(report, pipeline_state="STOPPED")
    return fail_pipeline(task_id, pipe_report.reason, report=pipe_report, ui_lang=ui_lang)


def _is_usable_tts_voice_id(voice_id: str | None) -> bool:
    """Reject silent/mock registry placeholders that would synthesize wrong audio."""
    vid = str(voice_id or "").strip()
    if not vid:
        return False
    low = vid.lower()
    if low in {"mock-default", "mock", "silent", "none", "null"}:
        return False
    if low.startswith("mock-") or "silent" in low:
        return False
    return True


def _pin_segments_to_deterministic_voices(
    segments_data: list,
    *,
    default_voice: str,
    preferred_voices: dict[str, str] | None = None,
    reason: str = "voice_plan_soft_fail",
) -> int:
    """Deterministic fallback: preferred → existing usable assigned → pipeline default.

    Never invent a new registry/mock voice on soft-fail. Returns how many segments
    were pinned/rewritten.
    """
    preferred = dict(preferred_voices or {})
    fallback = (
        default_voice
        if _is_usable_tts_voice_id(default_voice)
        else str(default_voice or "").strip()
    )
    pinned = 0
    for i, seg in enumerate(segments_data or []):
        if not isinstance(seg, dict) or seg.get("merged_into") is not None:
            continue
        if not str(seg.get("text") or "").strip():
            continue
        speaker = str(
            seg.get("speaker_uuid")
            or seg.get("speaker")
            or seg.get("speaker_id")
            or f"seg-{i}"
        )
        candidates = (
            preferred.get(speaker),
            seg.get("assigned_voice"),
            (seg.get("ai_voice") or {}).get("voice")
            if isinstance(seg.get("ai_voice"), dict)
            else None,
            (seg.get("ai_voice") or {}).get("voice_uuid")
            if isinstance(seg.get("ai_voice"), dict)
            else None,
            seg.get("voice"),
            fallback,
        )
        chosen = next(
            (str(c).strip() for c in candidates if _is_usable_tts_voice_id(c)),
            fallback,
        )
        prev = str(seg.get("assigned_voice") or "").strip()
        if prev != chosen:
            pinned += 1
        seg["assigned_voice"] = chosen
        seg["voice"] = chosen
        seg["voice_fallback_reason"] = reason
    return pinned


def _apply_voice_platform_assignments(
    task_id: str,
    segments_data: list,
    *,
    default_voice: str,
    target_lang: str,
    style_id: str = "Movie",
    preferred_voices: dict[str, str] | None = None,
) -> str:
    """Wire VoiceMemory / multi-speaker plans onto segments before TTS.

    Returns the primary pipeline voice (always ``default_voice``).
    Per-speaker voice is stored on segments as ``assigned_voice``.

    Soft-fail contract (documented):
    - Planning/import errors are logged at WARNING and do **not** abort TTS.
    - Fallback is deterministic: preferred_voices → prior usable assigned_voice →
      ``default_voice``. Mock/silent registry ids are never kept.
    - Task info gets ``voice_platform_plan.soft_fail`` + reason when fallback runs.
    """
    preferred: dict[str, str] = dict(preferred_voices or {})

    def _record_soft_fail(reason: str, *, pinned: int = 0) -> None:
        try:
            with STATE_LOCK:
                task = AUTO_TASKS.get(task_id)
                if not task:
                    return
                info = task.setdefault("info", {})
                info["voice_platform_plan"] = {
                    "soft_fail": True,
                    "reason": str(reason)[:400],
                    "fallback": "deterministic_default",
                    "default_voice": default_voice,
                    "pinned_segments": pinned,
                }
                info["segments_data"] = segments_data
                touch_task(task_id)
        except Exception:
            pass

    try:
        from engines.voice_platform import VoiceMemory, plan_project_voices
    except Exception as exc:
        logger.warning("Task %s: voice_platform import soft-fail: %s", task_id, exc)
        pinned = _pin_segments_to_deterministic_voices(
            segments_data,
            default_voice=default_voice,
            preferred_voices=preferred,
            reason="voice_platform_import_soft_fail",
        )
        _record_soft_fail(f"import: {exc}", pinned=pinned)
        return default_voice

    units: list[dict] = []
    for i, seg in enumerate(segments_data or []):
        if not isinstance(seg, dict) or seg.get("merged_into") is not None:
            continue
        speaker = str(
            seg.get("speaker_uuid")
            or seg.get("speaker")
            or seg.get("speaker_id")
            or f"seg-{i}"
        )
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        units.append(
            {
                "speech_uuid": str(seg.get("segment_id") or f"su-{i}"),
                "speaker_uuid": speaker,
                "text": text,
                "emotion": str(
                    (seg.get("tts_emotion") or {}).get("emotion")
                    or seg.get("emotion")
                    or "calm"
                ),
                "style": style_id,
                "language": target_lang,
            }
        )
        hint_voice = (
            str(seg.get("assigned_voice") or "").strip()
            or str((seg.get("ai_voice") or {}).get("voice") or "").strip()
            or str((seg.get("ai_voice") or {}).get("voice_uuid") or "").strip()
        )
        if _is_usable_tts_voice_id(hint_voice):
            preferred.setdefault(speaker, hint_voice)

    if not units:
        return default_voice

    mem = None
    mem_path = OUTPUT_DIR / "voice_memory" / f"{task_id[:32]}.json"
    try:
        if mem_path.is_file():
            mem = VoiceMemory.load(mem_path)
    except Exception:
        mem = None

    try:
        payload = plan_project_voices(
            units,
            project_id=task_id,
            style=style_id or "Movie",
            language=target_lang or "ru",
            preferred_voice=default_voice,
            preferred_voices=preferred,
            memory=mem,
        )
    except Exception as exc:
        logger.warning("Task %s: voice plan soft-fail: %s", task_id, exc)
        pinned = _pin_segments_to_deterministic_voices(
            segments_data,
            default_voice=default_voice,
            preferred_voices=preferred,
            reason="voice_plan_soft_fail",
        )
        _record_soft_fail(str(exc), pinned=pinned)
        return default_voice

    plans = payload.get("plans") or []
    voice_by_speech: dict[str, str] = {}
    voice_by_speaker: dict[str, str] = {}
    mock_replaced = 0
    for plan in plans:
        if not isinstance(plan, dict):
            continue
        # Edge/providers need external id (uk-UA-OstapNeural), not registry UUID.
        vu = str(
            plan.get("external_voice_id") or plan.get("voice_uuid") or ""
        ).strip()
        if not _is_usable_tts_voice_id(vu):
            mock_replaced += 1
            vu = preferred.get(str(plan.get("speaker_uuid") or "").strip()) or default_voice
            if not _is_usable_tts_voice_id(vu):
                continue
        su = str(plan.get("speech_uuid") or "").strip()
        sp = str(plan.get("speaker_uuid") or "").strip()
        if su:
            voice_by_speech[su] = vu
        if sp:
            voice_by_speaker[sp] = vu

    if mock_replaced:
        logger.warning(
            "Task %s: voice plan replaced %d mock/silent voice(s) with default/preferred",
            task_id,
            mock_replaced,
        )

    for i, seg in enumerate(segments_data or []):
        if not isinstance(seg, dict) or seg.get("merged_into") is not None:
            continue
        sid = str(seg.get("segment_id") or f"su-{i}")
        speaker = str(
            seg.get("speaker_uuid")
            or seg.get("speaker")
            or seg.get("speaker_id")
            or f"seg-{i}"
        )
        assigned = voice_by_speech.get(sid) or voice_by_speaker.get(speaker)
        if assigned and _is_usable_tts_voice_id(assigned):
            seg["assigned_voice"] = assigned
            seg.setdefault("voice", assigned)
            seg.pop("voice_fallback_reason", None)

    try:
        mem_dict = payload.get("memory")
        if isinstance(mem_dict, dict):
            mem_path.parent.mkdir(parents=True, exist_ok=True)
            mem_path.write_text(
                json.dumps(mem_dict, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        with STATE_LOCK:
            task = AUTO_TASKS.get(task_id)
            if task:
                info = task.setdefault("info", {})
                info["voice_platform_plan"] = {
                    "plans": len(plans),
                    "speakers": len(voice_by_speaker),
                    "memory_path": str(mem_path),
                    "consistency_issues": payload.get("consistency_issues") or [],
                    "soft_fail": False,
                    "mock_replaced": mock_replaced,
                }
                info["segments_data"] = segments_data
                touch_task(task_id)
    except Exception as exc:
        logger.debug("Task %s: voice plan persist soft-fail: %s", task_id, exc)

    # Keep pipeline default voice; per-segment assigned_voice is used at TTS time.
    return default_voice


def _segment_tts_voice(seg: dict | None, default_voice: str) -> str:
    if not isinstance(seg, dict):
        return default_voice
    for key in ("assigned_voice", "voice"):
        val = str(seg.get(key) or "").strip()
        if val:
            return val
    ai = seg.get("ai_voice") if isinstance(seg.get("ai_voice"), dict) else {}
    for key in ("voice", "voice_uuid", "voice_id"):
        val = str(ai.get(key) or "").strip()
        if val:
            return val
    return default_voice


def _mark_tts_segment_skipped(
    task_id: str,
    segments_data: list,
    indices: list[int],
    report,
    *,
    reason: str = "skipped",
) -> None:
    """Mark segment TTS failure but continue pipeline (do not fail-fast)."""
    from engines.dubbing_engine.tts_failure_diag import (
        TTSFailureReport,
        mark_segment_tts_failed,
        save_failure_report,
    )

    try:
        if isinstance(report, dict):
            report_obj = TTSFailureReport.from_partial_dict(report)
        else:
            report_obj = report
            report_obj.pipeline_state = "PARTIAL"
            report_obj.stage = "TTS"

        for idx in indices:
            if 0 <= idx < len(segments_data):
                mark_segment_tts_failed(segments_data[idx], report_obj)

        with STATE_LOCK:
            task = AUTO_TASKS.get(task_id)
            if task:
                info = task.setdefault("info", {})
                failures = info.setdefault("tts_failures", [])
                payload = report_obj.to_dict()
                payload["skipped_continue"] = True
                payload["skip_reason"] = reason
                failures.append(payload)
                info["segments_data"] = segments_data
                touch_task(task_id)

        try:
            save_failure_report(report_obj, task_id=task_id)
        except Exception as exc:
            logger.debug("Task %s: TTS skip report save failed: %s", task_id, exc)
    except Exception as exc:
        # Zip 8fadb9dd: skip handler TypeError aborted 31 successful TTS files.
        logger.error(
            "Task %s: TTS skip annotate failed (%s) indices=%s — continuing",
            task_id,
            exc,
            indices,
        )
        for idx in indices or []:
            if 0 <= idx < len(segments_data) and isinstance(segments_data[idx], dict):
                segments_data[idx]["skip_tts"] = True
                segments_data[idx]["tts_status"] = "failed"
                segments_data[idx]["file"] = None

    logger.warning(
        "Task %s: TTS segment skipped (%s) indices=%s — continuing pipeline",
        task_id,
        reason,
        indices,
    )


def _try_merge_neighbor_tts(
    segments_data: list,
    timing_map: list,
    idx: int,
    voice: str,
    tts_files: list,
    tts_rate: str | None = None,
    tts_pitch: str | None = None,
) -> bool:
    """Объединяет соседние реплики одной мысли в один TTS-блок."""
    import re

    if idx + 1 >= len(segments_data):
        return False
    head = segments_data[idx]
    nxt = segments_data[idx + 1]
    if nxt.get("merged_into") is not None or not head.get("text") or not nxt.get("text"):
        return False
    if re.search(r"[.!?…]\s*$", str(head.get("text") or "")):
        return False

    combined = f"{head['text'].strip()} {nxt['text'].strip()}".strip()
    if len(combined.split()) > 30:
        return False

    custom = head.get("tts_timing")
    if custom and len(custom) >= 2:
        start_ms = int(custom[0])
    elif idx < len(timing_map):
        start_ms, _ = _parse_timing(timing_map[idx])
    else:
        start_ms = 0

    if idx + 1 < len(timing_map):
        _, end_ms = _parse_timing(timing_map[idx + 1])
    else:
        _, end_ms = _parse_timing(timing_map[idx])

    new_file = _regen_segment_tts(combined, voice, tts_rate=tts_rate, tts_pitch=tts_pitch)
    if not new_file:
        return False

    old = head.get("file")
    if old:
        (_artifacts_dir() / Path(old).name).unlink(missing_ok=True)
    if nxt.get("file"):
        (_artifacts_dir() / Path(nxt["file"]).name).unlink(missing_ok=True)

    head["file"] = new_file
    head["text"] = combined
    head["tts_timing"] = [start_ms, end_ms]
    nxt["file"] = None
    from engines.pipeline_integrity.segment import new_segment_id as _new_sid

    head_sid = str(head.get("segment_id") or "").strip() or _new_sid()
    head["segment_id"] = head_sid
    head["segment_uuid"] = head.get("segment_uuid") or head_sid
    nxt_sid = str(nxt.get("segment_id") or "").strip()
    nxt["archived"] = True
    nxt["merged_into_id"] = head_sid
    nxt["merged_into"] = head_sid  # UUID, never list index
    nxt["parent_segment_id"] = nxt_sid or None
    tts_files.append(new_file)
    _audio_name = new_file[0] if isinstance(new_file, tuple) else new_file
    if _audio_name:
        head["file"] = _audio_name
        _identity_bind_after_regen(
            head,
            combined,
            _audio_name,
            segments_data=segments_data,
            stage="merge_neighbor_regen",
        )
    logger.info("Task merge neighbor TTS idx=%d+%d", idx, idx + 1)
    return True


def _style_params_from_info(info: dict | None) -> dict:
    from engines.dub_style_presets import get_dub_style, resolve_dub_style

    info = info or {}
    style_id = info.get("dub_style") or "modern"
    if info.get("style_allow_atempo") is not None:
        return {
            "reply_start_delay_ms": int(info.get("reply_start_delay_ms") or 0),
            "reply_start_delay_jitter_ms": int(info.get("reply_start_delay_jitter_ms") or 0),
            "max_atempo": float(info.get("style_max_atempo") or 1.18),
            "allow_atempo": bool(info.get("style_allow_atempo", True)),
            "voice_fx": info.get("style_voice_fx"),
            "prefer_semantic_adapt": bool(info.get("prefer_semantic_adapt")),
        }
    resolved = resolve_dub_style(style_id)
    return {
        "reply_start_delay_ms": int(resolved.get("reply_start_delay_ms") or 0),
        "reply_start_delay_jitter_ms": int(resolved.get("reply_start_delay_jitter_ms") or 0),
        "max_atempo": float(resolved.get("max_atempo") or 1.18),
        "allow_atempo": bool(resolved.get("allow_atempo", True)),
        "voice_fx": resolved.get("voice_fx"),
        "prefer_semantic_adapt": bool(resolved.get("prefer_semantic_adapt")),
    }


def _store_style_profile(info: dict, resolved_style: dict) -> None:
    info["dub_style"] = resolved_style.get("style_id")
    info["tts_rate"] = resolved_style.get("tts_rate")
    info["tts_pitch"] = resolved_style.get("tts_pitch")
    info["reply_start_delay_ms"] = resolved_style.get("reply_start_delay_ms")
    info["reply_start_delay_jitter_ms"] = resolved_style.get("reply_start_delay_jitter_ms")
    info["style_max_atempo"] = resolved_style.get("max_atempo")
    info["style_allow_atempo"] = resolved_style.get("allow_atempo")
    info["style_voice_fx"] = resolved_style.get("voice_fx")
    info["prefer_semantic_adapt"] = resolved_style.get("prefer_semantic_adapt")
    info["sync_mode"] = resolved_style.get("sync_mode")


def _adaptive_dub_resolve(
    segments_data: list,
    timing_map: list,
    voice: str,
    source_segments: list,
    tts_files: list,
    tts_rate: str | None = None,
    tts_pitch: str | None = None,
    semantic_log=None,
    tgt_lang: str = "ru",
    src_lang: str = "en",
    style_allow_atempo: bool = True,
    task_id: str | None = None,
) -> int:
    """
    FINAL TZ №2 — порядок решений:
    1) адаптация перевода (minimal → moderate → strong)
    2) объединение соседних сегментов одной мысли
    3) allow_atempo=True только если всё ещё не помещается
    Скорость TTS не меняется на шагах 1–2.
    """
    from engines.overlap_quality import analyze_placed_segments, _can_merge_thought

    fixes = 0
    adapt_stages = ("minimal", "moderate", "strong")

    for stage in adapt_stages:
        issues = [
            i
            for i in analyze_placed_segments(segments_data, timing_map)
            if i.get("overflow_ms", 0) > 40
        ]
        if not issues:
            break
        for issue in issues:
            idx = issue["idx"]
            seg = segments_data[idx]
            if task_id:
                _update_progress_detail(
                    task_id,
                    phase="timing",
                    timing_substep="adapt",
                    current_segment=idx + 1,
                    total_segments=len(segments_data),
                )
            if _apply_text_adaptation(
                seg, issue, source_segments, voice, tts_files, stage=stage,
                tts_rate=tts_rate, tts_pitch=tts_pitch,
                semantic_log=semantic_log, tgt_lang=tgt_lang, src_lang=src_lang,
            ):
                fixes += 1
                logger.info(
                    "Adaptive dub: adapt idx=%d stage=%s overflow=%dms",
                    idx,
                    stage,
                    issue["overflow_ms"],
                )

    issues = [
        i
        for i in analyze_placed_segments(segments_data, timing_map)
        if i.get("overflow_ms", 0) > 40
    ]
    for issue in issues:
        idx = issue["idx"]
        placed = analyze_placed_segments(segments_data, timing_map)
        row = next((p for p in placed if p["idx"] == idx), None)
        nxt_row = None
        if row:
            pos = next(
                (j for j, p in enumerate(placed) if p["idx"] == idx), -1
            )
            if 0 <= pos < len(placed) - 1:
                nxt_row = placed[pos + 1]

        if nxt_row and _can_merge_thought(
            row["text"] if row else "",
            nxt_row.get("text", ""),
        ):
            if _try_merge_neighbor_tts(
                segments_data, timing_map, idx, voice, tts_files,
                tts_rate=tts_rate, tts_pitch=tts_pitch,
            ):
                fixes += 1
                logger.info(
                    "Adaptive dub: merge idx=%d+%d overflow=%dms",
                    idx,
                    idx + 1,
                    issue["overflow_ms"],
                )

    for seg in segments_data:
        seg["allow_atempo"] = False

    if not style_allow_atempo:
        return fixes

    final_issues = analyze_placed_segments(segments_data, timing_map)
    for issue in final_issues:
        if issue.get("overflow_ms", 0) > 40:
            idx = issue["idx"]
            segments_data[idx]["allow_atempo"] = True
            logger.warning(
                "Adaptive dub: allow_atempo idx=%d — overflow=%dms after adapt+merge",
                idx,
                issue["overflow_ms"],
            )

    return fixes


def _resolve_timing_overflows(
    segments_data: list,
    timing_map: list,
    voice: str,
    source_segments: list,
    tts_files: list,
) -> int:
    """Обратная совместимость — делегирует в _adaptive_dub_resolve."""
    return _adaptive_dub_resolve(
        segments_data, timing_map, voice, source_segments, tts_files
    )


def _measure_tts_file_ms(file_name: str | None) -> int:
    if not file_name:
        return 0
    path = _artifacts_dir() / Path(str(file_name)).name
    if not path.is_file():
        return 0
    try:
        return len(AudioSegment.from_file(str(path)))
    except Exception:
        return 0


def _parse_segment_slot_timing(seg: dict, idx: int, timing_map: list) -> tuple[int, int]:
    custom = seg.get("tts_timing")
    if custom and len(custom) >= 2:
        return int(custom[0]), int(custom[1])
    if seg.get("start_ms") is not None:
        return int(seg["start_ms"]), int(seg.get("end_ms") or int(seg["start_ms"]) + 3000)
    if idx < len(timing_map):
        from engines.timing_fit import _parse_timing

        return _parse_timing(timing_map[idx])
    return 0, 3000


def _scheduler_set_segment_slot(
    seg: dict,
    *,
    start_ms: int,
    end_ms: int,
    segments_data: list | None = None,
    task_info: dict | None = None,
) -> None:
    """Freeze P1: write start/end only through Scheduler API."""
    from engines.scheduler import update_time

    sid = str(seg.get("segment_id") or "").strip()
    if not sid:
        # Bootstrap / pre-identity rows — stamp id then schedule.
        from engines.pipeline_integrity.segment import ensure_segment_ids

        ensure_segment_ids([seg])
        sid = str(seg.get("segment_id") or "").strip()
    rows = segments_data if segments_data is not None else [seg]
    if seg not in rows:
        # Ensure the segment dict being mutated is the one Scheduler finds.
        update_time([seg], sid, start_ms=start_ms, end_ms=end_ms, info=task_info)
        if end_ms > start_ms:
            seg["slot_ms"] = max(1, int(end_ms) - int(start_ms))
        return
    update_time(rows, sid, start_ms=start_ms, end_ms=end_ms, info=task_info)
    # Keep slot_ms in sync so diagnostics / optimizer never see a zero slot.
    if end_ms > start_ms:
        seg["slot_ms"] = max(1, int(end_ms) - int(start_ms))


def _slot_fit_content_key(
    text: str,
    voice: str,
    slot_ms: int,
    tts_rate: str | None,
    tts_pitch: str | None,
) -> str:
    import hashlib

    raw = f"{(text or '').strip()}|{voice}|{slot_ms}|{tts_rate or ''}|{tts_pitch or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _segment_slot_fit_key(seg: dict) -> str | None:
    """Read slot-fit cache key from timing_meta (allowed mutation container)."""
    tm = seg.get("timing_meta")
    if isinstance(tm, dict):
        key = tm.get("slot_fit_key")
        if key:
            return str(key)
    legacy = seg.get("slot_fit_key")
    return str(legacy) if legacy else None


def _set_segment_slot_fit_key(seg: dict, key: str) -> None:
    """Persist slot-fit cache key inside timing_meta — not as a top-level segment field."""
    tm = seg.get("timing_meta")
    if not isinstance(tm, dict):
        tm = {}
    else:
        tm = dict(tm)
    tm["slot_fit_key"] = key
    seg["timing_meta"] = tm


def _finalize_slot_fit_timing_meta(
    seg: dict,
    prep_meta: dict | None,
    *,
    slot_fit_tts_ms: int | None = None,
    slot_fit_text: str | None = None,
    slot_fit_compressed: bool = False,
    iterations: list[dict] | None = None,
) -> None:
    """Write slot-fit derived values into timing_meta without mutating translation/TTS fields."""
    meta = dict(prep_meta or {})
    if slot_fit_tts_ms is not None:
        meta["slot_fit_tts_ms"] = int(slot_fit_tts_ms)
    original_text = str(seg.get("text") or "").strip()
    adapted = str(slot_fit_text or "").strip()
    if adapted and adapted != original_text:
        meta["slot_fit_text"] = adapted
        meta["slot_fit_compressed"] = True
    elif slot_fit_compressed:
        meta["slot_fit_compressed"] = True
    if iterations:
        meta["slot_fit_iterations"] = list(iterations)
    seg["timing_meta"] = meta


def _publish_studio_session_keep_running(task_id: str, step: str) -> str | None:
    """Persist studio session without leaving task in studio_ready during pipeline."""
    try:
        from api.studio_api import publish_studio_ready

        studio_url = publish_studio_ready(task_id)
    except Exception as exc:
        logger.warning("Task %s: studio session publish skipped: %s", task_id, exc)
        return None
    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        if task and task.get("status") == "studio_ready":
            task["status"] = "running"
            task["step"] = step
            if studio_url:
                task.setdefault("info", {})["studio_url"] = studio_url
    return studio_url


_DUB_SLOT_TOLERANCE_MS = 75
_DUB_MAX_ATEMPO = 1.15          # non-UK legacy ceiling; UK mux is clamped to 1.08
_UK_MUX_MAX_ATEMPO = 1.08       # Stage 31/32 — zip 2286c82f still used 1.15


def _clamp_placement_window(
    start_ms: int,
    end_ms: int,
    *,
    merge_adjusted_start: int | None = None,
    min_slot_ms: int = 200,
) -> tuple[int, int]:
    """Stage 32 — never invert the mux window (diag 2286c82f idx=4 place_end < place_start).

    ``merge_adjusted_start`` past ``end_ms`` produced ``slot_ms=1`` and a 500 on
    POST /api/studio/mix. Ignore the inverted stamp; keep a positive slot.
    """
    start = int(start_ms or 0)
    end = int(end_ms or 0)
    if merge_adjusted_start is not None:
        try:
            adj = int(merge_adjusted_start)
        except (TypeError, ValueError):
            adj = start
        if adj < end:
            start = adj
    if end <= start:
        end = start + max(1, int(min_slot_ms))
    return start, end


_VIDEO_ADAPT_MAX_PCT = 15.0     # overlap ≤ 15% → prefer gap-borrow / video slowdown

# ── Block merge constants ──────────────────────────────────────────────────────
# If a segment overflows AND overflow_pct ≤ this limit, try merging with the next
# block rather than shortening text or applying atempo.
_MERGE_OVERFLOW_MAX_PCT: float = 35.0
_MAX_CONSECUTIVE_MERGES: int = 2      # hard limit per TZ
_MERGE_NATURAL_PAUSE_MS: int = 100    # silence gap between two merged blocks
_UNDERFILL_MERGE_THRESH: float = 0.45 # underfill < 45% of slot → consider merge/fix


def _plan_block_merges(segments_data: list, timing_map: list) -> int:
    """
    Post-fit block merge planner — TZ new sync logic.

    Rules (from TZ):
    - Main sync unit is one semantic block, NOT the whole film.
    - If a block slightly overflows (≤ 35%), it may borrow time from the NEXT
      adjacent block only. Max 2 consecutive merges.
    - After merging, next block starts from a clean time point.
    - If a block has excessive underfill (< 45% fill): also try merging so the
      combined pair fills the window more naturally.
    - Never merge ≥ 3 consecutive blocks.

    Sets `merge_adjusted_start` and `merge_adjusted_slot_ms` on the NEXT segment
    when a merge is planned.  Returns total merge count.
    """
    merge_count = 0
    consecutive = 0
    n = len(segments_data)

    for idx in range(n - 1):  # can't merge the last segment with anyone
        seg = segments_data[idx]
        if seg.get("merged_into") is not None:
            continue

        speech_ms = int(seg.get("tts_ms") or seg.get("fitted_ms") or 0)
        start_ms, end_ms = _parse_segment_slot_timing(seg, idx, timing_map)
        slot_ms = max(1, end_ms - start_ms)
        overflow_pct = float(seg.get("overflow_pct") or 0.0)
        if speech_ms > slot_ms and overflow_pct <= 0:
            overflow_ms = speech_ms - slot_ms
            overflow_pct = round(100.0 * overflow_ms / slot_ms, 1)

        speech_extends_past_slot = speech_ms > slot_ms + _DUB_SLOT_TOLERANCE_MS
        underfill = speech_ms > 0 and (speech_ms < slot_ms * _UNDERFILL_MERGE_THRESH)

        overflow_candidate = (
            (bool(seg.get("slot_overflow")) or speech_extends_past_slot)
            and overflow_pct > 0
            and overflow_pct <= _MERGE_OVERFLOW_MAX_PCT
        )
        underfill_candidate = (
            underfill
            and not seg.get("slot_overflow")
            and not speech_extends_past_slot
        )

        if not (overflow_candidate or underfill_candidate):
            if not seg.get("slot_overflow"):
                consecutive = 0  # clean segment — reset chain
            continue

        if consecutive >= _MAX_CONSECUTIVE_MERGES:
            consecutive = 0  # hard reset after reaching limit
            logger.debug("[BlockMerge] seg#%d: merge limit reached, skipping", idx)
            continue

        # Find the next non-skipped segment
        next_idx: int | None = None
        for ni in range(idx + 1, n):
            if segments_data[ni].get("merged_into") is None:
                next_idx = ni
                break

        if next_idx is None:
            consecutive = 0
            continue

        next_seg = segments_data[next_idx]
        next_start, next_end = _parse_segment_slot_timing(next_seg, next_idx, timing_map)

        # For overflow: segment i's audio ends at start_ms + tts_ms
        # The next segment must start after that + natural pause
        if overflow_candidate:
            adjusted_start = start_ms + speech_ms + _MERGE_NATURAL_PAUSE_MS
        else:
            # Underfill: keep natural speech flow, start next right after i's speech
            adjusted_start = start_ms + speech_ms + _MERGE_NATURAL_PAUSE_MS

        if adjusted_start >= next_end:
            # No room left for next segment — abandon merge
            consecutive = 0
            continue

        new_slot = next_end - adjusted_start
        next_tts_ms = int(next_seg.get("tts_ms") or next_seg.get("fitted_ms") or 0)

        # Safety check: next segment's audio must fit in the adjusted slot
        if next_tts_ms > 0 and new_slot < int(next_tts_ms * 0.5):
            logger.debug(
                "[BlockMerge] seg#%d→#%d: new_slot=%dms too tight for next tts=%dms — skip",
                idx, next_idx, new_slot, next_tts_ms,
            )
            consecutive = 0
            continue

        # Apply merge
        next_seg["merge_adjusted_start"] = adjusted_start
        next_seg["merge_adjusted_slot_ms"] = new_slot
        seg["block_merged_with_next"] = next_idx

        if overflow_candidate:
            seg["slot_overflow"] = False
            seg["container_status"] = "yellow"  # merged, not red

        consecutive += 1
        merge_count += 1
        logger.info(
            "[BlockMerge] seg#%d → seg#%d: %s overflow=%.1f%% underfill=%s "
            "adjusted_start=%dms new_slot=%dms",
            idx, next_idx,
            "overflow" if overflow_candidate else "underfill",
            overflow_pct, underfill,
            adjusted_start, new_slot,
        )

    if merge_count:
        logger.info("[BlockMerge] total merges planned: %d (max consecutive: %d)",
                    merge_count, _MAX_CONSECUTIVE_MERGES)
    return merge_count


def _validate_sync_plan(segments_data: list, timing_map: list) -> list[str]:
    """
    Final sync validation (TZ requirement).

    Checks:
    1. No overlap between adjacent segments
    2. No excessive atempo (> 1.05x = speech becomes a tongue-twister)
    3. No excessive underfill (< 40% fill = long dead air)
    4. No cumulative drift (each segment anchored to original Whisper timing)

    Returns list of warning strings (also logged).
    """
    warnings: list[str] = []
    prev_end_ms = 0

    for idx, seg in enumerate(segments_data):
        if seg.get("merged_into") is not None:
            continue

        # Effective start — may be adjusted by block merge
        if seg.get("merge_adjusted_start"):
            start_ms = int(seg["merge_adjusted_start"])
        else:
            start_ms, _ = _parse_segment_slot_timing(seg, idx, timing_map)
            start_ms = int(seg.get("start_ms") or start_ms)

        tts_ms = int(seg.get("tts_ms") or 0)
        actual_end = start_ms + tts_ms

        orig_start, orig_end = _parse_segment_slot_timing(seg, idx, timing_map)
        slot_ms = max(1, orig_end - orig_start)

        # 1. Overlap check
        if tts_ms > 0 and start_ms < prev_end_ms:
            overlap = prev_end_ms - start_ms
            warnings.append(
                f"[SyncValidate] seg#{idx}: OVERLAP {overlap}ms with prev (start={start_ms} prev_end={prev_end_ms})"
            )

        # 2. Atempo check
        meta = seg.get("timing_meta") or {}
        atempo = float(meta.get("atempo") or 1.0)
        if atempo > 1.05:
            warnings.append(
                f"[SyncValidate] seg#{idx}: atempo={atempo:.3f} EXCEEDS 1.05 — sounds like tongue-twister"
            )

        # 3. Underfill check (long dead air after speech)
        if tts_ms > 0 and tts_ms < slot_ms * 0.40:
            fill_pct = int(100 * tts_ms / slot_ms)
            warnings.append(
                f"[SyncValidate] seg#{idx}: underfill {fill_pct}% (tts={tts_ms}ms slot={slot_ms}ms) — dead air risk"
            )

        # 4. Drift check — segment must start near its original Whisper anchor
        drift = abs(start_ms - orig_start)
        if drift > 800:
            warnings.append(
                f"[SyncValidate] seg#{idx}: drift={drift}ms from original anchor "
                f"(start={start_ms} orig={orig_start}) — sync accumulation risk"
            )

        if tts_ms > 0:
            prev_end_ms = max(prev_end_ms, actual_end)

    for w in warnings:
        logger.warning(w)

    return warnings


def _equalize_speech_speeds(segments_data: list, timing_map: list) -> None:
    """
    Post-slot-fit pass: detect segments with unusually high atempo vs neighbours
    and log a warning.  Future versions can re-generate those segments at a
    slightly higher TTS rate so all segments sound equally paced.

    Current implementation: read-only analysis + logging (safe, no regressions).
    A segment is flagged if its atempo is >0.03 above the group median.
    """
    atempos: list[float] = []
    for seg in segments_data:
        if seg.get("merged_into") is not None:
            continue
        meta = seg.get("timing_meta") or {}
        a = float(meta.get("atempo") or 1.0)
        atempos.append(a)

    if not atempos:
        return

    sorted_a = sorted(atempos)
    median = sorted_a[len(sorted_a) // 2]
    outliers = [
        (i, seg, float((seg.get("timing_meta") or {}).get("atempo") or 1.0))
        for i, seg in enumerate(segments_data)
        if seg.get("merged_into") is None
        and float((seg.get("timing_meta") or {}).get("atempo") or 1.0) > median + 0.03
    ]
    if outliers:
        logger.info(
            "speed_equalize: median atempo=%.3f, %d outlier segments detected: %s",
            median,
            len(outliers),
            [(i, round(a, 3)) for i, _, a in outliers[:5]],
        )
        for _i, seg, a in outliers:
            seg.setdefault("timing_meta", {})["speed_outlier"] = True
            seg["timing_meta"]["atempo_vs_median"] = round(a - median, 3)


def _log_dub_slot_fit(
    task_id: str | None,
    seg_idx: int,
    step: str,
    slot_ms: int,
    tts_ms: int,
    ratio: float = 1.0,
) -> None:
    msg = (
        f"[DubSlotFit] seg={seg_idx} step={step} "
        f"slot_ms={slot_ms} tts_ms={tts_ms} ratio={ratio:.3f}"
    )
    logger.info(msg)
    if not task_id:
        return
    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        if not task:
            return
        detail = task.setdefault("info", {}).setdefault("progress_detail", {})
        logs = detail.setdefault("slot_fit_log", [])
        if not logs or logs[-1] != msg:
            logs.append(msg)
        if len(logs) > 300:
            detail["slot_fit_log"] = logs[-300:]


def _absolutize_segment_audio_paths(
    segments_data: list,
    session_dir: str | Path | None = None,
    *,
    task_id: str | None = None,
) -> int:
    """Stage 24/28: rewrite segment audio keys to absolute resolved paths.

    Stage 28 §A2 — a stale/relative path no longer wins over a valid file in
    ``session_dir/closed_loop/<task_id>/`` (where soft-pad / repair / regen
    actually write). If a key holds a ghost path we look through session-adapter
    rglob before writing it back, so census cannot report exists:false for a
    file that is physically on disk.
    """
    base = Path(str(session_dir)) if session_dir else None
    if base is not None:
        try:
            base = base.resolve()
        except OSError:
            pass
    tid = str(task_id or "").strip()

    try:
        from engines.dubbing_engine.session_adapter import (
            resolve_session_audio as _resolve_session_audio,
        )
    except Exception:
        _resolve_session_audio = None

    def _deep_find(raw: str) -> Path | None:
        p = Path(raw)
        if p.is_file():
            return p
        if base is not None and tid:
            cand = base / "closed_loop" / tid / p.name
            if cand.is_file():
                return cand
        if base is not None:
            # VideoLingo-style segs/ is the mux workdir (Stage 36).
            segs_hit = base / "segs" / p.name
            if segs_hit.is_file():
                return segs_hit
            m = re.search(r"(?:^|[_-])(\d{4})\.(?:wav|mp3)$", p.name, re.I)
            if m:
                numbered = base / "segs" / f"{int(m.group(1)):04d}.wav"
                if numbered.is_file():
                    return numbered
            cand = base / p.name
            if cand.is_file():
                return cand
        if _resolve_session_audio is not None:
            try:
                cand = _resolve_session_audio(
                    raw,
                    task_info={
                        "session_dir": str(base) if base is not None else None,
                        "task_id": tid,
                    },
                )
                if cand and cand.is_file():
                    return cand
            except Exception:
                pass
        return None

    fixed = 0
    for seg in segments_data or []:
        if not isinstance(seg, dict):
            continue
        for key in ("file", "tts_file_path", "fitted_file", "resolved_path"):
            raw = str(seg.get(key) or "").strip()
            if not raw:
                continue
            p = _deep_find(raw)
            if p is None:
                continue
            try:
                abs_p = str(p.resolve())
            except OSError:
                abs_p = str(p)
            if seg.get(key) != abs_p:
                seg[key] = abs_p
                fixed += 1
        # Prefer fitted → file as canonical absolute path.
        for prefer in ("fitted_file", "file", "tts_file_path"):
            pref = str(seg.get(prefer) or "").strip()
            if not pref:
                continue
            pref_path = _deep_find(pref)
            if pref_path is None:
                continue
            try:
                abs_p = str(pref_path.resolve())
            except OSError:
                abs_p = str(pref_path)
            seg["resolved_path"] = abs_p
            if prefer != "file" and not (
                seg.get("file") and Path(str(seg.get("file"))).is_file()
            ):
                seg["file"] = abs_p
            break
    return fixed


def _regen_segment_tts(
    text: str,
    *,
    voice: str,
    work_dir: Path,
    tts_rate: str | None = None,
    tts_pitch: str | None = None,
    emotion: str | None = None,
    task_id: str | None = None,
    segment_index: int | None = None,
    segment_id: str | None = None,
    engine_id: str | None = None,
    length_scale: float | None = None,
    volume: float | None = None,
    mykyta_controls: dict | None = None,
    target_lang: str | None = None,
    stamp_seg: dict | None = None,
) -> tuple[str | None, int]:
    from engines.pipeline_integrity.audio_identity import (
        allocate_tts_path,
        bind_segment_audio,
        copy_to_unique_path,
        ensure_segment_uuid,
    )
    from engines.pipeline_integrity.tts_file_lifecycle import log_tts_lifecycle
    from engines.tts import generate_audio
    from engines.tts_backends import normalize_backend_name
    from engines.tts_lang_lock import (
        force_uk_tts_identity,
        guard_uk_tts_text,
        is_latin_heavy,
        uk_text_has_russian_leak,
    )

    seg_meta = {"segment_id": segment_id or "", "index": segment_index or 0}
    suid = ensure_segment_uuid(seg_meta)
    # Stage 24: force Ukrainian identity when target=uk.
    _ident = force_uk_tts_identity(
        target_lang=target_lang, engine_id=engine_id, voice=voice
    )
    eid = normalize_backend_name(_ident.get("engine_id") or engine_id or "edge-offline")
    voice = str(_ident.get("voice") or voice or "")
    _lang = str(_ident.get("language") or target_lang or "uk")
    speak_text = " ".join(str(text or "").split()).strip()
    # Stage 28 §D1 — strip Stage-5 pacing pads BEFORE TTS so "ось як це було
    # тоді"/"Саме так: …" fillers do not eat slot time in the final audio.
    try:
        from engines.text_slot_fit import prepare_uk_spoken_text

        speak_text = prepare_uk_spoken_text(speak_text)
    except Exception:
        pass
    if _lang == "uk" and speak_text:
        from engines.tts_lang_lock import cyrillic_letter_ratio, is_uk_tts_text_ok

        heavy, lat_r = is_latin_heavy(speak_text, threshold=0.30)
        cyr_ok = is_uk_tts_text_ok(speak_text)
        if stamp_seg is not None:
            stamp_seg["cyrillic_ratio"] = round(cyrillic_letter_ratio(speak_text), 3)
        if heavy or not cyr_ok:
            logger.warning(
                "[TTS] uk_text_gate seg#%s latin=%.2f cyr_ok=%s text=%.80s — remt/refuse",
                segment_index if segment_index is not None else "?",
                lat_r,
                cyr_ok,
                speak_text,
            )
            if stamp_seg is not None:
                if heavy:
                    stamp_seg["latin_heavy_warning"] = True
                    stamp_seg["latin_letter_ratio"] = round(lat_r, 3)
            remt_text, meta = guard_uk_tts_text(
                speak_text,
                source_text=str(
                    (stamp_seg or {}).get("original")
                    or (stamp_seg or {}).get("original_text")
                    or (stamp_seg or {}).get("source_text")
                    or ""
                ),
                src_lang=str((stamp_seg or {}).get("source_lang") or "en"),
                tgt_lang="uk",
                segment_index=int(segment_index or 0),
                allow_remt=True,
                fail_loud=False,
            )
            if meta.get("tts_lang_ok") and remt_text:
                speak_text = remt_text
                if stamp_seg is not None:
                    stamp_seg["cyrillic_ratio"] = round(
                        cyrillic_letter_ratio(speak_text), 3
                    )
            else:
                # Stage 29/33 — do not voice Latin or Russian as UK; pad instead.
                if stamp_seg is not None:
                    if heavy:
                        skip_reason = "latin_heavy_refused"
                    elif uk_text_has_russian_leak(speak_text):
                        skip_reason = "russian_in_uk"
                    else:
                        skip_reason = "cyrillic_ratio_low"
                    stamp_seg["tts_skip_reason"] = skip_reason
                    stamp_seg["needs_re_tts"] = True
                return None, 0
    text = speak_text
    log_tts_lifecycle(
        task_id,
        event="gen_start",
        segment_id=suid,
        segment_index=segment_index,
        stage="slot_fit_regen",
        detail=f"text_len={len(text)} engine={eid} lang={_lang} voice={voice}",
    )
    ctx_extra: dict = {
        "segment_id": suid,
        "segment_uuid": suid,
        "segment_index": segment_index,
        "task_id": task_id or "",
        "tts_backend": eid,
        "target_lang": _lang,
        "language": _lang,
        "tts_language": _lang,
        "tts_voice": voice,
    }
    if eid == "tts_uk":
        try:
            from engines.tts_backends import (
                resolve_mykyta_controls,
                set_pipeline_mykyta_controls,
            )

            _mk_src = dict(mykyta_controls or {})
            if tts_rate is not None:
                _mk_src.setdefault("rate", tts_rate)
            if tts_pitch is not None:
                _mk_src.setdefault("pitch", tts_pitch)
            if volume is not None:
                _mk_src.setdefault("volume", volume)
            if length_scale is not None:
                _mk_src.setdefault("length_scale", length_scale)
            _mk = resolve_mykyta_controls(_mk_src)
            ctx_extra["tts_rate"] = _mk["rate"]
            ctx_extra["tts_pitch"] = _mk["pitch"]
            ctx_extra["tts_volume"] = _mk["volume"]
            ctx_extra["tts_length_scale"] = _mk["length_scale"]
            set_pipeline_mykyta_controls(_mk)
            tts_rate = str(_mk["rate"])
            tts_pitch = str(_mk["pitch"])
        except Exception:
            pass
    gen_kwargs: dict = {
        "text": text,
        "voice": voice,
        "segments": [text],
        "rate": tts_rate,
        "pitch": tts_pitch,
        "emotion": emotion,
        "engine_id": eid,
        "output_dir": _artifacts_dir(),
        "context": ctx_extra,
    }
    files = generate_audio(**gen_kwargs)
    if not files:
        log_tts_lifecycle(
            task_id,
            event="gen_end",
            segment_id=suid,
            segment_index=segment_index,
            stage="slot_fit_regen",
            success=False,
            detail="generate_audio returned empty",
        )
        return None, 0
    src = _artifacts_dir() / files[0]
    if not src.is_file():
        log_tts_lifecycle(
            task_id,
            event="gen_end",
            segment_id=suid,
            segment_index=segment_index,
            filename=files[0],
            path=src,
            stage="slot_fit_regen",
            success=False,
            exists=False,
            detail="output missing on disk",
        )
        return None, 0
    work_dir.mkdir(parents=True, exist_ok=True)
    dest = copy_to_unique_path(
        src,
        work_dir,
        segment_uuid=suid,
        run_id=str(task_id or ""),
        purpose="tts_regen",
    )
    # Stage 30 C2 — sidecar was keyed by synth `src`; dest is a copy.
    try:
        from engines.tts_backends import transfer_last_synth_meta as _xfer_meta

        _xfer_meta(src, dest)
    except Exception:
        pass
    try:
        tts_ms = len(AudioSegment.from_file(str(dest)))
    except Exception:
        tts_ms = 0
    try:
        from engines.pipeline_integrity.audio_presence import MIN_AUDIO_BYTES, audio_stat

        ok_a, size_a = audio_stat(dest)
        if not ok_a or size_a < MIN_AUDIO_BYTES:
            log_tts_lifecycle(
                task_id,
                event="gen_end",
                segment_id=suid,
                segment_index=segment_index,
                filename=dest.name,
                path=dest,
                stage="slot_fit_regen",
                success=False,
                exists=dest.is_file(),
                detail=f"tiny_or_missing size={size_a} duration_ms={tts_ms}",
            )
            return None, 0
    except Exception:
        if not dest.is_file() or dest.stat().st_size < 1000:
            return None, 0
    log_tts_lifecycle(
        task_id,
        event="gen_end",
        segment_id=suid,
        segment_index=segment_index,
        filename=dest.name,
        path=dest,
        stage="slot_fit_regen",
        success=True,
        exists=dest.is_file(),
        detail=f"duration_ms={tts_ms}",
    )
    # Stage 24/25/26: stamp TTS identity on the segment when provided —
    # honour the actually-used backend/voice from the last-synth sidecar so
    # a silent Edge fallback never lies as tts_uk/Mykyta in the JSON.
    if stamp_seg is not None:
        try:
            abs_dest = str(Path(dest).resolve())
        except OSError:
            abs_dest = str(dest)
        try:
            from engines.tts_backends import (
                backend_display_name,
                normalize_backend_name,
                pop_last_synth_meta,
            )

            _synth_meta = pop_last_synth_meta(abs_dest)
            if not _synth_meta:
                try:
                    _synth_meta = pop_last_synth_meta(str(Path(src).resolve()))
                except Exception:
                    _synth_meta = {}
            _eid_eff = normalize_backend_name(
                _synth_meta.get("tts_engine")
                or _synth_meta.get("tts_backend")
                or eid
            )
            _voice_eff = str(_synth_meta.get("tts_voice") or voice or "")
            if str(_eid_eff).startswith("edge") or (
                str(_voice_eff).startswith("uk-UA-")
                and _synth_meta.get("tts_fallback_reason")
            ):
                _eid_eff = normalize_backend_name("edge-offline")
            display = backend_display_name(_eid_eff)
            if str(_eid_eff).startswith("edge"):
                display = "edge-offline"
        except Exception:
            _synth_meta = {}
            _eid_eff = eid
            _voice_eff = voice
            display = "tts_uk" if eid == "tts_uk" else ("edge-offline" if eid in ("edge", "edge-offline") else eid)
        stamp_seg["tts_backend"] = display
        stamp_seg["tts_engine"] = _eid_eff
        stamp_seg["tts_voice"] = _voice_eff or voice
        stamp_seg["tts_language"] = _lang
        stamp_seg["voice"] = _voice_eff or voice
        stamp_seg["file"] = abs_dest
        stamp_seg["tts_file_path"] = abs_dest
        stamp_seg["resolved_path"] = abs_dest
        if _synth_meta.get("tts_fallback_reason"):
            stamp_seg["tts_fallback_reason"] = str(
                _synth_meta["tts_fallback_reason"]
            )
        if _synth_meta.get("tts_engine_requested"):
            stamp_seg["tts_engine_requested"] = str(
                _synth_meta["tts_engine_requested"]
            )
        if _synth_meta.get("tts_voice_requested"):
            stamp_seg["tts_voice_requested"] = str(
                _synth_meta["tts_voice_requested"]
            )
        # Diagnostics (TZ §5): honest exists / size / cyrillic ratio.
        try:
            from engines.pipeline_integrity.audio_presence import audio_stat

            _ok_a, _size_a = audio_stat(abs_dest)
            stamp_seg["audio_exists"] = bool(_ok_a)
            stamp_seg["audio_size_bytes"] = int(_size_a)
        except Exception:
            pass
        try:
            from engines.tts_lang_lock import cyrillic_letter_ratio

            if text:
                stamp_seg["cyrillic_ratio"] = round(cyrillic_letter_ratio(text), 3)
        except Exception:
            pass
    return str(Path(dest).resolve()) if Path(dest).is_file() else dest.name, tts_ms


def _premux_segment_fits(audio_path: str | Path, slot_ms: int, tolerance_ms: int | None = None) -> tuple[bool, int]:
    tolerance_ms = tolerance_ms if tolerance_ms is not None else _DUB_SLOT_TOLERANCE_MS
    try:
        dur = len(AudioSegment.from_file(str(audio_path)))
    except Exception:
        return False, 0
    return dur <= slot_ms + tolerance_ms, dur


def _commit_fitted_wav(
    src_path: Path,
    idx: int,
    *,
    task_info: dict | None = None,
    segment_id: str | None = None,
) -> str | None:
    """Copy fitted WAV into the session artifact dir under a unique name.

    Stage 28 §A1 — slot-fit outputs go under
    ``session_dir/closed_loop/<task_id>/`` alongside pads and regens, so the
    census resolver finds them in a single tree.
    """
    if not src_path.is_file():
        return None
    try:
        _sd_root = _artifacts_dir(task_info).resolve()
    except Exception:
        _sd_root = _artifacts_dir(task_info)
    _tid_fit = str((task_info or {}).get("task_id") or "").strip() or "fit"
    dest_dir = _sd_root / "closed_loop" / _tid_fit
    dest_dir.mkdir(parents=True, exist_ok=True)
    from engines.pipeline_integrity.audio_identity import (
        copy_to_unique_path,
        ensure_segment_uuid,
    )

    meta = {"segment_id": segment_id or f"idx{idx}"}
    suid = ensure_segment_uuid(meta)
    run_id = str((task_info or {}).get("task_id") or "")
    try:
        dest = copy_to_unique_path(
            src_path,
            dest_dir,
            segment_uuid=suid,
            run_id=run_id,
            purpose="slot_fit",
        )
    except OSError as exc:
        logger.warning("fitted wav copy failed idx=%s: %s", idx, exc)
        return None
    if not dest.is_file() or dest.stat().st_size < 64:
        return None
    # Stage 24: always return absolute path (never bare basename).
    try:
        return str(dest.resolve())
    except OSError:
        return str(dest)


def _repair_missing_fitted_files(
    segments_data: list,
    *,
    task_info: dict | None,
    task_id: str | None = None,
) -> int:
    """Rebuild missing slot_*_fit.wav from raw TTS when fit step failed silently."""
    repaired = 0
    for idx, seg in enumerate(segments_data):
        if seg.get("merged_into") is not None:
            continue
        fitted = seg.get("fitted_file")
        if fitted:
            if _resolve_segment_audio_path(fitted, task_info).is_file():
                continue
        raw_name = seg.get("file")
        if not raw_name:
            seg.pop("fitted_file", None)
            continue
        raw_path = _resolve_segment_audio_path(raw_name, task_info)
        if not raw_path.is_file():
            seg.pop("fitted_file", None)
            continue
        new_fit = _commit_fitted_wav(
            raw_path,
            idx,
            task_info=task_info,
            segment_id=str(seg.get("segment_id") or ""),
        )
        if new_fit:
            seg["fitted_file"] = new_fit
            repaired += 1
            logger.info(
                "Task %s: repaired missing fitted_file seg=%d -> %s",
                task_id,
                idx,
                new_fit,
            )
        else:
            seg.pop("fitted_file", None)
    return repaired


def _prepare_segment_audio_for_mux(
    seg: dict,
    *,
    idx: int,
    start_ms: int,
    end_ms: int,
    slot_ms: int,
    voice: str,
    target_lang: str,
    source_hint: str,
    tts_rate: str | None,
    tts_pitch: str | None,
    emotion: str | None,
    work_dir: Path,
    task_id: str | None = None,
    task_info: dict | None = None,
    max_compress_rounds: int = 3,
    gap_after_ms: int = 0,
) -> dict:
    """
    Quality-first segment audio prep — natural speech takes priority.

    New hierarchy (speech compression is LAST, not first):
      Phase 0  — TTS already fits within tolerance → done, no modification
      Phase 1  — Trim trailing silence → fits? done
      Phase 2  — Compress internal pauses → fits? done
      Phase 3  — Overflow ≤ 15% → gap-absorb or video-adapt (no speech modification)
      Phase 4  — Overflow > 15% → natural text shortening (up to max_compress_rounds)
                 Each round: shorten text → regen TTS → re-check phases 1-3
      Phase 5  — Minimal atempo ≤ 1.05x (LAST RESORT — barely perceptible)

    Video-adapt segments (overflow 0-15%, gap < overflow) are tagged with
    seg["video_stretch_ratio"] so DubEngine can slow the video instead of
    rushing the voice.
    """
    from engines.regeneration import _overflow_status
    from engines.timing_fit import (
        DUB_SLOT_TOLERANCE_MS,
        VIDEO_ADAPT_MAX_OVERFLOW_PCT,
        classify_segment_overflow,
        prepare_dub_segment_audio,
    )

    file_name = seg.get("file")
    if not file_name:
        return {"ok": False, "error": "no_tts_file", "overflow_pct": 100.0}

    src_path = _artifacts_dir(task_info) / Path(str(file_name)).name
    if not src_path.is_file():
        src_path = _resolve_segment_audio_path(file_name, task_info)
    if not src_path.is_file():
        return {"ok": False, "error": "missing_tts", "overflow_pct": 100.0}

    cur_text = str(seg.get("text") or "").strip()
    iterations: list[dict] = []
    slot_limit = slot_ms + DUB_SLOT_TOLERANCE_MS
    prepared_path: Path | None = None
    tts_ms = 0
    fitted_ms = 0
    stretch_ratio = 1.0
    prep_meta: dict = {}
    video_stretch_ratio = 1.0
    gap_absorb_mode = False

    def _fit_with_trim_only(src: Path, round_idx: int) -> tuple[Path | None, dict, int, float]:
        """Phase 1-2: trim silence + compress pauses, NO atempo."""
        rw = work_dir / f"round_{round_idx}_trim"
        prepared_str, meta = prepare_dub_segment_audio(
            src, slot_ms, rw,
            max_atempo=1.0,          # no atempo — trim+compress only
            tolerance_ms=DUB_SLOT_TOLERANCE_MS,
        )
        path = Path(prepared_str)
        f_ms = int(meta.get("fitted_ms") or 0)
        s_ratio = float(meta.get("atempo") or 1.0)
        return path, meta, f_ms, s_ratio

    def _fit_with_minimal_atempo(src: Path, round_idx: int) -> tuple[Path | None, dict, int, float]:
        """Phase 5: last-resort minimal atempo (≤ 1.05x)."""
        rw = work_dir / f"round_{round_idx}_atempo"
        prepared_str, meta = prepare_dub_segment_audio(
            src, slot_ms, rw,
            max_atempo=_DUB_MAX_ATEMPO,
            tolerance_ms=DUB_SLOT_TOLERANCE_MS,
        )
        path = Path(prepared_str)
        f_ms = int(meta.get("fitted_ms") or 0)
        s_ratio = float(meta.get("atempo") or 1.0)
        return path, meta, f_ms, s_ratio

    # ── Phase 0: quick pre-check ──────────────────────────────────────────────
    tts_ms = len(AudioSegment.from_file(str(src_path)))
    oc = classify_segment_overflow(tts_ms, slot_ms, gap_after_ms)

    if oc.label == "fits":
        # Already fits: copy to prepared location, skip all fitting
        prep_meta = {
            "slot_ms": slot_ms,
            "fitted_ms": tts_ms,
            "atempo": 1.0,
            "strategy": "none",
            "overflow_ms": 0,
        }
        prepared_path = work_dir / f"phase0_{src_path.name}"
        prepared_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_path, prepared_path)
        fitted_ms = tts_ms
    else:
        # ── Phases 1-2: trim silence + compress pauses ────────────────────────
        prepared_path, prep_meta, fitted_ms, stretch_ratio = _fit_with_trim_only(src_path, 0)
        _log_dub_slot_fit(task_id, idx, "trim_compress", slot_ms, fitted_ms, stretch_ratio)

        oc2 = classify_segment_overflow(fitted_ms, slot_ms, gap_after_ms)

        if oc2.label == "fits":
            pass  # phases 1-2 were enough

        elif oc2.label in ("gap_absorb", "video_adapt"):
            # TZ: never jump to video stretch before tempo (Problem №8 / №10)
            applied_chain = ["trim_silence", "pause_optimization"]
            fitted_after_tempo = fitted_ms
            try:
                prepared_path, prep_meta, fitted_after_tempo, stretch_ratio = (
                    _fit_with_minimal_atempo(src_path, 1)
                )
                applied_chain.append("tempo")
                _log_dub_slot_fit(
                    task_id, idx, "tempo_before_stretch", slot_ms, fitted_after_tempo, stretch_ratio
                )
                iterations.append(
                    {
                        "action": "tempo",
                        "fitted_ms": fitted_after_tempo,
                        "atempo": stretch_ratio,
                    }
                )
            except Exception:
                fitted_after_tempo = fitted_ms

            oc3 = classify_segment_overflow(fitted_after_tempo, slot_ms, gap_after_ms)
            if oc3.label == "fits":
                fitted_ms = fitted_after_tempo
                from engines.dub_engine_v2.overflow_strategy import (
                    decide_overflow,
                    stamp_decision_on_segment,
                )

                decision = decide_overflow(
                    index=idx,
                    overflow_ms=max(0, int(oc2.overflow_ms or 0)),
                    slot_ms=slot_ms,
                    cause="resolved_by_tempo",
                    gap_after_ms=gap_after_ms,
                    text_locked=True,
                    already_applied=applied_chain,
                )
                decision.chosen = "tempo"
                decision.chosen_cost = 2.0
                decision.duration_after_ms = fitted_ms
                stamp_decision_on_segment(seg, decision)
            else:
                # Only now allow borrow (gap) or stretch (video)
                fitted_ms = fitted_after_tempo
                gap_absorb_mode = oc3.label == "gap_absorb"
                video_stretch_ratio = oc3.video_stretch_ratio
                seg["video_stretch_ratio"] = round(video_stretch_ratio, 4)
                seg["video_adapt_mode"] = oc3.label
                applied_chain.append(
                    "borrow_time" if gap_absorb_mode else "stretch"
                )
                _log_dub_slot_fit(task_id, idx, oc3.label, slot_ms, fitted_ms, 1.0)
                from engines.dub_engine_v2.overflow_strategy import (
                    decide_overflow,
                    stamp_decision_on_segment,
                )

                decision = decide_overflow(
                    index=idx,
                    overflow_ms=int(oc3.overflow_ms or 0),
                    slot_ms=slot_ms,
                    cause=str(oc3.label),
                    gap_after_ms=gap_after_ms,
                    text_locked=True,
                    already_applied=applied_chain[:-1],
                )
                decision.chosen = (
                    "borrow_time" if gap_absorb_mode else "stretch"
                )
                decision.chosen_cost = 6.0 if gap_absorb_mode else 4.0
                decision.duration_after_ms = fitted_ms
                stamp_decision_on_segment(seg, decision)
                # Keep speech; overflow absorbed by gap or video stretch

        else:
            # Overflow > 15% — text adaptation runs in post_tts; no atempo (TZ stage 2)
            _log_dub_slot_fit(task_id, idx, "overflow_text_only", slot_ms, fitted_ms, 1.0)
            iterations.append({"action": "overflow_text_only", "note": "no_atempo"})
            if fitted_ms > slot_limit and prepared_path and prepared_path.is_file():
                oc_audio = classify_segment_overflow(fitted_ms, slot_ms, gap_after_ms)
                if oc_audio.label in ("gap_absorb", "video_adapt"):
                    video_stretch_ratio = oc_audio.video_stretch_ratio
                    seg["video_stretch_ratio"] = round(video_stretch_ratio, 4)
                    seg["video_adapt_mode"] = oc_audio.label
                    gap_absorb_mode = oc_audio.label == "gap_absorb"
                    fitted_ms = tts_ms

    if prepared_path is None or not prepared_path.is_file():
        return {
            "ok": False,
            "error": "prepare_failed",
            "overflow_pct": 100.0,
            "iterations": iterations,
        }

    fits, measured_ms = _premux_segment_fits(prepared_path, slot_ms)
    if measured_ms > 0:
        fitted_ms = measured_ms

    fit_dest = _commit_fitted_wav(
        prepared_path,
        idx,
        task_info=task_info,
        segment_id=str(seg.get("segment_id") or ""),
    )
    if not fit_dest:
        _overflow_ms = max(0, fitted_ms - slot_limit)
        return {
            "ok": False,
            "error": "fitted_copy_failed",
            "overflow_pct": round(100.0 * _overflow_ms / max(slot_ms, 1), 1),
            "iterations": iterations,
        }
    seg["fitted_file"] = fit_dest

    overflow_ms = max(0, fitted_ms - slot_limit)
    overflow_pct = round(100.0 * overflow_ms / max(slot_ms, 1), 1)

    # Gap-absorb mode: overflow goes into the inter-segment gap — mark green/yellow
    if gap_absorb_mode and overflow_pct <= _VIDEO_ADAPT_MAX_PCT:
        ok = True
        seg["container_status"] = "yellow"   # acceptable overflow absorbed by gap
        seg["slot_overflow"] = False
    elif fits and overflow_ms <= 0:
        ok = True
        seg["container_status"] = _overflow_status(overflow_pct)
        seg["slot_overflow"] = False
    else:
        ok = overflow_pct <= _VIDEO_ADAPT_MAX_PCT   # video_adapt segments still "ok"
    if not ok:
        _log_dub_slot_fit(task_id, idx, "warn", slot_ms, fitted_ms, stretch_ratio)
        seg["container_status"] = "red"
        seg["slot_overflow"] = True
    elif "container_status" not in seg:
        seg["container_status"] = "yellow"
        seg["slot_overflow"] = False

    seg["fitted_ms"] = fitted_ms
    seg["overflow_ms"] = overflow_ms
    seg["overflow_pct"] = overflow_pct
    _finalize_slot_fit_timing_meta(
        seg,
        prep_meta,
        slot_fit_tts_ms=tts_ms,
        slot_fit_text=cur_text,
        slot_fit_compressed=False,
        iterations=iterations or None,
    )
    _scheduler_set_segment_slot(
        seg, start_ms=start_ms, end_ms=end_ms, task_info=task_info
    )
    seg["slot_fit_attempts"] = len(iterations)

    return {
        "ok": ok,
        "text": cur_text,
        "file": seg.get("file"),
        "fitted_file": fit_dest,
        "tts_ms": tts_ms,
        "fitted_ms": fitted_ms,
        "slot_ms": slot_ms,
        "overflow_ms": overflow_ms,
        "overflow_pct": overflow_pct,
        "video_stretch_ratio": video_stretch_ratio,
        "meta": prep_meta,
        "iterations": iterations,
        "work_dir": str(work_dir),
    }


def _apply_stretch_only_slot_fit(
    seg: dict,
    *,
    idx: int,
    start_ms: int,
    end_ms: int,
    slot_ms: int,
    work_root: Path,
    word_map: dict | None = None,
    task_id: str | None = None,
    task_info: dict | None = None,
) -> dict:
    """Time-stretch existing TTS into slot without regenerating speech (no speech trim)."""
    from engines.regeneration import _overflow_status
    from engines.timing_fit import prepare_dub_segment_audio

    file_name = seg.get("file")
    if not file_name:
        return {"ok": False, "overflow_pct": 100.0}

    src = _artifacts_dir(task_info) / Path(str(file_name)).name
    if not src.is_file():
        src = _resolve_segment_audio_path(file_name, task_info)
    if not src.is_file():
        return {"ok": False, "overflow_pct": 100.0}

    seg_work = work_root / f"seg_{idx}_stretch"
    seg_work.mkdir(parents=True, exist_ok=True)
    fitted_path, fit_meta = prepare_dub_segment_audio(
        src,
        slot_ms,
        seg_work,
        max_atempo=_DUB_MAX_ATEMPO,
        tolerance_ms=_DUB_SLOT_TOLERANCE_MS,
    )
    fitted_ms = _measure_tts_file_ms(Path(fitted_path).name)
    if fitted_ms <= 0:
        try:
            fitted_ms = len(AudioSegment.from_file(str(fitted_path)))
        except Exception:
            fitted_ms = 0

    stretch_ratio = float(fit_meta.get("atempo") or 1.0)
    _log_dub_slot_fit(task_id, idx, "stretch", slot_ms, fitted_ms, stretch_ratio)

    slot_limit = slot_ms + _DUB_SLOT_TOLERANCE_MS
    overflow_ms = max(0, fitted_ms - slot_limit)
    overflow_pct = round(100.0 * overflow_ms / max(slot_ms, 1), 1)

    fit_dest = _commit_fitted_wav(
        Path(fitted_path),
        idx,
        task_info=task_info,
        segment_id=str(seg.get("segment_id") or ""),
    )
    if not fit_dest:
        return {"ok": False, "overflow_pct": overflow_pct, "error": "fitted_copy_failed"}
    seg["fitted_file"] = fit_dest
    seg["fitted_ms"] = fitted_ms
    seg["overflow_ms"] = overflow_ms
    seg["overflow_pct"] = overflow_pct
    seg["container_status"] = _overflow_status(overflow_pct)
    fit_meta = dict(fit_meta or {})
    fit_meta["slot_fit_tts_ms"] = fitted_ms
    seg["timing_meta"] = fit_meta
    _scheduler_set_segment_slot(
        seg, start_ms=start_ms, end_ms=end_ms, task_info=task_info
    )
    seg["slot_fit_attempts"] = 0
    tm = dict(seg.get("timing_meta") or {})
    tm["slot_fit_stretch_only"] = True
    seg["timing_meta"] = tm

    return {
        "ok": overflow_ms <= 0,
        "overflow_pct": overflow_pct,
        "overflow_ms": overflow_ms,
    }


def _pipeline_slot_fit_segments(
    segments_data: list,
    timing_map: list,
    *,
    voice: str,
    target_lang: str,
    source_segments: list,
    tts_files: list | None = None,
    tts_rate: str | None = None,
    tts_pitch: str | None = None,
    max_attempts: int = 3,
    task_id: str | None = None,
    task_info: dict | None = None,
    skip_text_compression: bool = False,
) -> dict:
    """
    Mandatory segment audio prep before MP4 assembly (Dub module).
    Order: trailing silence trim → stretch (≤1.15x) → compress text → regen TTS → warn.
    Never trims speech content.
    """
    from engines.regeneration import _overflow_status

    stats = {
        "total": 0,
        "already_fit": 0,
        "compressed": 0,
        "overflow": 0,
        "failed": 0,
    }
    work_root = _artifacts_dir(task_info) / "slot_fit" / (task_id or uuid.uuid4().hex[:8])
    work_root.mkdir(parents=True, exist_ok=True)
    tts_files = tts_files if tts_files is not None else []
    compress_rounds = 0 if skip_text_compression else max(0, int(max_attempts) - 1)

    for idx, seg in enumerate(segments_data):
        if seg.get("merged_into") is not None or not seg.get("file"):
            continue
        stats["total"] += 1
        start_ms, end_ms = _parse_segment_slot_timing(seg, idx, timing_map)
        slot_ms = max(1, end_ms - start_ms)

        # Gap between this segment's end and the next segment's start —
        # used by _prepare_segment_audio_for_mux to decide if overflow can
        # be absorbed naturally (gap_absorb) without touching speech speed.
        next_start_ms: int | None = None
        for nxt_idx in range(idx + 1, len(segments_data)):
            nxt = segments_data[nxt_idx]
            if nxt.get("merged_into") is not None:
                continue
            nxt_start, _ = _parse_segment_slot_timing(nxt, nxt_idx, timing_map)
            next_start_ms = nxt_start
            break
        gap_after_ms = max(0, (next_start_ms - end_ms)) if next_start_ms is not None else 0

        text = str(seg.get("text") or "").strip()
        fit_key = _slot_fit_content_key(text, voice, slot_ms, tts_rate, tts_pitch)
        cached_fit = seg.get("fitted_file")
        if (
            _segment_slot_fit_key(seg) == fit_key
            and cached_fit
            and (_artifacts_dir(task_info) / Path(str(cached_fit)).name).is_file()
        ):
            fitted_ms = int(seg.get("fitted_ms") or 0) or _measure_tts_file_ms(cached_fit)
            fits, measured = _premux_segment_fits(
                _artifacts_dir(task_info) / Path(str(cached_fit)).name, slot_ms
            )
            if measured > 0:
                fitted_ms = measured
            overflow_ms = max(0, fitted_ms - (slot_ms + _DUB_SLOT_TOLERANCE_MS))
            overflow_pct = round(100.0 * overflow_ms / max(slot_ms, 1), 1)
            seg["overflow_ms"] = overflow_ms
            seg["overflow_pct"] = overflow_pct
            seg["container_status"] = _overflow_status(overflow_pct)
            _scheduler_set_segment_slot(
                seg,
                start_ms=start_ms,
                end_ms=end_ms,
                segments_data=segments_data,
                task_info=task_info,
            )
            stats["already_fit"] += 1
            if fits:
                continue

        if not text:
            stats["failed"] += 1
            seg["overflow_pct"] = 100.0
            seg["container_status"] = "red"
            seg["slot_overflow"] = True
            _log_dub_slot_fit(task_id, idx, "warn", slot_ms, 0)
            continue

        if task_id:
            _update_progress_detail(
                task_id,
                phase="slot_fit",
                current_segment=idx + 1,
                total_segments=len(segments_data),
            )

        seg_work = work_root / f"seg_{idx}"
        seg_work.mkdir(parents=True, exist_ok=True)
        emotion = seg.get("emotion") or (seg.get("tts_emotion") or {}).get("emotion")

        from engines.pipeline_segment_watchdog import run_segment_bounded

        def _run_slot_fit() -> dict:
            return _prepare_segment_audio_for_mux(
                seg,
                idx=idx,
                start_ms=start_ms,
                end_ms=end_ms,
                slot_ms=slot_ms,
                voice=voice,
                target_lang=target_lang,
                source_hint=source_segments[idx] if idx < len(source_segments) else "",
                tts_rate=tts_rate,
                tts_pitch=tts_pitch,
                emotion=str(emotion) if emotion else None,
                work_dir=seg_work,
                task_id=task_id,
                task_info=task_info,
                max_compress_rounds=compress_rounds,
                gap_after_ms=gap_after_ms,
            )

        watch = run_segment_bounded(
            task_id=task_id or "",
            phase="slot_fit",
            segment_index=idx,
            stage="slot_fit",
            fn=_run_slot_fit,
            fallback=lambda: {
                "ok": False,
                "error": "segment_watchdog_timeout",
                "overflow_pct": 100.0,
            },
        )
        result = watch.value
        if watch.timed_out or watch.error:
            stretch = _apply_stretch_only_slot_fit(
                seg,
                idx=idx,
                start_ms=start_ms,
                end_ms=end_ms,
                slot_ms=slot_ms,
                work_root=work_root,
                task_id=task_id,
                task_info=task_info,
            )
            tm = dict(seg.get("timing_meta") or {})
            tm["slot_fit_error"] = watch.error or "timeout"
            seg["timing_meta"] = tm
            if stretch.get("ok") is not False or seg.get("fitted_file"):
                result = {"ok": stretch.get("ok", True), **stretch}
            if task_id:
                _open_ddf.record_agent(
                    task_id, "SlotFit", called=True, success=False,
                    error=watch.error or "timeout",
                    fallback_used=True, segment_idx=idx,
                    decision="fit_skipped_raw_tts",
                )
                _open_ddf.mark_segment_attention(task_id, idx, "fit_skipped")

        if result.get("file"):
            dest_name = Path(str(result["file"])).name
            if dest_name not in tts_files:
                tts_files.append(dest_name)

        _set_segment_slot_fit_key(
            seg,
            _slot_fit_content_key(
                str(seg.get("text") or text), voice, slot_ms, tts_rate, tts_pitch
            ),
        )

        overflow_pct = float(result.get("overflow_pct") or 100.0)
        if not result.get("ok"):
            stats["overflow"] += 1
            seg["slot_overflow"] = True
        else:
            stats["compressed"] += 1
            seg["slot_overflow"] = False

        logger.info(
            "Task %s slot_fit idx=%d attempts=%d overflow=%.1f%%",
            task_id,
            idx,
            seg.get("slot_fit_attempts"),
            overflow_pct,
        )

    # ── Block merge (LAST RESORT only) ────────────────────────────────────────
    # Closed Loop Timing forbids cascade neighbor shifts as a primary fix.
    # Enable only via VM_ALLOW_BLOCK_MERGE=1 after rewrite/pause failed.
    from engines.closed_loop_timing import allow_block_merge

    merge_count = 0
    if allow_block_merge():
        merge_count = _plan_block_merges(segments_data, timing_map)
        if merge_count:
            logger.warning(
                "Task %s: %d block merges planned (VM_ALLOW_BLOCK_MERGE=1 last resort)",
                task_id,
                merge_count,
            )
    else:
        logger.info(
            "Task %s: block merge cascade disabled — Closed Loop Timing owns fit",
            task_id,
        )

    # ── Final sync validation ─────────────────────────────────────────────────
    validation_warnings = _validate_sync_plan(segments_data, timing_map)
    if validation_warnings:
        logger.warning("Task %s: %d sync validation issues", task_id, len(validation_warnings))

    repaired = _repair_missing_fitted_files(
        segments_data,
        task_info=task_info,
        task_id=task_id,
    )
    if repaired:
        stats["repaired_fitted"] = repaired

    return stats


def _build_gap_adjusted_track_no_double_soft_sync(
    segment_paths: list,
    timing_map: list,
    skip_soft_sync_flags: list[bool],
    text_hints: list[str] | None = None,
    allow_overflow_flags: list[bool] | None = None,
    *,
    happy_path: bool = False,
    max_atempo: float | None = None,
    **kwargs,
):
    """build_gap_adjusted_track wrapper — skip soft_sync for slot-fitted segments.

    Passes text_hints to fit_segment_audio for natural-pause-based padding
    instead of full-slot silence filling.

    Anti-bleed (Root Cause Audit): pre_fitted alone must NOT skip hard-cap trim.
    Slot-fit may still leave overflow_ms>0; skipping trim_overlap overlays full WAV
    into the next segment. Only video_adapt / gap_absorb may keep no_speech_trim.

    Happy Path (TZ text-fit / Stage 17): never chop speech — no_speech_trim=True,
    atempo up ≤1.15, atempo down ≥0.95 to kill dead air.
    """
    import engines.timing_fit as timing_fit_mod

    orig_fit = timing_fit_mod.fit_segment_audio
    call_idx = {"i": 0}
    try:
        from engines.happy_path import HAPPY_PATH_MAX_ATEMPO as _HP_ATEMPO
    except Exception:
        _HP_ATEMPO = 1.15
    fit_max = float(max_atempo if max_atempo is not None else (
        _HP_ATEMPO if happy_path else _DUB_MAX_ATEMPO
    ))
    # Happy Path: hard 1.15; advanced may go to 1.20 absolute ceiling.
    # Do NOT clamp with max(1.0, …) — underfill uses atempo < 1.0.
    _ceil = 1.15 if happy_path else 1.20
    fit_max = min(_ceil, max(0.01, fit_max))

    def _fit_with_skip(tts_path, slot_start, slot_end, next_start=None, work_dir=None, **fit_kw):
        pos = call_idx["i"]
        call_idx["i"] += 1
        pre_fitted = pos < len(skip_soft_sync_flags) and skip_soft_sync_flags[pos]
        allow_overflow = bool(
            allow_overflow_flags
            and pos < len(allow_overflow_flags)
            and allow_overflow_flags[pos]
        )
        # Happy Path: never hard-trim speech. Advanced: trim unless absorb mode.
        if happy_path:
            fit_kw["no_speech_trim"] = True
            # Force True (caller may pass allow_atempo=False via flags).
            fit_kw["allow_atempo"] = True
        else:
            fit_kw.setdefault("no_speech_trim", allow_overflow)
        # Always enforce ceiling (setdefault loses when build_gap_adjusted_track
        # already passes absolute default 1.20).
        fit_kw["max_atempo"] = fit_max
        if pre_fitted:
            # Skip soft_sync re-pass; Stage 17 still allows underfill atempo_slow.
            fit_kw["_skip_soft_sync"] = True
        # Natural pause hint — avoids padding the full slot with silence
        if text_hints and pos < len(text_hints):
            fit_kw.setdefault("text_hint", text_hints[pos])
        return orig_fit(
            tts_path,
            slot_start,
            slot_end,
            next_start,
            work_dir=work_dir,
            **fit_kw,
        )

    timing_fit_mod.fit_segment_audio = _fit_with_skip
    try:
        kwargs = dict(kwargs)
        kwargs["max_atempo"] = fit_max
        return timing_fit_mod.build_gap_adjusted_track(segment_paths, timing_map, **kwargs)
    finally:
        timing_fit_mod.fit_segment_audio = orig_fit


def _build_timed_dub_track(
    segments_data: list,
    timing_map: list,
    target_duration_ms,
    task_id: str,
    style_params: dict | None = None,
    on_segment_progress=None,
):
    """Gap-aware dub track on silence base; logs dub_segment_log + dub_timing_fit_log."""
    from engines.timing_fit import _segment_start_delays

    # TZ: master length from ffprobe video — never from last-segment end alone.
    task_info = None
    try:
        with STATE_LOCK:
            _t = AUTO_TASKS.get(task_id) if task_id else None
            if _t and isinstance(_t.get("info"), dict):
                task_info = _t["info"]
    except Exception:
        task_info = None
    video_ms = int(target_duration_ms or 0)
    try:
        vpath = ""
        if task_info:
            vpath = str(
                task_info.get("video_path_backup")
                or task_info.get("video_path")
                or ""
            )
            video_ms = int(
                task_info.get("video_duration_ms")
                or task_info.get("target_duration_ms")
                or video_ms
                or 0
            )
        if vpath:
            probed = _video_duration_ms(vpath)
            if probed and probed > 0:
                video_ms = int(probed)
                if task_info is not None:
                    task_info["video_duration_ms"] = video_ms
                    task_info["target_duration_ms"] = video_ms
    except Exception as _vd_exc:
        logger.debug("ffprobe video duration skipped: %s", _vd_exc)
    target_duration_ms = video_ms or target_duration_ms

    # Keep placements inside the video mux window (speech-expanded splits).
    try:
        _vid_clamp = int(target_duration_ms or 0)
        if _vid_clamp > 0:
            from engines.segment_timing_qa import clamp_timeline_to_video_duration

            clamp_timeline_to_video_duration(
                segments_data, timing_map, _vid_clamp
            )
    except Exception as _clamp_exc:
        logger.debug("pre-mix video clamp skipped: %s", _clamp_exc)

    # TZ Root Cause Audit — diagnostic only (no mutation)
    try:
        from engines.pipeline_integrity.timing_lifecycle_audit import (
            dump_pre_merge_timing_audit,
        )

        dump_pre_merge_timing_audit(
            segments_data,
            task_id=str(task_id or ""),
            timing_map=timing_map,
            source="pre_build_timed_dub_track",
        )
    except Exception:
        pass

    # Stage 24/26: forced ripple when placement overlaps (>80ms) before mix.
    # Three passes — first pass shifts, second/third clear the cascade tail so
    # residual overlap count stays well under the TZ target (≤ 15).
    try:
        from engines.conflict_resolver import ripple_shift_segment_dicts

        _ripple = ripple_shift_segment_dicts(list(segments_data or []))
        _ripple2 = ripple_shift_segment_dicts(list(segments_data or []))
        _ripple3 = ripple_shift_segment_dicts(list(segments_data or []))
        _ripple = {
            **_ripple,
            "ripple_shifted": int(_ripple.get("ripple_shifted") or 0)
            + int(_ripple2.get("ripple_shifted") or 0)
            + int(_ripple3.get("ripple_shifted") or 0),
            "overlap_after_ripple": int(
                _ripple3.get("overlap_after_ripple")
                or _ripple2.get("overlap_after_ripple")
                or _ripple.get("overlap_after_ripple")
                or 0
            ),
            "overlap_count": int(
                _ripple3.get("overlap_count")
                or _ripple3.get("overlap_after_ripple")
                or 0
            ),
            "pass2": _ripple2,
            "pass3": _ripple3,
        }
        if task_info is not None:
            task_info["stage22_ripple"] = _ripple
            task_info["stage23_ripple"] = _ripple
            task_info["overlap_count"] = int(
                _ripple.get("overlap_count")
                or _ripple.get("overlap_after_ripple")
                or 0
            )
        if int(_ripple.get("ripple_shifted") or 0) > 0 or int(
            _ripple.get("overlap_after_ripple") or 0
        ) > 0:
            logger.info(
                "Task %s: stage24 ripple_shift shifted=%s severe=%s residual=%s",
                task_id,
                _ripple.get("ripple_shifted"),
                _ripple.get("severe_shifted"),
                _ripple.get("overlap_after_ripple"),
            )
            if task_id:
                with STATE_LOCK:
                    _t = AUTO_TASKS.get(task_id)
                    if _t and isinstance(_t.get("info"), dict):
                        _t["info"]["stage22_ripple"] = _ripple
                        _t["info"]["stage23_ripple"] = _ripple
                        _t["info"]["overlap_count"] = int(
                            _ripple.get("overlap_count")
                            or _ripple.get("overlap_after_ripple")
                            or 0
                        )
            try:
                residual = int(_ripple.get("overlap_after_ripple") or 0)
                for _s in segments_data or []:
                    if isinstance(_s, dict):
                        meta = dict(_s.get("stage23") or {})
                        meta["overlap_after_ripple"] = residual
                        _s["stage23"] = meta
            except Exception:
                pass
    except Exception as _ripple_exc:
        logger.debug("stage24 ripple_shift skipped: %s", _ripple_exc)

    style_params = style_params or {}
    if task_info is None and task_id:
        with STATE_LOCK:
            _task = AUTO_TASKS.get(task_id)
            if _task:
                task_info = _task.get("info") or {}

    # Stage 40 — ONE pre-mux order (Simple and main): repair → soft-pad →
    # last-resort → census from absolute paths → re-pad if still missing.
    def _mux_resolve(p: str) -> str:
        try:
            return str(_resolve_segment_audio_path(p) or p)
        except Exception:
            return p

    _voice = str(
        (task_info or {}).get("voice")
        or next(
            (
                s.get("voice")
                for s in (segments_data or [])
                if isinstance(s, dict) and s.get("voice")
            ),
            "",
        )
        or ""
    )
    try:
        _prepare_segments_audio_before_mux(
            list(segments_data or []),
            task_info=task_info if isinstance(task_info, dict) else {},
            task_id=task_id,
            timing_map=timing_map,
            voice=_voice,
            resolve_path=_mux_resolve,
        )
    except Exception as _prep_exc:
        logger.warning(
            "Task %s: pre-mux audio prepare failed: %s (mux still continues)",
            task_id,
            _prep_exc,
        )

    # Stage 31: ripple AFTER pads so silence_pad durations are real, then
    # overlap_count is taken from the final placement (not a pre-pad census).
    try:
        from engines.conflict_resolver import ripple_shift_segment_dicts as _ripple_fn

        _rp1 = _ripple_fn(list(segments_data or []))
        _rp2 = _ripple_fn(list(segments_data or []))
        _rp3 = _ripple_fn(list(segments_data or []), max_shift_ms=0)
        _ov_final = int(
            _rp3.get("overlap_count")
            or _rp3.get("overlap_after_ripple")
            or 0
        )
        if task_info is not None:
            task_info["stage31_post_pad_ripple"] = {
                "ripple_shifted": int(_rp1.get("ripple_shifted") or 0)
                + int(_rp2.get("ripple_shifted") or 0)
                + int(_rp3.get("ripple_shifted") or 0),
                "overlap_count": _ov_final,
            }
            task_info["overlap_count"] = _ov_final
        if _ov_final:
            logger.info(
                "Task %s: stage31 post-pad ripple residual overlap_count=%s",
                task_id,
                _ov_final,
            )
    except Exception as _rp_exc:
        logger.debug("stage31 post-pad ripple skipped: %s", _rp_exc)

    # Stage 28/32 — UK Simple max_atempo is 1.08 even outside happy-path
    # (diag 2286c82f mux logs still showed atempo=1.15).
    _tgt_ta = str((task_info or {}).get("target_lang") or (task_info or {}).get("lang") or "").split("-")[0].lower()
    try:
        _policy_max = float((task_info or {}).get("max_atempo") or _UK_MUX_MAX_ATEMPO)
    except (TypeError, ValueError):
        _policy_max = _UK_MUX_MAX_ATEMPO
    if _tgt_ta == "uk":
        _mux_cap = min(_UK_MUX_MAX_ATEMPO, _policy_max if _policy_max > 0 else _UK_MUX_MAX_ATEMPO)
    else:
        _mux_cap = _policy_max or _DUB_MAX_ATEMPO
    if task_info is not None:
        task_info["timing_max_atempo"] = _mux_cap

    _happy_path_timing = False
    try:
        from engines.happy_path import skip_advanced_text_shorteners as _hp_tm

        _happy_path_timing = bool(_hp_tm(dict(task_info or {})))
    except Exception:
        _happy_path_timing = True
    if _happy_path_timing and task_info is not None:
        task_info["timing_mode"] = "happy_path_no_speech_trim"
        # Stage 28 §D1 — UK Simple max_atempo honours the policy stamp
        # (1.05 for uk, 1.15 legacy) with 1.08 as the emergency hard ceiling.
        _tgt_ta = str(task_info.get("target_lang") or task_info.get("lang") or "").split("-")[0].lower()
        try:
            _policy_max = float(task_info.get("max_atempo") or 1.08)
        except (TypeError, ValueError):
            _policy_max = 1.08
        if _tgt_ta == "uk":
            task_info["timing_max_atempo"] = min(1.08, _policy_max if _policy_max > 0 else 1.05)
        else:
            task_info["timing_max_atempo"] = _policy_max or 1.08

    segment_paths: list[str] = []
    placed_seg_indices: list[int] = []
    skip_soft_sync_flags: list[bool] = []
    allow_overflow_flags: list[bool] = []
    aligned_timing: list = []
    allow_atempo_flags: list[bool] = []
    place_delays: list[int] = []
    lead_ins: list[int] = []
    text_hints: list[str] = []      # for natural-pause calculation in fit_segment_audio
    log_entries: list[str] = []

    for idx, seg in enumerate(segments_data):
        if idx >= len(timing_map):
            break
        if seg.get("merged_into") is not None:
            continue

        custom_timing = seg.get("tts_timing")
        if custom_timing and isinstance(custom_timing, (list, tuple)) and len(custom_timing) >= 2:
            start_ms, end_ms = int(custom_timing[0]), int(custom_timing[1])
            _place_source = "tts_timing"
        elif seg.get("start_ms") is not None and seg.get("end_ms") is not None:
            # Prefer Scheduler edges over raw Whisper timing_map (Root Cause Audit).
            start_ms, end_ms = int(seg["start_ms"]), int(seg["end_ms"])
            _place_source = "scheduler"
        else:
            start_ms, end_ms = _parse_timing(timing_map[idx])
            _place_source = "timing_map"

        # Block-merge: if previous segment claimed part of this segment's slot,
        # use the adjusted start — but never invert the window (diag 2286c82f).
        _adj = seg.get("merge_adjusted_start")
        start_ms, end_ms = _clamp_placement_window(
            start_ms,
            end_ms,
            merge_adjusted_start=int(_adj) if _adj not in (None, "", False) else None,
        )
        if _adj not in (None, "", False) and start_ms == int(_adj):
            _place_source = f"{_place_source}+merge_adjusted_start"

        # If adapted (or need_adaptation forced by duration delta): bind end to TTS length,
        # never keep Whisper/original_duration_ms as the clip end.
        # Cap at next neighbor / video duration so mux -t cannot cut the ending.
        _adapted = bool(seg.get("adaptation_executed")) or bool(
            (seg.get("adaptation_decision") or {}).get("need_adaptation")
        ) or bool(seg.get("need_adaptation"))
        if _adapted:
            from engines.dub_engine_v2.adaptation_decision import segment_tts_duration_ms

            _tts_len = int(
                seg.get("final_tts_duration_ms")
                or segment_tts_duration_ms(seg)
                or 0
            )
            if _tts_len > 0:
                end_ms = int(start_ms) + _tts_len
                _cap = None
                if idx + 1 < len(timing_map):
                    try:
                        _cap = int(_parse_timing(timing_map[idx + 1])[0])
                    except Exception:
                        _cap = None
                if segments_data is not None and idx + 1 < len(segments_data):
                    try:
                        _ns = segments_data[idx + 1].get("start_ms")
                        if _ns is not None:
                            _cap = (
                                min(int(_cap), int(_ns))
                                if _cap is not None
                                else int(_ns)
                            )
                    except Exception:
                        pass
                _vid_cap = None
                try:
                    _vid_cap = int(
                        (task_info or {}).get("target_duration_ms")
                        or target_duration_ms
                        or 0
                    ) or None
                except Exception:
                    _vid_cap = None
                if _cap is not None and _cap > int(start_ms):
                    end_ms = min(end_ms, int(_cap))
                if _vid_cap is not None and _vid_cap > int(start_ms):
                    end_ms = min(end_ms, int(_vid_cap))
                end_ms = max(end_ms, int(start_ms) + 1)
                _place_source = f"{_place_source}+tts_duration_end"
                try:
                    _scheduler_set_segment_slot(
                        seg,
                        start_ms=start_ms,
                        end_ms=end_ms,
                        task_info=task_info,
                    )
                except Exception:
                    seg["end_ms"] = end_ms
                    seg["slot_ms"] = max(1, end_ms - start_ms)

        text = str(seg.get("text") or "").strip()
        audio_candidates = [
            name
            for name in (seg.get("fitted_file"), seg.get("file"))
            if name
        ]
        tts_dur = 0
        placed = False
        premux_ok = False
        pre_fitted = bool(seg.get("fitted_file"))
        path = None
        file_name = None

        for candidate_name in audio_candidates:
            from engines.dubbing_engine.session_adapter import resolve_session_audio
            from engines.pipeline_integrity.audio_presence import (
                MIN_AUDIO_BYTES,
                audio_stat,
            )

            candidate_path = resolve_session_audio(
                candidate_name,
                task_info=task_info,
                default_dir=OUTPUT_DIR,
                segment_index=idx,
            )
            ok_c, size_c = audio_stat(candidate_path)
            if ok_c and size_c >= MIN_AUDIO_BYTES:
                path = candidate_path
                file_name = candidate_name
                break
            if candidate_path.is_file() and size_c < MIN_AUDIO_BYTES:
                logger.error(
                    "Task %s: refusing tiny wav idx=%s path=%s size=%s",
                    task_id,
                    idx,
                    candidate_path,
                    size_c,
                )

        if path is not None and file_name:
            slot_ms = max(1, end_ms - start_ms)
            try:
                tts_dur = len(AudioSegment.from_file(str(path)))
            except Exception:
                tts_dur = 0
            premux_ok, measured = _premux_segment_fits(path, slot_ms)
            if measured > 0:
                tts_dur = measured
            if tts_dur > 0 and not premux_ok:
                from engines.regeneration import _overflow_status

                _log_dub_slot_fit(task_id, idx, "warn", slot_ms, tts_dur)
                overflow_ms = max(0, tts_dur - (slot_ms + _DUB_SLOT_TOLERANCE_MS))
                overflow_pct = round(100.0 * overflow_ms / max(slot_ms, 1), 1)
                seg["overflow_ms"] = overflow_ms
                seg["overflow_pct"] = overflow_pct
                seg["container_status"] = _overflow_status(overflow_pct)
                if overflow_pct > 15:
                    seg["container_status"] = "red"
                seg["slot_overflow"] = True
            if tts_dur > 0:
                segment_paths.append(str(path))
                placed_seg_indices.append(idx)
                skip_soft_sync_flags.append(pre_fitted)
                _mode = str(seg.get("video_adapt_mode") or "")
                allow_overflow_flags.append(_mode in ("video_adapt", "gap_absorb"))
                aligned_timing.append({"start": start_ms, "end": end_ms})
                # Stage 31 UK: atempo only when |delta| still > 150 after text-fit.
                _slot_ms_flag = max(1, int(end_ms) - int(start_ms))
                _need_speed = abs(int(tts_dur or 0) - _slot_ms_flag) > 150
                _uk_ta = str(
                    (task_info or {}).get("target_lang")
                    or (task_info or {}).get("lang")
                    or ""
                ).split("-")[0].lower() == "uk"
                if _uk_ta:
                    allow_atempo_flags.append(
                        bool(_need_speed or seg.get("allow_atempo") or seg.get("needs_atempo_clamp"))
                    )
                else:
                    allow_atempo_flags.append(
                        True
                        if _happy_path_timing
                        else bool(seg.get("allow_atempo", False))
                    )
                prosody = seg.get("prosody") or {}
                place_delays.append(
                    int(seg.get("place_delay_ms") or prosody.get("place_delay_ms") or 0)
                )
                lead_ins.append(
                    int(seg.get("lead_in_ms") or prosody.get("lead_in_ms") or 0)
                )
                text_hints.append(text)
                placed = True

        log_entries.append(
            f"idx={idx} text_len={len(text)} tts_file={file_name or '-'} "
            f"tts_dur_ms={tts_dur} place_start={start_ms} place_end={end_ms} "
            f"slot_ms={max(1, end_ms - start_ms)} premux={'ok' if premux_ok else 'fail'} "
            f"allow_atempo={'yes' if seg.get('allow_atempo') else 'no'} "
            f"pre_fitted={'yes' if pre_fitted else 'no'} "
            f"placed={'yes' if placed else 'no'} "
            f"block_merged={'yes' if seg.get('block_merged_with_next') else 'no'} "
            f"merge_adjusted_start={seg.get('merge_adjusted_start', '-')}"
        )

    _write_dub_segment_log(task_id, log_entries)

    # Stage 40 — backstop `duration_control_used` when |slot − tts| > 200ms
    # would otherwise stay "none". Never rewrite a more specific upstream stamp.
    try:
        from engines.text_slot_fit import backstop_duration_control_used as _dc_backstop

        for _s_dc in segments_data or []:
            if not isinstance(_s_dc, dict):
                continue
            if _s_dc.get("merged_into") is not None or _s_dc.get("merged_into_id"):
                continue
            _dc_backstop(_s_dc)
    except Exception as _dc_exc:
        logger.debug("Task %s: duration_control backstop skipped: %s", task_id, _dc_exc)

    from engines.dubbing_engine.tts_handoff_diag import (
        log_empty_tts_diagnosis,
        log_track_builder_input,
    )

    log_track_builder_input(
        task_id or "-",
        segment_paths=segment_paths,
        segments_data=segments_data,
        source="auto_dub._build_timed_dub_track",
    )

    if not segment_paths:
        log_empty_tts_diagnosis(
            task_id or "-",
            task_info=task_info,
            segments_data=segments_data,
            segment_paths=segment_paths,
            stage="auto_dub._build_timed_dub_track",
        )
        # Never silently mux a silent/empty dub after LANGUAGE_MISMATCH blanking
        if task_info is not None:
            blanked = task_info.get("language_mismatch_blanked") or []
            if blanked or task_info.get("language_mismatch_reports"):
                task_info["export_blocked_reason"] = (
                    "LANGUAGE_MISMATCH_EMPTY_TTS — export refused "
                    "(all voiceable segments blanked)."
                )
        return None, ["EXPORT_BLOCKED_EMPTY_TTS"], {"ok": False, "fitted_overlap_count": 0}

    delays = _segment_start_delays(
        len(segment_paths),
        int(style_params.get("reply_start_delay_ms") or 0),
        int(style_params.get("reply_start_delay_jitter_ms") or 0),
    )
    combined_delays = [
        int(delays[i]) + int(place_delays[i] if i < len(place_delays) else 0)
        for i in range(len(segment_paths))
    ]

    timed_audio, fit_logs, overlap_report = _build_gap_adjusted_track_no_double_soft_sync(
        segment_paths=segment_paths,
        timing_map=aligned_timing,
        skip_soft_sync_flags=skip_soft_sync_flags,
        allow_overflow_flags=allow_overflow_flags,
        video_duration_ms=target_duration_ms,
        log_path=DUB_TIMING_FIT_LOG,
        task_id=task_id,
        allow_atempo_flags=allow_atempo_flags,
        start_delays_ms=combined_delays,
        lead_in_ms_list=lead_ins,
        text_hints=text_hints,
        max_atempo=float(
            (task_info or {}).get("timing_max_atempo")
            or (
                _UK_MUX_MAX_ATEMPO
                if str((task_info or {}).get("target_lang") or "").startswith("uk")
                else _DUB_MAX_ATEMPO
            )
        ),
        happy_path=_happy_path_timing,
        on_segment_progress=on_segment_progress,
    )
    # Stage 40 / pyVideoTrans: dub track must cover 100% of video_ms.
    try:
        from engines.oss_production import pad_master_to_video_ms

        _vid_pad = int(target_duration_ms or 0)
        if timed_audio is not None and _vid_pad > 0:
            _sr = getattr(timed_audio, "frame_rate", None) or 24000
            timed_audio = pad_master_to_video_ms(
                timed_audio, _vid_pad, sample_rate=int(_sr)
            )
            if isinstance(overlap_report, dict):
                overlap_report["track_duration_ms"] = int(len(timed_audio))
                overlap_report["video_duration_ms"] = _vid_pad
    except Exception as _pad_m_exc:
        logger.debug("pad_master_to_video_ms skipped: %s", _pad_m_exc)
    # Stamp video/track/tail diagnostics for tts_pipeline + final_dub_qa.
    try:
        track_ms = int(len(timed_audio)) if timed_audio is not None else 0
        vid_ms = int(target_duration_ms or 0)
        if isinstance(overlap_report, dict):
            track_ms = int(overlap_report.get("track_duration_ms") or track_ms or 0)
            vid_ms = int(overlap_report.get("video_duration_ms") or vid_ms or 0)
        if task_info is not None:
            if vid_ms > 0:
                task_info["video_duration_ms"] = vid_ms
                task_info["target_duration_ms"] = vid_ms
            if track_ms > 0:
                task_info["track_duration_ms"] = track_ms
            task_info["tail_gap_ms"] = max(0, vid_ms - track_ms) if vid_ms and track_ms else 0
            if task_info["tail_gap_ms"] > 500:
                task_info["track_duration_warning"] = "track_shorter_than_video"
    except Exception as _stamp_exc:
        logger.debug("track duration stamp skipped: %s", _stamp_exc)
    # Persist per-segment timing diagnostics (TZ Stage 3 logging).
    try:
        placements = list((overlap_report or {}).get("fitted_placements") or [])
        timing_rows = []
        for place in placements:
            if not isinstance(place, dict):
                continue
            _slot = place.get("slot_ms")
            if _slot is None and place.get("slot_end_ms") is not None:
                _slot = int(place.get("slot_end_ms") or 0) - int(
                    place.get("original_start_ms") or 0
                )
            timing_rows.append(
                {
                    "idx": place.get("idx"),
                    "slot_ms": _slot,
                    "tts_ms": place.get("tts_ms"),
                    "speech_ms": place.get("speech_ms"),
                    "fitted_ms": place.get("fitted_ms"),
                    "atempo": place.get("atempo"),
                    "overflow_ms": place.get("overflow_ms"),
                    "strategy": place.get("strategy"),
                    "speech_trimmed": place.get("speech_trimmed"),
                    "no_speech_trim": place.get("no_speech_trim"),
                    "fill_ratio": place.get("fill_ratio"),
                    "underfill_ms": place.get("underfill_ms"),
                    "underfill_significant": place.get("underfill_significant"),
                    "slot_shrunk": place.get("slot_shrunk"),
                    "slot_ms_effective": place.get("slot_ms_effective"),
                    "fill_ratio_effective": place.get("fill_ratio_effective"),
                    "underfill_resolved_by_shrink": place.get(
                        "underfill_resolved_by_shrink"
                    ),
                    "dead_air_ms": place.get("dead_air_ms"),
                    "gap_close_ms": place.get("gap_close_ms"),
                }
            )
        # Stage 4: attach final_tts_text + text-fit preds for lip/scene diagnostics.
        if task_info is not None and timing_rows:
            _sd_log = list(task_info.get("segments_data") or [])
            _voice_log = str(
                task_info.get("pipeline_voice")
                or task_info.get("tts_voice")
                or ""
            )
            for _row in timing_rows:
                _i = int(_row.get("idx") if _row.get("idx") is not None else -1)
                if 0 <= _i < len(_sd_log) and isinstance(_sd_log[_i], dict):
                    _seg = _sd_log[_i]
                    _row["final_tts_text"] = str(
                        _seg.get("final_tts_text")
                        or _seg.get("tts_text")
                        or _seg.get("text")
                        or ""
                    )[:300]
                    _row["spoken_text_source"] = _seg.get("spoken_text_source") or (
                        "final_tts_text" if _seg.get("final_tts_text") else ""
                    )
                    _row["voice_id"] = str(
                        _seg.get("voice_id")
                        or _seg.get("assigned_voice")
                        or _seg.get("voice")
                        or _voice_log
                        or ""
                    )
                    # Stage 17: tts_text_hash must match Final hash.
                    try:
                        from engines.tts_text_authority import text_hash as _th

                        _final_for_hash = str(
                            _seg.get("final_tts_text")
                            or _seg.get("tts_text")
                            or _seg.get("final_text")
                            or ""
                        )
                        _row["tts_text_hash"] = str(
                            _seg.get("tts_text_hash") or _th(_final_for_hash) or ""
                        )
                        if _final_for_hash and not _seg.get("tts_text_hash"):
                            _seg["tts_text_hash"] = _row["tts_text_hash"]
                    except Exception:
                        _row["tts_text_hash"] = str(_seg.get("tts_text_hash") or "")
                    _tf = _seg.get("text_slot_fit") or {}
                    if isinstance(_tf, dict):
                        _row["predicted_ms_before"] = _tf.get("predicted_ms_before")
                        _row["predicted_ms_after"] = _tf.get("predicted_ms_after")
                        _row["text_fit_applied"] = _tf.get("text_fit_applied")
                        _row["meaning_truncated"] = _tf.get("meaning_truncated")
                        if _row.get("dead_air_ms") is None and _tf.get(
                            "dead_air_risk_ms"
                        ) is not None:
                            _row["dead_air_ms"] = int(_tf.get("dead_air_risk_ms") or 0)
                    # Stage 17 Review/trace stamps.
                    if _row.get("slot_ms") is not None:
                        _seg["slot_ms"] = int(_row["slot_ms"])
                    if _row.get("tts_ms") is not None:
                        _seg["tts_ms"] = int(_row["tts_ms"])
                    if _row.get("dead_air_ms") is not None:
                        _seg["dead_air_ms"] = int(_row["dead_air_ms"])
                    if _row.get("voice_id"):
                        _seg["voice_id"] = str(_row["voice_id"])
                # Ensure fill metrics even if placement omitted them.
                if _row.get("fill_ratio") is None:
                    try:
                        from engines.timing_fit import underfill_metrics as _ufm

                        _um = _ufm(
                            int(_row.get("speech_ms") or _row.get("tts_ms") or 0),
                            int(_row.get("slot_ms") or 0),
                        )
                        _row.update(_um)
                    except Exception:
                        pass
            atempos = [float(r.get("atempo") or 1.0) for r in timing_rows]
            if atempos and (max(atempos) > 1.1501 or min(atempos) < 0.949):
                logger.warning(
                    "Task %s: atempo outside Simple band 0.95–1.15 (min=%.3f max=%.3f)",
                    task_id,
                    min(atempos),
                    max(atempos),
                )
            _sig = [
                r
                for r in timing_rows
                if r.get("underfill_significant")
                and not r.get("underfill_resolved_by_shrink")
            ]
            _all_under = [
                int(r.get("underfill_ms") or 0)
                for r in timing_rows
                if int(r.get("underfill_ms") or 0) > 0
            ]
            task_info["timing_fit_segments"] = timing_rows
            task_info["underfill_count"] = len(
                [r for r in timing_rows if r.get("underfill_significant")]
            )
            task_info["underfill_unresolved_count"] = len(_sig)
            task_info["max_underfill_ms"] = max(_all_under) if _all_under else 0
            task_info["underfill_summary"] = {
                "count": task_info["underfill_count"],
                "unresolved": task_info["underfill_unresolved_count"],
                "max_underfill_ms": task_info["max_underfill_ms"],
                "slot_shrunk": sum(1 for r in timing_rows if r.get("slot_shrunk")),
                "fill_ok": sum(
                    1
                    for r in timing_rows
                    if float(r.get("fill_ratio") or 0) >= 0.80
                    or r.get("underfill_resolved_by_shrink")
                ),
                "n": len(timing_rows),
            }
            logger.info(
                "Task %s: timing_fit summary segs=%d overflow=%d trimmed=%d "
                "underfill=%d max_underfill_ms=%d",
                task_id,
                len(timing_rows),
                sum(1 for r in timing_rows if int(r.get("overflow_ms") or 0) > 0),
                sum(1 for r in timing_rows if r.get("speech_trimmed")),
                task_info["underfill_count"],
                task_info["max_underfill_ms"],
            )
    except Exception as _tlog_exc:
        logger.debug("timing_fit summary skipped: %s", _tlog_exc)
    # Hard trim cut speech but left full paragraph in Final — sync Review text.
    # Happy Path / Stage 4: never rewrite Final after TTS (Review == voiced text).
    try:
        _skip_trim_sync = bool(_happy_path_timing)
        if task_info is not None and task_info.get("final_tts_locked"):
            _skip_trim_sync = True
        if not _skip_trim_sync:
            from engines.tts_audio_text_sync import apply_audio_trim_text_sync

            audits = list((task_info or {}).get("translation_audits") or [])
            trim_synced = apply_audio_trim_text_sync(
                segments_data,
                list((overlap_report or {}).get("fitted_placements") or []),
                placed_seg_indices=placed_seg_indices,
                audits=audits,
            )
            if trim_synced and task_info is not None:
                task_info["translation_audits"] = audits
                task_info["audio_trim_text_synced"] = int(trim_synced)
                logger.info(
                    "Task %s: audio_trim_text_sync updated %s segment(s)",
                    task_id,
                    trim_synced,
                )
            if overlap_report is not None:
                overlap_report["audio_trim_text_synced"] = int(trim_synced)
                overlap_report["placed_seg_indices"] = list(placed_seg_indices)
        elif task_info is not None:
            task_info["audio_trim_text_synced"] = 0
            task_info["audio_trim_text_sync_skipped"] = "happy_path_final_tts_lock"
    except Exception as _trim_sync_exc:
        logger.warning(
            "Task %s: audio_trim_text_sync skipped: %s",
            task_id,
            _trim_sync_exc,
        )
    warnings = [
        line
        for line in fit_logs
        if ("overflow_ms=" in line and "overflow_ms=0" not in line)
        or ("atempo=" in line and "atempo=1.0" not in line and "atempo=1." in line)
    ]
    return timed_audio, warnings, overlap_report


def _persist_task_review(task: dict) -> str | None:
    """Write translation review JSON next to output MP4."""
    try:
        from engines.translation_quality import persist_translation_review
        from engines.translation_review import build_translation_review

        info = task.get("info") or {}
        output_name = task.get("output_file")
        if not output_name:
            return None
        output_path = info.get("output_path_full") or str(OUTPUT_DIR / output_name)
        if not Path(output_path).exists():
            return None
        review = build_translation_review(info)
        path = persist_translation_review(output_path, review)
        info["translation_review_path"] = path
        return path
    except Exception as e:
        logger.debug("persist translation review skipped: %s", e)
        return None


def _remux_done_output(task_id: str, timed_audio_path: str) -> tuple[bool, str | None, list]:
    """Remux final MP4 after post-done segment edit."""
    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        if not task or task.get("status") != "done":
            return True, None, []
        info = task.get("info") or {}

    if info.get("skip_tts"):
        return True, None, []

    video_path = info.get("video_path_backup")
    output_path = info.get("output_path_full")
    if not output_path and task.get("output_file"):
        output_path = str(OUTPUT_DIR / task["output_file"])

    if not video_path or not Path(video_path).exists():
        return False, None, ["Исходное видео недоступно для пересборки"]
    if not timed_audio_path or not Path(timed_audio_path).exists():
        return False, None, ["Timed audio missing"]
    if not output_path:
        return False, None, ["Output path unknown"]

    target_duration = info.get("target_duration_ms")
    dub_timeout_sec = max(600, int((target_duration or 0) / 1000) + 300)

    from engines.dub_engine import DubEngine
    from engines.source_separation import get_background_mix_params

    # Mirror the main mux: keep music/SFX stem + isolated voice ducking on re-export
    # so a post-edit remux never regresses to a flatter mix (parity with §1A path).
    _sep_info = dict(info.get("source_separation") or {})
    _bg_path, _bg_atten_db, _sep_ok = get_background_mix_params(
        {"source_separation": _sep_info}
    )
    _dialogue_path = ""
    if _sep_ok and _sep_info.get("success"):
        _dlg = _sep_info.get("dialogue_path")
        if _dlg and Path(str(_dlg)).is_file():
            _dialogue_path = str(_dlg)
    _mix_config = None
    try:
        from engines.audio_mix_config import resolve_mix_config
        from engines.dubbing_engine.content_mode import get_profile

        _cm = info.get("content_mode") or ""
        _mix_config = resolve_mix_config(
            original_volume=(info.get("mix_volumes_backup") or {}).get("original_volume"),
            dub_volume=(info.get("mix_volumes_backup") or {}).get("dub_volume"),
            background_volume=(info.get("mix_volumes_backup") or {}).get("background_volume"),
            content_mode_profile=get_profile(_cm) if _cm else None,
            request=(info.get("mix_volumes_backup") or {}),
        )
    except Exception:  # noqa: BLE001 — mixer policy must not break remux
        _mix_config = None

    logger.info("Task %s: remux after regen -> %s", task_id, output_path)
    raw_result = DubEngine(
        video_path=video_path,
        timed_audio=timed_audio_path,
        background_audio_path=_bg_path or "",
        background_attenuation_db=_bg_atten_db,
        dialogue_audio_path=_dialogue_path,
        mix_config=_mix_config,
    ).run(
        output_path=output_path,
        mode=info.get("dub_mode_backup") or "replace",
        mix_mode=info.get("mix_mode_backup") or "full_dub",
        mix_volume=float(info.get("mix_volume_backup") or 0.3),
        original_volume=(info.get("mix_volumes_backup") or {}).get("original_volume"),
        dub_volume=(info.get("mix_volumes_backup") or {}).get("dub_volume"),
        background_volume=(info.get("mix_volumes_backup") or {}).get("background_volume"),
        timeout_sec=dub_timeout_sec,
    )

    if isinstance(raw_result, tuple) and len(raw_result) >= 3:
        ok, out_path, dub_errors = raw_result[0], raw_result[1], raw_result[2]
    elif isinstance(raw_result, tuple) and len(raw_result) == 2:
        ok, out_path, dub_errors = raw_result[0], raw_result[1], []
    else:
        return False, None, ["DubEngine contract broken"]

    if not ok:
        errs = dub_errors if isinstance(dub_errors, list) else [str(dub_errors)]
        return False, None, errs

    final_output = out_path or output_path
    if not final_output or not Path(final_output).exists():
        return False, None, ["Remux output missing"]

    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        if task:
            task["output_file"] = Path(final_output).name
            task["info"]["output_path_full"] = str(final_output)

    return True, str(final_output), []


def _dev_preview_enabled(info: dict | None) -> bool:
    if not info:
        return False
    if info.get("developer_preview_enabled"):
        return True
    return os.getenv("VM_DEV_MODE", "").strip().lower() in ("1", "true", "yes", "on") or os.getenv(
        "VM_ARCHITECT_MODE", ""
    ).strip().lower() in ("1", "true", "yes", "on")


def _build_dev_preview_sync(task_id: str) -> None:
    from engines.developer_preview import (
        MIN_PREVIEW_SEGMENTS,
        contiguous_ready_prefix,
        detect_first_pipeline_error,
    )

    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        if not task:
            return
        info = task.setdefault("info", {})
        if not _dev_preview_enabled(info):
            return
        segments_data = list(info.get("segments_data") or [])
        timing_map = list(info.get("timing_map_backup") or info.get("timing_map") or [])
        video_path = str(info.get("video_path_backup") or info.get("video_path") or "")
        prefix = contiguous_ready_prefix(segments_data)
        if prefix < MIN_PREVIEW_SEGMENTS - 1 or not video_path or not timing_map:
            return
        gen = int(info.get("developer_preview_generation") or 0) + 1
        style_params = dict(info.get("dub_style_params") or {})

    partial_segs = segments_data[: prefix + 1]
    partial_timing = timing_map[: prefix + 1]
    _, end_ms = _parse_timing(partial_timing[-1])
    target_duration_ms = max(500, int(end_ms) + 300)

    timed_audio_obj, _warnings, _overlap = _build_timed_dub_track(
        partial_segs,
        partial_timing,
        target_duration_ms,
        task_id,
        style_params=style_params,
    )
    if timed_audio_obj is None:
        return

    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        info = dict((task or {}).get("info") or {})
    artifacts = _artifacts_dir(info)
    preview_audio = str(artifacts / f"{task_id}_dev_preview_{gen}.mp3")
    if not _safe_export_audio(timed_audio_obj, preview_audio):
        return

    preview_name = f"{task_id}_dev_preview_{gen}.mp4"
    preview_path = str(OUTPUT_DIR / preview_name)
    dub_timeout = max(120, int(target_duration_ms / 1000) + 60)
    from engines.dub_engine import DubEngine

    ok, out_path, _errs = DubEngine(
        video_path=video_path,
        timed_audio=preview_audio,
    ).run(
        output_path=preview_path,
        mix_mode=info.get("mix_mode_backup") or "full_dub",
        timeout_sec=dub_timeout,
    )
    if not ok or not out_path or not Path(out_path).is_file():
        return

    first_error = detect_first_pipeline_error(info)
    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        if not task:
            return
        info = task.setdefault("info", {})
        info["developer_preview"] = {
            "file": preview_name,
            "url": f"/output/{preview_name}",
            "segments_ready": prefix + 1,
            "generation": gen,
            "updated_at": time.time(),
            "duration_ms": end_ms,
            "first_error": first_error,
        }
        info["developer_preview_generation"] = gen
    logger.info(
        "Task %s: dev preview gen=%d segments=%d -> %s",
        task_id,
        gen,
        prefix + 1,
        preview_name,
    )


def _schedule_dev_preview(task_id: str) -> None:
    from engines.developer_preview import schedule_preview_build

    schedule_preview_build(task_id, lambda: _build_dev_preview_sync(task_id))


def _normalize_tts_result(raw_files):
    """Приведение типов результатов generate_audio к безопасному списку строк."""
    normalized = []
    if raw_files is None:
        return normalized
    if isinstance(raw_files, str):
        if raw_files.strip():
            normalized.append(raw_files.strip())
    elif isinstance(raw_files, (list, tuple)):
        for item in raw_files:
            if item and isinstance(item, str) and item.strip():
                normalized.append(item.strip())
            elif item and not isinstance(item, str):
                normalized.append(str(item).strip())
    return normalized


def _normalize_progress(pct):
    """Приводит прогресс от DubEngine (0..1 или 0..100) строго к диапазону 0..100."""
    try:
        val = float(pct)
        if val <= 1.0 and val > 0.0:
            return val * 100.0
        if val < 0.0:
            return 0.0
        if val > 100.0:
            return 100.0
        return val
    except (ValueError, TypeError):
        return 0.0


# ─────────────────────────────────────────────
#  API Маршруты Управления Пайплайном
# ─────────────────────────────────────────────


@bp.post("/api/auto_dub/start")
def api_auto_dub_start():
    """Инициализация и запуск асинхронного фонового пайплайна дубляжа."""
    from engines.license_manager import require_feature

    allowed, lic_msg = require_feature("auto_dub")
    if not allowed:
        return jsonify({"error": lic_msg}), 403

    lp = _get_lp(request)

    from engines.ffmpeg_paths import find_ffmpeg

    if not find_ffmpeg():
        return jsonify({"error": lp["ffmpeg_missing"]}), 500

    data = request.get_json(silent=True) or {}
    logger.debug("auto_dub start payload keys: %s", list(data.keys()))

    # UI Simple/Pro/Dev mode — Simple always forces Happy Path (TZ Stage 1).
    try:
        from engines.feature_flags.modes import normalize_mode
        from engines.happy_path import stamp_happy_path_meta

        _raw_mode = (
            data.get("user_mode")
            or data.get("vm_user_mode")
            or request.cookies.get("vm_user_mode")
            or "basic"
        )
        _user_mode = normalize_mode(str(_raw_mode))
    except Exception:
        _user_mode = "basic"
        stamp_happy_path_meta = None  # type: ignore[assignment]

    raw_path = (data.get("video_path") or "").strip()

    if not raw_path:
        return jsonify({"error": "Видео не выбрано"}), 400

    if "_OUTPUT_" in Path(raw_path).name.upper():
        return jsonify({"error": lp.get("output_file_blocked", lp["not_found"])}), 400

    from engines.path_safety import resolve_under_roots

    vp = resolve_under_roots(
        raw_path,
        [
            APP_DIR / "uploads",
            APP_DIR / "uploads" / "imports",
            OUTPUT_DIR if OUTPUT_DIR.is_absolute() else APP_DIR / "output",
        ],
        basename_fallback=True,
    )
    if not vp:
        return jsonify({"error": f"Файл не найден: {raw_path}"}), 400

    video_path = str(vp.resolve())

    if "_OUTPUT_" in Path(video_path).name.upper():
        return jsonify({"error": lp.get("output_file_blocked", lp["not_found"])}), 400

    allowed_models = ["tiny", "base", "small", "medium", "large", "large-v3"]
    model_size = data.get("model_size", "tiny")
    if model_size not in allowed_models:
        model_size = "tiny"
    # Spec v3 opt-in: high quality → large-v3 + word_timestamps; Simple stays capped.
    _stt_quality_req = str(
        data.get("stt_quality") or ("high" if data.get("spec_v3") else "")
    ).strip().lower()
    try:
        from engines.happy_path import is_simple_mode
        from engines.simple_stt_policy import (
            resolve_simple_stt_model,
            resolve_stt_model_for_quality,
        )

        if _stt_quality_req in ("standard", "high"):
            model_size = resolve_stt_model_for_quality(
                _stt_quality_req, requested=model_size
            )
        elif is_simple_mode({"user_mode": _user_mode}):
            model_size = resolve_simple_stt_model(model_size)
    except Exception:
        if _stt_quality_req == "high":
            model_size = "large-v3"
        elif _stt_quality_req == "standard" and model_size in ("tiny", "base", "small"):
            model_size = "medium"
        elif _user_mode in ("basic", "simple"):
            if model_size in ("medium", "large", "large-v3"):
                model_size = "small"

    ui_lang = _resolve_ui_lang(data.get("ui_lang"))

    from engines.dub_style_presets import (
        DEFAULT_DUB_STYLE,
        gate_style_for_user_mode,
        normalize_style_id,
        resolve_dub_style,
    )

    def _vol_pct(key: str) -> float | None:
        if key not in data:
            return None
        try:
            v = float(data[key])
            return v / 100.0 if v > 1.0 else v
        except (TypeError, ValueError):
            return None

    _raw_style_req = (
        (data.get("dub_style") or data.get("voice_style") or "").strip()
    )
    style_id = normalize_style_id(_raw_style_req)
    if not _raw_style_req:
        legacy_mix = (data.get("mix_mode") or data.get("dub_mode") or "").strip().lower()
        if legacy_mix:
            _raw_style_req = legacy_mix
            style_id = normalize_style_id(legacy_mix)

    # TZ Stage 6: language_learning / underlay mix → Pro only; Simple stays full_dub.
    style_id, _style_gated = gate_style_for_user_mode(
        style_id, _user_mode, raw_request=_raw_style_req or style_id
    )
    if _style_gated:
        logger.info(
            "Happy Path: underlay style gated to %s (user_mode=%s raw=%s)",
            style_id,
            _user_mode,
            _raw_style_req,
        )

    orig_override = _vol_pct("original_volume")
    if orig_override is None:
        orig_override = _vol_pct("original_volume_pct")
    if _style_gated:
        # Keep documentary-like underlay instead of silencing original (TZ §24–26).
        orig_override = 0.20

    resolved_style = resolve_dub_style(style_id, original_volume=orig_override)
    mix_mode = resolved_style["mix_mode"]
    mix_volumes = resolved_style["mix_volumes"]
    try:
        from engines.simple_dub_pipeline import apply_simple_uk_source_underlay

        _mix_info = {
            "user_mode": _user_mode,
            "target_lang": data.get("target_lang") or data.get("lang") or "",
            "simple_pipeline": str(_user_mode or "").lower() in ("basic", "simple", "")
            or bool(data.get("simple_pipeline")),
            "happy_path": True,
        }
        mix_volumes = apply_simple_uk_source_underlay(
            _mix_info,
            mix_volumes,
            explicit_original=orig_override if not _style_gated else None,
            raw_style=_raw_style_req,
            style_gated=_style_gated,
        )
        mix_mode = mix_volumes.get("mix_mode") or mix_mode
    except Exception:
        pass
    # Optional professional-mix overrides (TZ Task 9): intelligent-ducking
    # intensity / fade / per-track volumes. Carried on mix_volumes so the single
    # resolver (engines/audio_mix_config.resolve_mix_config) picks them up at mux.
    try:
        mix_volumes = dict(mix_volumes or {})
        for _k in ("ducking_db", "fade_ms", "background_volume", "dub_volume"):
            if data.get(_k) is not None:
                mix_volumes[_k] = float(data[_k])
        if data.get("ducking_enabled") is not None:
            mix_volumes["ducking_enabled"] = bool(data["ducking_enabled"])
    except (TypeError, ValueError):
        pass
    skip_tts = bool(resolved_style.get("skip_tts"))
    tts_rate = resolved_style.get("tts_rate")
    tts_pitch = resolved_style.get("tts_pitch")
    style_id = resolved_style["style_id"]
    review_before_tts = bool(data.get("translation_review_before_tts", True))

    keep_original_track = bool(data.get("keep_original_track", False))
    # RASM (Sync QC) needs retained reference audio for dual playback
    try:
        from engines.rasm.config import is_rasm_enabled

        if is_rasm_enabled():
            keep_original_track = True
    except Exception:
        pass

    # TZ §2: per-job adaptation mode (automatic | strict). Absent → resolved later
    # from the registered feature flag / env override.
    from engines.llm_adaptation_mode import normalize_mode as _norm_adapt_mode

    strict_llm_adaptation = (
        _norm_adapt_mode(data.get("strict_llm_adaptation"))
        if data.get("strict_llm_adaptation") not in (None, "")
        else None
    )

    # ТЗ §3: adaptation speed/quality budget. Independent per-segment budget,
    # never a single project-wide timer that skips later segments.
    from engines.translation_adapt import normalize_speed_mode as _norm_speed_mode

    adaptation_speed_mode = (
        _norm_speed_mode(
            data.get("adaptation_speed_mode") or data.get("dub_speed_mode")
        )
        if (data.get("adaptation_speed_mode") or data.get("dub_speed_mode"))
        not in (None, "")
        else None
    )
    if adaptation_speed_mode is None:
        try:
            from engines.llm_adaptation_mode import resolve_default_adaptation_speed_mode

            adaptation_speed_mode = resolve_default_adaptation_speed_mode()
        except Exception:
            adaptation_speed_mode = _norm_speed_mode(None)

    def _opt_float(val):
        try:
            f = float(val)
            return f if f > 0 else None
        except (TypeError, ValueError):
            return None

    adaptation_segment_budget_s = _opt_float(
        data.get("adaptation_segment_budget_s")
        or data.get("segment_budget_s")
    )
    adaptation_project_budget_s = _opt_float(
        data.get("adaptation_project_budget_s")
        or data.get("project_budget_s")
    )

    # Content Mode — determines dubbing behaviour (Movie, Blogger, Podcast…)
    content_mode = (data.get("content_mode") or "movie").strip().lower()
    try:
        from engines.dubbing_engine.content_mode import ContentMode
        content_mode = ContentMode.from_str(content_mode).value
    except Exception:
        content_mode = "movie"

    skip_translate = bool(data.get("skip_translate"))
    if skip_translate and not data.get("translated_segments"):
        skip_translate = False

    src_for_prepare = (data.get("source_lang") or "en").split("-")[0].lower()
    tgt_for_prepare = (data.get("target_lang") or "ru").split("-")[0].lower()
    ocr_flag = bool(data.get("ocr_enabled", False))
    if not skip_translate:
        from engines.model_manager import is_profile_ready

        if not is_profile_ready(
            APP_DIR,
            src_for_prepare,
            tgt_for_prepare,
            whisper_size=model_size,
            ocr_enabled=ocr_flag,
            feature="dub",
        ):
            return jsonify(
                {
                    "error": "Сначала завершите подготовку компонентов для выбранных языков",
                    "error_code": "prepare_required",
                }
            ), 409

    task_id = uuid.uuid4().hex

    preload = {}
    if data.get("source_segments"):
        preload["source_segments"] = data["source_segments"]
    if skip_translate and data.get("translated_segments"):
        preload["translated_segments"] = data["translated_segments"]
    if data.get("timing_map"):
        preload["timing_map"] = data["timing_map"]

    task_payload = {
        "status": "running",
        "step": "preparing",
        "progress": 0.0,
        "ui_lang": ui_lang,
        "steps_done": 0,
        "errors": [],
        "output_file": None,
        "info": {
            "segments_data": [],
            "source_segments": [],
            "timing_map_backup": [],
            "timed_audio": None,
            "skip_translate": skip_translate,
            "preload": preload,
            "dub_style": style_id,
            "skip_tts": skip_tts,
            "translation_review_before_tts": review_before_tts,
            "tts_rate": tts_rate,
            "tts_pitch": tts_pitch,
            "tts_engine": (
                data.get("tts_engine") or data.get("tts_backend") or "edge-offline"
            ),
            "content_mode": content_mode,
            "strict_llm_adaptation": strict_llm_adaptation,
            "adaptation_speed_mode": adaptation_speed_mode,
            "adaptation_segment_budget_s": adaptation_segment_budget_s,
            "adaptation_project_budget_s": adaptation_project_budget_s,
            "adaptive_segmentation_settings": data.get("adaptive_segmentation")
            if isinstance(data.get("adaptive_segmentation"), dict)
            else None,
            "segmentation_mode": (data.get("segmentation_mode") or "adaptive")
            .strip()
            .lower(),
            "user_mode": _user_mode,
            "mix_mode": mix_mode,
            "underlay_style_gated": bool(_style_gated),
            "stt_quality": _stt_quality_req or "simple",
            "spec_v3": bool(data.get("spec_v3") or _stt_quality_req == "high"),
        },
    }
    try:
        from engines.happy_path import stamp_happy_path_meta as _stamp_hp
        from engines.simple_dub_pipeline import apply_simple_pipeline_policy as _simple_pol

        _stamp_hp(task_payload["info"], user_mode=_user_mode)
        if _user_mode in ("basic", "simple"):
            _simple_pol(task_payload["info"], user_mode=_user_mode)
    except Exception:
        task_payload["info"]["happy_path"] = True
        task_payload["info"]["adaptation_path"] = "happy_path"
        task_payload["info"]["USE_ADVANCED_ADAPTATION"] = False
        task_payload["info"]["simple_pipeline"] = True
        task_payload["info"]["simple_auto_mix"] = True
    _store_style_profile(task_payload["info"], resolved_style)
    try:
        from engines.tts_backends import (
            normalize_backend_name,
            resolve_mykyta_controls,
            resolve_voice_for_backend,
            set_pipeline_mykyta_controls,
            set_pipeline_tts_backend,
        )

        _eid = normalize_backend_name(task_payload["info"].get("tts_engine"))
        _tgt_start = str(
            data.get("target_lang")
            or data.get("lang")
            or task_payload["info"].get("target_lang")
            or "uk"
        ).split("-")[0].lower()
        # Stage 24: UK jobs always start on tts_uk + mykyta (no cs/sk/pl/ru).
        if _tgt_start == "uk":
            try:
                from engines.tts_lang_lock import force_uk_tts_identity

                _ident0 = force_uk_tts_identity(
                    target_lang="uk",
                    engine_id=_eid or "tts_uk",
                    voice=str(
                        data.get("voice") or task_payload["info"].get("voice") or ""
                    ),
                )
                _eid = normalize_backend_name(_ident0.get("engine_id") or "tts_uk")
                task_payload["info"]["voice"] = str(_ident0.get("voice") or "mykyta")
                task_payload["info"]["tts_voice"] = task_payload["info"]["voice"]
                task_payload["info"]["tts_language"] = "uk"
                task_payload["info"]["target_lang"] = "uk"
            except Exception:
                _eid = "tts_uk"
                task_payload["info"]["voice"] = "mykyta"
                task_payload["info"]["tts_language"] = "uk"
        task_payload["info"]["tts_engine"] = _eid
        task_payload["info"]["tts_backend"] = (
            "tts_uk"
            if _eid == "tts_uk"
            else ("piper" if _eid == "piper" else "edge")
        )
        set_pipeline_tts_backend(_eid)
        _voice0 = str(data.get("voice") or task_payload["info"].get("voice") or "")
        if _voice0:
            task_payload["info"]["voice"] = resolve_voice_for_backend(_voice0, _eid)
            if _tgt_start == "uk":
                try:
                    from engines.tts_lang_lock import force_uk_tts_identity

                    _ident1 = force_uk_tts_identity(
                        target_lang="uk", engine_id=_eid, voice=task_payload["info"]["voice"]
                    )
                    task_payload["info"]["voice"] = str(_ident1.get("voice") or "mykyta")
                    task_payload["info"]["tts_voice"] = task_payload["info"]["voice"]
                    task_payload["info"]["tts_language"] = "uk"
                except Exception:
                    pass
        # Stage 22 — Mykyta / tts_uk voice controls
        _mk_raw = {
            "rate": data.get("mykyta_rate", data.get("tts_uk_rate", data.get("tts_rate"))),
            "pitch": data.get(
                "mykyta_pitch", data.get("tts_uk_pitch", data.get("tts_pitch"))
            ),
            "volume": data.get(
                "mykyta_volume", data.get("tts_uk_volume", data.get("tts_volume"))
            ),
            "length_scale": data.get(
                "mykyta_length_scale",
                data.get("tts_uk_length_scale", data.get("tts_length_scale")),
            ),
        }
        _mk = resolve_mykyta_controls(_mk_raw)
        task_payload["info"]["mykyta_rate"] = _mk["rate"]
        task_payload["info"]["mykyta_pitch"] = _mk["pitch"]
        task_payload["info"]["mykyta_volume"] = _mk["volume"]
        task_payload["info"]["mykyta_length_scale"] = _mk["length_scale"]
        task_payload["info"]["tts_volume"] = _mk["volume"]
        task_payload["info"]["tts_length_scale"] = _mk["length_scale"]
        if _eid == "tts_uk":
            # Prefer numeric Mykyta rate/pitch over Edge-style strings when set.
            if data.get("mykyta_rate") is not None or data.get("tts_uk_rate") is not None:
                task_payload["info"]["tts_rate"] = str(_mk["rate"])
            if data.get("mykyta_pitch") is not None or data.get("tts_uk_pitch") is not None:
                task_payload["info"]["tts_pitch"] = str(_mk["pitch"])
            set_pipeline_mykyta_controls(_mk)
        else:
            set_pipeline_mykyta_controls(None)
    except Exception:
        pass
    init_auto_task(task_id, task_payload)
    with STATE_LOCK:
        AUTO_TASK_CONTROLS[task_id] = {
            "state": "running",
            "editing": False,
            "editor_error": False,
            "current_segment": 0,
            "stop_after_segment": bool(data.get("stop_after_segment")),
            "awaiting_translation_review": False,
        }

    import threading

    from engines.dub_task_state import register_pipeline_thread

    _pipe_kwargs = {
        "task_id": task_id,
        "video_path": str(video_path),
        "target_lang": _normalize_pipeline_target_lang(
            data.get("target_lang") or data.get("lang")
        ),
        "voice": data.get("voice")
        or _default_edge_voice(
            _normalize_pipeline_target_lang(
                data.get("target_lang") or data.get("lang")
            )
        ),
        "model_size": model_size,
        "mix_mode": mix_mode,
        "mix_volumes": mix_volumes,
        "keep_original_track": keep_original_track,
        "dub_mode": data.get("dub_mode", "replace"),
        "mix_volume": float(data.get("mix_volume", 0.3)),
        "source_lang": data.get("source_lang"),
        "target_duration_ms": data.get("target_duration_ms"),
        "skip_translate": skip_translate,
        "ui_lang": ui_lang,
        "segmentation_mode": (data.get("segmentation_mode") or "adaptive").strip().lower(),
        "ocr_enabled": bool(data.get("ocr_enabled", False)),
        "dub_style": style_id,
        "skip_tts": skip_tts,
        "tts_rate": tts_rate,
        "tts_pitch": tts_pitch,
        "content_mode": content_mode,
    }
    _pipeline_target = _run_pipeline
    if _user_mode in ("basic", "simple"):
        # Explicit Simple entrypoint (TZ reference pipeline).
        from engines.simple_dub_pipeline import run_simple_dub_pipeline as _run_simple

        _pipeline_target = _run_simple
        _pipe_kwargs["segmentation_mode"] = "happy_path"
        # run_simple_dub_pipeline does not take ocr_enabled.
        _pipe_kwargs.pop("ocr_enabled", None)
        _pipe_kwargs.pop("segmentation_mode", None)

    t = threading.Thread(
        target=_pipeline_target,
        kwargs=_pipe_kwargs,
        daemon=True,
    )
    register_pipeline_thread(task_id, t)
    try:
        from engines.pipeline_watchdog import start_pipeline_watchdog

        start_pipeline_watchdog(task_id, app_dir=APP_DIR)
    except Exception:
        pass
    t.start()
    return jsonify({"task_id": task_id, "status": "running"})


@bp.get("/api/auto_dub/styles")
def api_auto_dub_styles():
    """Style Packs: base + regional styles for target dubbing language."""
    try:
        from engines.dub_style_presets import (
            DEFAULT_DUB_STYLE,
            ORIGINAL_VOLUME_PRESETS,
            list_dub_styles,
            list_style_pack_meta,
        )

        target_lang = (request.args.get("target_lang") or "").strip()
        local_only = request.args.get("local_only", "1").strip().lower() not in (
            "0",
            "false",
            "no",
            "off",
        )

        meta = list_style_pack_meta(target_lang or None, local_only=local_only)
        return jsonify(
            {
                "styles": list_dub_styles(
                    target_lang=target_lang or None,
                    local_only=local_only,
                ),
                "default": DEFAULT_DUB_STYLE,
                "original_volume_presets": [int(p * 100) for p in ORIGINAL_VOLUME_PRESETS],
                **meta,
            }
        )
    except Exception as exc:
        logger.exception("api_auto_dub_styles failed")
        return jsonify({"error": str(exc), "styles": []}), 500


@bp.post("/api/auto_dub/preview_style")
def api_preview_style():
    """Короткая тестовая фраза выбранным стилем озвучки."""
    data = request.get_json(silent=True) or {}
    ui_lang = _resolve_ui_lang(data.get("ui_lang"))
    from engines.dub_style_presets import (
        get_preview_phrase,
        normalize_style_id,
        resolve_dub_style,
    )

    style_id = normalize_style_id(data.get("dub_style"))
    resolved = resolve_dub_style(style_id)
    if resolved.get("skip_tts"):
        return jsonify({"error": "Стиль без озвучки"}), 400

    voice = (
        data.get("voice")
        or _default_edge_voice(data.get("target_lang") or data.get("lang"))
    ).strip()
    target_lang = (data.get("target_lang") or ui_lang or "ru").strip()
    phrase = get_preview_phrase(style_id, target_lang)
    if not phrase:
        return jsonify({"error": "Нет фразы для предпрослушивания"}), 400

    try:
        from engines.tts import generate_audio
        from engines.tts_backends import normalize_backend_name, resolve_voice_for_backend
        from engines.voice_style_fx import apply_voice_style_fx

        eid = normalize_backend_name(
            data.get("tts_engine") or data.get("engine_id") or "edge-offline"
        )
        voice = resolve_voice_for_backend(voice, eid)
        raw = generate_audio(
            text=phrase,
            voice=voice,
            segments=[phrase],
            rate=resolved.get("tts_rate"),
            pitch=resolved.get("tts_pitch"),
            engine_id=eid,
        )
        files = _normalize_tts_result(raw)
        if not files:
            return jsonify({"error": "TTS failed"}), 500
        out_path = OUTPUT_DIR / files[0]
        apply_voice_style_fx(out_path, resolved.get("voice_fx"), inplace=True)
        return jsonify(
            {
                "ok": True,
                "file": files[0],
                "phrase": phrase,
                "style_id": style_id,
            }
        )
    except Exception as e:
        logger.exception("preview_style failed")
        return jsonify({"error": str(e)}), 500


def _resolve_diagnostic_zip(task_id: str) -> tuple[dict | None, str | None]:
    from engines.pipeline_integrity.passive_openddf import ensure_diagnostic_archive

    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        if not task:
            logger.warning(
                "[OpenDDF-Download] run_id=%s reason=task_not_found",
                task_id,
            )
            return None, None
        info = dict(task.get("info") or {})
    zip_path = ensure_diagnostic_archive(task_id, task_info=info)
    if not zip_path:
        logger.warning(
            "[OpenDDF-Download] run_id=%s reason=archive_not_created",
            task_id,
        )
        return info, None
    if not Path(zip_path).is_file():
        logger.warning(
            "[OpenDDF-Download] run_id=%s path=%s reason=path_not_found",
            task_id,
            zip_path,
        )
        return info, None
    return info, zip_path


@bp.get("/api/auto_dub/diagnostics/<task_id>/zip")
def api_auto_dub_diagnostic_zip(task_id):
    """Download OpenDDF passive diagnostic ZIP (creates archive if missing)."""
    from flask import send_file

    _info, zip_path = _resolve_diagnostic_zip(task_id)
    if _info is None:
        return jsonify({
            "error": "Задача не найдена",
            "diagnostic_zip_status": "failed",
            "diagnostic_zip_reason_code": "task_not_found",
            "diagnostic_zip_reason": "задача не найдена",
        }), 404
    if not zip_path:
        return jsonify({
            "error": "Diagnostic archive not available",
            "diagnostic_zip_status": "failed",
            "diagnostic_zip_reason_code": "archive_not_created",
            "diagnostic_zip_reason": "архив не создан",
        }), 404
    return send_file(
        zip_path,
        as_attachment=True,
        download_name=Path(zip_path).name,
    )


@bp.route("/api/auto_dub/diagnostics/<task_id>/zip", methods=["HEAD"])
def api_auto_dub_diagnostic_zip_head(task_id):
    """Ensure diagnostic ZIP exists (TZ §5 — «Сообщить об ошибке»)."""
    _info, zip_path = _resolve_diagnostic_zip(task_id)
    if _info is None:
        return "", 404
    if not zip_path:
        return "", 404
    return "", 200


@bp.post("/api/auto_dub/diagnostics/<task_id>/save")
def api_auto_dub_diagnostic_save(task_id):
    """Save As dialog for Diagnostic ZIP — user picks folder and filename."""
    import shutil as _shutil

    _info, zip_path = _resolve_diagnostic_zip(task_id)
    if _info is None:
        return jsonify({
            "error": "Задача не найдена",
            "diagnostic_zip_status": "failed",
            "diagnostic_zip_reason_code": "task_not_found",
            "diagnostic_zip_reason": "задача не найдена",
        }), 404
    if not zip_path:
        return jsonify({
            "error": "Diagnostic archive not available",
            "diagnostic_zip_status": "failed",
            "diagnostic_zip_reason_code": "archive_not_created",
            "diagnostic_zip_reason": "архив не создан",
        }), 404

    default_name = f"diagnostic_{task_id}.zip"
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        dest = filedialog.asksaveasfilename(
            title="Сохранить диагностику",
            defaultextension=".zip",
            initialfile=default_name,
            filetypes=[("ZIP archive", "*.zip"), ("All files", "*.*")],
            parent=root,
        )
        root.destroy()
    except Exception as exc:
        logger.warning(
            "[OpenDDF-Download] save dialog failed run_id=%s reason=api_error detail=%s",
            task_id,
            exc,
        )
        return jsonify({
            "error": f"Диалог сохранения недоступен: {exc}",
            "diagnostic_zip_status": "failed",
            "diagnostic_zip_reason_code": "api_error",
            "diagnostic_zip_reason": "ошибка API",
        }), 500

    if not dest:
        return jsonify({"cancelled": True})

    dest_path = Path(dest)
    if dest_path.suffix.lower() != ".zip":
        dest_path = dest_path.with_suffix(".zip")

    try:
        _shutil.copy2(str(zip_path), str(dest_path))
    except PermissionError:
        logger.warning(
            "[OpenDDF-Download] save failed run_id=%s path=%s reason=permission_denied",
            task_id,
            dest_path,
        )
        return jsonify({
            "error": "Недостаточно прав для записи в выбранную папку",
            "diagnostic_zip_status": "failed",
            "diagnostic_zip_reason_code": "permission_denied",
            "diagnostic_zip_reason": "недостаточно прав доступа",
        }), 500
    except OSError as exc:
        logger.warning(
            "[OpenDDF-Download] save failed run_id=%s path=%s reason=write_error detail=%s",
            task_id,
            dest_path,
            exc,
        )
        return jsonify({
            "error": f"Ошибка копирования: {exc}",
            "diagnostic_zip_status": "failed",
            "diagnostic_zip_reason_code": "write_error",
            "diagnostic_zip_reason": "ошибка записи",
        }), 500

    logger.info(
        "[OpenDDF-Download] diagnostic zip saved run_id=%s path=%s",
        task_id,
        dest_path,
    )
    return jsonify({
        "success": True,
        "path": str(dest_path.resolve()),
        "filename": dest_path.name,
    })


@bp.get("/api/ai_core/report/<task_id>")
def api_ai_core_report(task_id: str):
    """AI Core aggregated report (OpenDDF + LLM telemetry)."""
    from engines.ai_core.report import build_ai_core_report_for_task, build_and_save_ai_core_report

    safe = Path(task_id).name
    if not safe or safe != task_id:
        return jsonify({"ok": False, "error": "invalid task_id"}), 400

    with STATE_LOCK:
        task = AUTO_TASKS.get(safe)
        info = dict((task or {}).get("info") or {})

    report = build_ai_core_report_for_task(safe, task_info=info)
    if report.get("final_status") == "no_data":
        path = OUTPUT_DIR / f"ai_core_report_{safe}.json"
        if path.is_file():
            try:
                import json

                with open(path, encoding="utf-8") as fh:
                    report = json.load(fh)
            except Exception:
                return jsonify({"ok": False, "error": "report not found", "task_id": safe}), 404
        else:
            build_and_save_ai_core_report(safe, task_info=info)
            report = build_ai_core_report_for_task(safe, task_info=info)

    return jsonify({"ok": True, "task_id": safe, "report": report})


@bp.get("/api/auto_dub/openddf_report/<task_id>")
def api_auto_dub_openddf_report(task_id):
    """Full OpenDDF diagnostic report JSON for UI viewer."""
    from engines.segment_timing_qa import build_openddf_full_report

    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        if not task:
            return jsonify({"ok": False, "error": "Задача не найдена"}), 404
        info = dict(task.get("info") or {})
        info.setdefault("task_id", task_id)

    report = info.get("openddf_full_report") or build_openddf_full_report(info)
    return jsonify({"ok": True, "report": report, "task_id": task_id})


@bp.post("/api/auto_dub/openddf_report/<task_id>/save")
def api_auto_dub_openddf_report_save(task_id):
    """Save As dialog for OpenDDF JSON — user picks folder and filename."""
    import json as _json

    from engines.segment_timing_qa import build_openddf_full_report

    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        if not task:
            return jsonify({"error": "Задача не найдена"}), 404
        info = dict(task.get("info") or {})
        info.setdefault("task_id", task_id)

    report = info.get("openddf_full_report") or build_openddf_full_report(info)
    default_name = f"openddf_report_{task_id}.json"

    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        dest = filedialog.asksaveasfilename(
            title="Сохранить OpenDDF",
            defaultextension=".json",
            initialfile=default_name,
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
            parent=root,
        )
        root.destroy()
    except Exception as exc:
        logger.warning(
            "[OpenDDF-Save] dialog failed task=%s: %s",
            task_id,
            exc,
        )
        return jsonify({"error": f"Диалог сохранения недоступен: {exc}"}), 500

    if not dest:
        return jsonify({"cancelled": True})

    dest_path = Path(dest)
    if dest_path.suffix.lower() != ".json":
        dest_path = dest_path.with_suffix(".json")

    try:
        dest_path.write_text(
            _json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        return jsonify({"error": f"Ошибка записи: {exc}"}), 500

    logger.info("[OpenDDF-Save] task=%s path=%s", task_id, dest_path)
    return jsonify({
        "success": True,
        "path": str(dest_path.resolve()),
        "filename": dest_path.name,
    })


@bp.get("/api/auto_dub/debug_mode")
def api_auto_dub_debug_mode():
    """Return whether Debug/Learning mode is active."""
    from engines.core.feature_flags import IS_DEBUG_LEARNING_MODE

    return jsonify({"enabled": IS_DEBUG_LEARNING_MODE()})


def _lite_diagnostic_status_from_info(info: dict | None) -> dict:
    """ZIP status from in-memory task info only (no disk scan)."""
    from engines.pipeline_integrity.passive_openddf import diagnostic_zip_status_fields

    info = info or {}
    passive = info.get("passive_openddf") or {}
    arts = info.get("openddf_artifacts") or {}
    zip_path = passive.get("diagnostic_zip") or arts.get("diagnostic_zip")
    stored_status = passive.get("diagnostic_zip_status")
    stored_reason = passive.get("diagnostic_zip_reason_code")
    if stored_status == "created" and zip_path:
        return {
            "diagnostic_zip": zip_path,
            "diagnostic_zip_available": True,
            "diagnostic_zip_status": "created",
            "diagnostic_zip_reason_code": None,
            "diagnostic_zip_reason": None,
        }
    if stored_status == "failed" and stored_reason:
        return diagnostic_zip_status_fields(zip_path, reason_code=stored_reason)
    if stored_status == "creating":
        return diagnostic_zip_status_fields(zip_path, pending=True)
    if zip_path:
        return diagnostic_zip_status_fields(zip_path)
    return diagnostic_zip_status_fields(None, reason_code="path_not_found")


@bp.get("/api/auto_dub/status/<task_id>")
def api_auto_dub_status(task_id):
    """Потокобезопасная отдача текущего состояния атомарной задачи.

    Hold STATE_LOCK only for a shallow snapshot. Heavy diagnostics / disk I/O
    run outside the lock so the dubbing pipeline is not starved and status
    polls (every 1s) stay responsive — otherwise the UI shows «Нет связи».
    """
    lite = request.args.get("lite", "").strip().lower() in ("1", "true", "yes", "on")
    from engines.module_registry.registry import is_developer_session

    dev_mode = is_developer_session(
        request_headers=dict(request.headers),
        request_cookies=dict(request.cookies),
    )
    architect_mode = dev_mode or os.getenv("VM_ARCHITECT_MODE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    build_preview = not lite and architect_mode

    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        control = AUTO_TASK_CONTROLS.get(task_id)
        if not task:
            return jsonify({"status": "error", "error": "Задача не найдена"}), 404
        task["_last_touch"] = time.time()

        ui_lang = task.get("ui_lang") or _resolve_ui_lang(request.args.get("lang"))
        step = task["step"]
        status = task["status"]
        if status == "done":
            step = "done"
        elif status == "studio_ready":
            step = "studio"

        info = task.get("info") or {}
        detected = info.get("detected_lang")
        res = {
            "status": status,
            "step": task["step"],
            "step_label": _step_label(step, ui_lang),
            "progress": task["progress"],
            "steps_done": task["steps_done"],
            "errors": list(task.get("errors") or []),
            "output_file": task["output_file"],
            "subtitle_file": info.get("subtitle_file"),
            "dub_style": info.get("dub_style"),
            "detected_lang": detected,
            "detected_lang_name": LANG_CODE_TO_NAME.get(detected, detected) if detected else None,
            "source_lang": info.get("source_lang"),
            "studio_url": info.get("studio_url"),
            "ddf_url": info.get("ddf_url"),
            "dev_diagnostics_available": dev_mode and bool(info.get("translation_diagnostics")),
            "dev_inspector_available": dev_mode or architect_mode,
            "progress_detail": dict(info.get("progress_detail") or {}),
            "translation_timing": info.get("translation_timing_breakdown")
            or info.get("translation_timing"),
            "simple_pipeline": bool(info.get("simple_pipeline")),
            "happy_path": bool(info.get("happy_path")),
            "simple_mt_locked": bool(info.get("simple_mt_locked")),
            "simple_voice_locked": bool(info.get("simple_voice_locked")),
            "tts_voice": info.get("tts_voice") or info.get("pipeline_voice") or info.get("voice"),
            "unique_voices_used": info.get("unique_voices_used"),
            "llm_adaptation_used": bool(info.get("llm_adaptation_used")),
            "translate_method": info.get("translate_method"),
            "translation_agent_path": bool(info.get("translation_agent_path")),
            "mt_wall_sec": info.get("mt_wall_sec"),
            "mt_engine": info.get("mt_engine"),
            "mt_cache_hits": info.get("mt_cache_hits"),
            "mt_cache_misses": info.get("mt_cache_misses"),
            "tts_failures": list(info.get("tts_failures") or []),
            "last_tts_error": info.get("last_tts_error"),
            "pipeline_error": info.get("pipeline_error"),
            "runtime_diagnostics": list(info.get("runtime_diagnostics") or []),
            "openddf_run_id": info.get("openddf_run_id"),
            "checkpoint": info.get("pipeline_checkpoint"),
            "stall_info": info.get("pipeline_stall"),
            "pipeline_performance": info.get("pipeline_performance"),
            "pipeline_conveyor_timing": info.get("pipeline_conveyor_timing"),
            "full_conveyor": info.get("full_conveyor"),
            "tps": bool(info.get("tps")),
            "tps_manual_indices": list(info.get("tps_manual_indices") or []),
        }
        info_snapshot = dict(info)
        control_snap = dict(control) if control else None
        if build_preview:
            preview_src = list(info.get("source_segments", []))
            preview_segs = list(info.get("segments_data", []))
            preview_audits = list(info.get("translation_audits") or [])
        else:
            preview_src = preview_segs = preview_audits = None
        progress_for_preview = float(task.get("progress") or 0)
        step_for_preview = task.get("step") or ""

    # Outside STATE_LOCK — keep polling cheap and never block the pipeline.
    res["watchdog"] = _watchdog_status_snapshot(task_id)
    from engines.core.feature_flags import IS_DEBUG_LEARNING_MODE

    res["debug_learning_mode"] = IS_DEBUG_LEARNING_MODE()

    if lite:
        res["ai_core"] = None
        res.update(_lite_diagnostic_status_from_info(info_snapshot))
    else:
        try:
            from engines.ai_core.unified_diagnostics import build_unified_diagnostics

            ai_diag = build_unified_diagnostics(
                task_id, app_dir=APP_DIR, task_info=info_snapshot
            )
            res["ai_core"] = {
                "active_model": ai_diag.get("active_model"),
                "pipeline_route_display": ai_diag.get("pipeline_route_display"),
                "quality_score": ai_diag.get("quality_score"),
                "status": ai_diag.get("status"),
                "global_skill_version": ai_diag.get("global_skill_version"),
            }
        except Exception:
            res["ai_core"] = None
        from engines.pipeline_integrity.passive_openddf import diagnostic_status_for_task

        res.update(diagnostic_status_for_task(task_id, info_snapshot))

    if dev_mode:
        res["last_tts_diagnostic"] = info_snapshot.get("last_tts_diagnostic")
        res["last_pipeline_diagnostic"] = info_snapshot.get("last_pipeline_diagnostic")
        res["pipeline_error_developer"] = info_snapshot.get("pipeline_error_developer")
        res["openddf_artifacts"] = info_snapshot.get("openddf_artifacts")
        res["passive_openddf"] = info_snapshot.get("passive_openddf")
        res["openddf_run_id"] = info_snapshot.get("openddf_run_id")
    if control_snap:
        res.update(
            {
                "state": control_snap["state"],
                "editing": control_snap["editing"],
                "editor_error": control_snap.get("editor_error", False),
                "awaiting_translation_review": control_snap.get(
                    "awaiting_translation_review", False
                ),
                "current_segment": control_snap["current_segment"] + 1,
            }
        )

    segments_preview: list[dict] = []
    if build_preview and preview_segs is not None:
        audits_by_idx = {
            int(a.get("index", -1)): a for a in (preview_audits or [])
        }
        for i, seg in enumerate(preview_segs[:50]):
            audit = audits_by_idx.get(i, {})
            preview = {
                "original": preview_src[i] if i < len(preview_src) else "",
                "translated": seg.get("text", ""),
            }
            if audit:
                qd = audit.get("quality_details") or {}
                preview.update(
                    {
                        "whisper": audit.get("whisper_text") or preview["original"],
                        "raw_mt": audit.get("raw_translation") or "",
                        "alternative": audit.get("alternative_translation") or "",
                        "naturalized": audit.get("naturalized_text") or "",
                        "final": audit.get("final_text") or preview["translated"],
                        "tts": audit.get("tts_text") or preview["translated"],
                        "engine": audit.get("engine") or "",
                        "route": audit.get("route_label") or audit.get("route") or "",
                        "router_reason": audit.get("router_reason") or "",
                        "quality_score": audit.get("quality_score"),
                        "mt_ms": audit.get("duration_ms"),
                        "alternative_route": audit.get("alternative_route") or "",
                        "alternative_engine": audit.get("alternative_engine") or "",
                        "routes_tried": audit.get("routes_tried") or [],
                        "pipeline_health": qd.get("pipeline_health") or {},
                        "warnings": audit.get("validation_warnings") or [],
                        "naturalizer_reasons": audit.get("naturalizer_reasons") or [],
                        "enterprise": audit.get("enterprise") or False,
                        "tournament_engines": audit.get("tournament_engines") or [],
                        "tournament_scores": audit.get("tournament_scores") or {},
                        "fusion_reason": audit.get("fusion_reason") or "",
                    }
                )
                if audit.get("architect"):
                    preview["architect"] = audit.get("architect")
            segments_preview.append(preview)

    res["segments_preview"] = segments_preview
    if dev_mode:
        from engines.developer_preview import build_status_payload

        res["developer_preview"] = build_status_payload(
            info_snapshot, step=step_for_preview, progress=progress_for_preview
        )
    return jsonify(res)


@bp.get("/api/auto_dub/sso_report/<task_id>")
def api_auto_dub_sso_report(task_id):
    """Smart Segment Optimizer V2 dev report (Developer Mode)."""
    import json

    dev_mode = os.getenv("VM_DEV_MODE", "").strip().lower() in ("1", "true", "yes", "on")
    if not dev_mode:
        return jsonify({"error": "Developer mode required"}), 403

    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        if not task:
            return jsonify({"error": "Задача не найдена"}), 404
        meta = dict(task.get("info", {}).get("smart_segment_optimizer") or {})
        report_path = meta.get("dev_report_path") or task.get("info", {}).get("sso_dev_report")

    if report_path and Path(report_path).is_file():
        try:
            data = json.loads(Path(report_path).read_text(encoding="utf-8"))
            produb_path = (
                (task.get("info") or {}).get("professional_dubbing") or {}
            ).get("dev_report_path")
            if produb_path and Path(produb_path).is_file():
                produb = json.loads(Path(produb_path).read_text(encoding="utf-8"))
                data["prosody_groups"] = produb.get("groups") or []
                data["prosody_meta"] = produb.get("meta") or {}
            return jsonify({"ok": True, "task_id": task_id, **data})
        except Exception as e:
            return jsonify({"error": str(e), "path": report_path}), 500

    return jsonify({"ok": True, "task_id": task_id, "meta": meta, "segments": []})


@bp.get("/api/auto_dub/translation_review/<task_id>")
def api_translation_review(task_id):
    """Полный путь текста для панели контроля перевода."""
    from engines.translation_review import build_translation_review

    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        if not task:
            return jsonify({"error": "Задача не найдена"}), 404
        info = copy.deepcopy(task.get("info") or {})
        status = task.get("status")
        output_name = task.get("output_file")
        info["target_lang"] = info.get("target_lang") or request.args.get("target_lang")

    cached = info.get("translation_review")
    if cached and cached.get("segments") and status not in ("done", "studio_ready"):
        review = cached
    else:
        review = build_translation_review(info)

    output_name = output_name or info.get("output_file")
    if output_name and not review.get("segments"):
        from engines.translation_quality import load_translation_review

        loaded = load_translation_review(OUTPUT_DIR / output_name)
        if loaded and loaded.get("segments"):
            review = loaded

    return jsonify(
        {
            "ok": True,
            "task_id": task_id,
            "status": status,
            "awaiting_translation_review": status == "translation_review",
            **review,
        }
    )


@bp.post("/api/auto_dub/translation_review/<task_id>/apply")
def api_translation_review_apply(task_id):
    """Explicit reopen→edit→relock. Archives old revision; marks NeedReTTS."""
    data = request.get_json(silent=True) or {}
    new_text = str(data.get("new_text") or data.get("text") or "").strip()
    if not new_text:
        return jsonify({"error": "new_text is required"}), 400

    sid = str(data.get("segment_id") or "").strip()
    user_idx = None
    if data.get("segment_index") is not None:
        try:
            user_idx = int(data.get("segment_index", 1))
        except (TypeError, ValueError):
            return jsonify({"error": "invalid segment_index"}), 400

    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        if not task:
            return jsonify({"error": "Задача не найдена"}), 404
        info = task.setdefault("info", {})
        segments_data = info.get("segments_data") or []

    edit = {"text": new_text}
    if sid:
        edit["segment_id"] = sid
    if user_idx is not None:
        edit["index"] = user_idx

    texts = _apply_translation_text_edits(info, edits=[edit])

    # Locate the edited row by UUID first (index is display only).
    from engines.pipeline_integrity.identity_guard import resolve_row_by_identity

    seg, how = resolve_row_by_identity(
        info.get("segments_data") or [],
        segment_id=sid,
        index=(user_idx - 1) if user_idx else None,
    )
    seg_idx = 0
    if isinstance(seg, dict):
        try:
            seg_idx = (info.get("segments_data") or []).index(seg)
        except ValueError:
            seg_idx = (user_idx - 1) if user_idx else 0

    with STATE_LOCK:
        status = AUTO_TASKS.get(task_id, {}).get("status")

    return jsonify(
        {
            "ok": True,
            "segment_index": (user_idx if user_idx is not None else seg_idx + 1),
            "segment_id": str((seg or {}).get("segment_id") or sid or ""),
            "lookup": how,
            "status": status,
            "segment_count": len(texts),
            "needs_retts": bool((seg or {}).get("needs_retts")),
            "adaptation_uuid": (seg or {}).get("adaptation_uuid"),
            "tts_uuid": (seg or {}).get("tts_uuid"),
            "dsal": (seg or {}).get("dsal"),
            "dsal_band": (seg or {}).get("dsal_band"),
            "needs_studio": bool((seg or {}).get("needs_studio")),
        }
    )


@bp.post("/api/auto_dub/translation_review/<task_id>/relock")
def api_translation_review_relock(task_id):
    """P4: re-evaluate LOCK gate after Studio editorial edits."""
    from engines.dsal.studio_editorial import relock_after_editorial
    from engines.translation_review import build_translation_review

    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        if not task:
            return jsonify({"error": "Задача не найдена"}), 404
        info = task.setdefault("info", {})

    meta = relock_after_editorial(info)
    review = build_translation_review(info)
    info["translation_review"] = review
    return jsonify(
        {
            "ok": True,
            "task_id": task_id,
            "lock": meta,
            "needs_studio": bool(info.get("needs_studio")),
            "translation_lock_deferred": bool(info.get("translation_lock_deferred")),
            "segment_count": review.get("segment_count"),
        }
    )


@bp.post("/api/auto_dub/translation_review/<task_id>/approve")
def api_translation_review_approve(task_id):
    """Apply optional bulk edits and resume pipeline to TTS."""
    data = request.get_json(silent=True) or {}
    edits = data.get("edits")

    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        control = AUTO_TASK_CONTROLS.get(task_id)
        if not task or not control:
            return jsonify({"error": "Задача не найдена"}), 404
        if task.get("status") != "translation_review":
            return jsonify({"error": "Task is not awaiting translation review"}), 400
        info = task.setdefault("info", {})

        # Apply Original Voice % so mix uses review/settings value (not start-only freeze).
        if "original_volume" in data or "original_volume_pct" in data:
            raw = data.get("original_volume", data.get("original_volume_pct"))
            try:
                v = float(raw)
                pct = v / 100.0 if v > 1.0 else v
                pct = max(0.0, min(1.0, pct))
            except (TypeError, ValueError):
                pct = None
            if pct is not None:
                from engines.dub_style_presets import resolve_dub_style

                style_id = (
                    info.get("dub_style")
                    or info.get("style_id")
                    or "modern"
                )
                resolved = resolve_dub_style(style_id, original_volume=pct)
                info["mix_volumes"] = dict(resolved["mix_volumes"])
                info["mix_volumes_backup"] = dict(resolved["mix_volumes"])
                info["mix_mode"] = resolved["mix_mode"]
                info["mix_mode_backup"] = resolved["mix_mode"]
                info["original_volume_pct"] = int(round(pct * 100))

    if edits:
        _apply_translation_text_edits(info, edits=edits)

    try:
        from engines.dsal.studio_editorial import relock_after_editorial

        relock_after_editorial(info)
    except Exception:
        pass

    texts = _resume_from_translation_review(task_id)
    if texts is None:
        return jsonify({"error": "Задача не найдена"}), 404

    return jsonify(
        {
            "ok": True,
            "task_id": task_id,
            "state": "running",
            "segment_count": len(texts),
        }
    )


@bp.get("/api/auto_dub/translation_review/<task_id>/export")
def api_translation_review_export(task_id):
    """Экспорт review в plain text."""
    from engines.translation_review import build_translation_review, export_review_text

    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        if not task:
            return jsonify({"error": "Задача не найдена"}), 404
        info = copy.deepcopy(task.get("info") or {})

    review = build_translation_review(info)
    body = export_review_text(review)
    return jsonify({"ok": True, "text": body, "filename": f"tubedub_review_{task_id[:8]}.txt"})


def _dev_diagnostics_allowed() -> bool:
    from engines.translation_diagnostics import dev_diagnostics_enabled

    return dev_diagnostics_enabled()


@bp.get("/api/auto_dub/translation_diagnostics/<task_id>")
def api_translation_diagnostics(task_id):
    """Developer-only full pipeline diagnostics."""
    if not _dev_diagnostics_allowed():
        return jsonify({"error": "Developer mode required"}), 403

    from engines.translation_diagnostics import build_developer_diagnostics

    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        if not task:
            return jsonify({"error": "Задача не найдена"}), 404
        info = copy.deepcopy(task.get("info") or {})
        info["task_id"] = task_id

    diag = info.get("translation_diagnostics") or build_developer_diagnostics(info)
    return jsonify({"ok": True, "task_id": task_id, "diagnostics": diag})


@bp.get("/api/auto_dub/translation_diagnostics/<task_id>/export")
def api_translation_diagnostics_export(task_id):
    """Export diagnostics as translation_diagnostics.txt."""
    if not _dev_diagnostics_allowed():
        return jsonify({"error": "Developer mode required"}), 403

    from engines.translation_diagnostics import (
        build_developer_diagnostics,
        export_diagnostics_text,
    )

    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        if not task:
            return jsonify({"error": "Задача не найдена"}), 404
        info = copy.deepcopy(task.get("info") or {})
        info["task_id"] = task_id

    diag = info.get("translation_diagnostics") or build_developer_diagnostics(info)
    body = export_diagnostics_text(diag)
    return jsonify(
        {
            "ok": True,
            "text": body,
            "filename": "translation_diagnostics.txt",
        }
    )


def _dev_inspector_allowed() -> bool:
    from engines.translation_inspector import inspector_enabled

    return inspector_enabled()


@bp.get("/api/auto_dub/translation_inspector/<task_id>")
def api_translation_inspector(task_id):
    """Developer-only per-stage translation inspector."""
    if not _dev_inspector_allowed():
        return jsonify({"error": "Developer mode required (VM_DEV_MODE=1)"}), 403

    from engines.translation_inspector import build_translation_inspector

    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        if not task:
            return jsonify({"error": "Задача не найдена"}), 404
        info = copy.deepcopy(task.get("info") or {})
        info["task_id"] = task_id

    report = info.get("translation_inspector") or build_translation_inspector(info)
    return jsonify({"ok": True, "task_id": task_id, "inspector": report})


@bp.get("/api/auto_dub/translation_inspector/<task_id>/export")
def api_translation_inspector_export(task_id):
    """Export inspector report as TXT."""
    if not _dev_inspector_allowed():
        return jsonify({"error": "Developer mode required (VM_DEV_MODE=1)"}), 403

    from engines.translation_inspector import (
        build_translation_inspector,
        export_inspector_text,
    )

    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        if not task:
            return jsonify({"error": "Задача не найдена"}), 404
        info = copy.deepcopy(task.get("info") or {})
        info["task_id"] = task_id

    report = info.get("translation_inspector") or build_translation_inspector(info)
    body = export_inspector_text(report)
    return jsonify(
        {
            "ok": True,
            "text": body,
            "filename": f"translation_inspector_{task_id[:8]}.txt",
        }
    )


@bp.get("/api/auto_dub/translation_inspector/<task_id>/json")
def api_translation_inspector_json(task_id):
    """Export full inspector JSON."""
    if not _dev_inspector_allowed():
        return jsonify({"error": "Developer mode required (VM_DEV_MODE=1)"}), 403

    from engines.translation_inspector import (
        build_translation_inspector,
        export_inspector_json,
    )

    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        if not task:
            return jsonify({"error": "Задача не найдена"}), 404
        info = copy.deepcopy(task.get("info") or {})
        info["task_id"] = task_id

    report = info.get("translation_inspector") or build_translation_inspector(info)
    return jsonify(
        {
            "ok": True,
            "json": export_inspector_json(report),
            "filename": f"translation_inspector_{task_id[:8]}.json",
        }
    )


@bp.post("/api/auto_dub/cancel/<task_id>")
def api_auto_cancel(task_id):
    """Safely cancel autodub; preserves checkpoint for restart with new settings."""
    from engines.dub_task_state import cancel_pipeline_runtime, request_cancel

    request_cancel(task_id, reason="user")
    runtime = cancel_pipeline_runtime(task_id, join_timeout=5.0)

    with STATE_LOCK:
        control = AUTO_TASK_CONTROLS.get(task_id)
        task = AUTO_TASKS.get(task_id)
        if not control or not task:
            return jsonify({"error": "Задача не найдена"}), 404
        if task.get("status") in ("done", "cancelled"):
            return jsonify({"error": "Task already finished"}), 400

        from engines.ai_core.architecture_validation import pipeline_checkpoint

        info = task.setdefault("info", {})
        checkpoint = pipeline_checkpoint(info)
        info["pipeline_checkpoint"] = checkpoint
        info["cancelled_at"] = time.time()
        info["cancel_runtime"] = runtime
        control["state"] = "cancelled"
        task["status"] = "cancelled"

        try:
            from engines.ai_core.architecture_validation import merge_ux_event

            merge_ux_event(task_id, event="cancel", checkpoint=checkpoint, app_dir=APP_DIR)
        except Exception:
            pass

        return jsonify({
            "ok": True,
            "task_id": task_id,
            "state": "cancelled",
            "checkpoint": checkpoint,
            "runtime": runtime,
        })


@bp.post("/api/auto_dub/restart/<task_id>")
def api_auto_restart(task_id):
    """Restart from last checkpoint (voice/lang changes) without full pipeline redo."""
    data = request.get_json(silent=True) or {}
    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        if not task:
            return jsonify({"error": "Задача не найдена"}), 404

        from engines.ai_core.architecture_validation import pipeline_checkpoint

        info = task.setdefault("info", {})
        checkpoint = info.get("pipeline_checkpoint") or pipeline_checkpoint(info)

        from engines.developer_preview import resolve_restart_cache_plan

        cache_plan = resolve_restart_cache_plan(info, data, checkpoint=checkpoint)
        info["restart_cache_plan"] = cache_plan
        if cache_plan.get("skip_translate"):
            info["skip_translate"] = True
            info["pre_translated"] = list(info.get("source_segments") or [])
            translated = []
            for seg in info.get("segments_data") or []:
                translated.append(str(seg.get("text") or seg.get("plain_text") or ""))
            if translated:
                info["translated_segments"] = translated
        else:
            info.pop("skip_translate", None)

        if data.get("target_lang"):
            info["target_lang"] = str(data["target_lang"])
        if data.get("voice"):
            info["voice"] = str(data["voice"])
        if data.get("tts_rate") is not None:
            info["tts_rate"] = data["tts_rate"]
        if data.get("tts_pitch") is not None:
            info["tts_pitch"] = data["tts_pitch"]

        info["restart_from_checkpoint"] = checkpoint
        task["status"] = "running"
        control = AUTO_TASK_CONTROLS.get(task_id)
        if not control:
            AUTO_TASK_CONTROLS[task_id] = {
                "state": "running",
                "editing": False,
                "editor_error": False,
                "current_segment": 0,
                "stop_after_segment": False,
                "awaiting_translation_review": False,
            }
        else:
            control["state"] = "running"
            control["editor_error"] = False
            control["editing"] = False

        video_path = info.get("video_path") or task.get("video_path")
        target_lang = info.get("target_lang", "ru")
        voice = info.get("voice") or _default_edge_voice(
            info.get("target_lang") or info.get("lang") or data.get("target_lang")
        )

    try:
        from engines.ai_core.architecture_validation import merge_ux_event

        merge_ux_event(task_id, event="restart", checkpoint=checkpoint, app_dir=APP_DIR)
    except Exception:
        pass

    import threading

    from engines.dub_task_state import CANCEL_FLAGS, register_pipeline_thread

    ev = CANCEL_FLAGS.get(task_id)
    if ev:
        ev.clear()

    rt = threading.Thread(
        target=_run_pipeline_from_checkpoint,
        kwargs={
            "task_id": task_id,
            "video_path": str(video_path or ""),
            "target_lang": target_lang,
            "voice": voice,
            "checkpoint": checkpoint,
        },
        daemon=True,
    )
    register_pipeline_thread(task_id, rt)
    try:
        from engines.pipeline_watchdog import start_pipeline_watchdog

        start_pipeline_watchdog(task_id, app_dir=APP_DIR)
    except Exception:
        pass
    rt.start()

    return jsonify({"ok": True, "task_id": task_id, "checkpoint": checkpoint, "restarting": True, "cache_plan": cache_plan})


def _run_pipeline_from_checkpoint(
    task_id: str,
    video_path: str,
    target_lang: str,
    voice: str,
    checkpoint: str,
) -> None:
    """Resume autodub from checkpoint without redoing completed stages."""
    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        if not task:
            return
        info = dict(task.get("info") or {})

    if checkpoint == "post_ai_core_text":
        info["quality_agent_path"] = False
        info["reviewer_agent_path"] = False
    elif checkpoint == "post_translation":
        for key in (
            "semantic_agent_path", "timing_agent_path", "grammar_agent_path",
            "quality_agent_path", "reviewer_agent_path",
        ):
            info.pop(key, None)

    with STATE_LOCK:
        task["info"] = info

    try:
        _run_pipeline(
            task_id=task_id,
            video_path=video_path,
            target_lang=target_lang,
            voice=voice,
            model_size=info.get("model_size", "medium"),
            mix_mode=info.get("mix_mode", "replace"),
            mix_volumes=info.get("mix_volumes"),
            keep_original_track=bool(info.get("keep_original_track")),
            dub_mode=info.get("dub_mode", "replace"),
            mix_volume=float(info.get("mix_volume", 0.3)),
            source_lang=info.get("source_lang"),
            skip_translate=bool(info.get("skip_translate")),
            ui_lang=info.get("ui_lang", "ru"),
            skip_tts=checkpoint in ("post_voice", "post_mix"),
        )
    except Exception as exc:
        logger.exception("Restart from checkpoint failed task=%s: %s", task_id, exc)


@bp.post("/api/auto_dub/resume/<task_id>")
def api_auto_resume(task_id):
    """Потокобезопасный сброс флагов интерактива и возобновление пайплайна после паузы или ошибки."""
    with STATE_LOCK:
        control = AUTO_TASK_CONTROLS.get(task_id)
        task = AUTO_TASKS.get(task_id)
        if not control:
            return jsonify({"error": "Задача не найдена"}), 404

        if task and task.get("status") == "translation_review":
            return jsonify({"error": "Use translation_review approve endpoint"}), 400

        control["editor_error"] = False
        control["editing"] = False
        control["state"] = "running"
        control["stop_after_segment"] = False
        control["awaiting_translation_review"] = False

        return jsonify(
            {
                "task_id": task_id,
                "state": "running",
                "editing": False,
                "editor_error": False,
                "ok": True,
            }
        )


@bp.get("/api/auto_dub/current_segment/<task_id>")
def api_auto_current_segment(task_id):
    """Отдача текущей реплики для UI."""
    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        control = AUTO_TASK_CONTROLS.get(task_id)
        if not task or not control:
            return jsonify({"error": "Задача не найдена"}), 404

        curr_idx = control.get("current_segment")
        if curr_idx is None or curr_idx < 0:
            return jsonify(
                {"index": 1, "original": "", "translated": "", "generated": False}
            )

        segments_data = task.get("info", {}).get("segments_data", [])
        source_segments = task.get("info", {}).get("source_segments", [])

        if curr_idx >= len(segments_data):
            return jsonify(
                {"index": 1, "original": "", "translated": "", "generated": False}
            )

        filename = segments_data[curr_idx].get("file")
        is_generated = bool(filename and (OUTPUT_DIR / filename).exists())

        return jsonify(
            {
                "index": curr_idx + 1,
                "original": (
                    source_segments[curr_idx] if curr_idx < len(source_segments) else ""
                ),
                "translated": segments_data[curr_idx].get("text", ""),
                "generated": is_generated,
            }
        )


@bp.post("/api/auto_dub/regen_segment")
def api_regen_segment():
    """Потокобезопасная атомарная регенерация сегмента."""
    lp = _get_lp(request)
    data = request.get_json(silent=True) or {}
    task_id = data.get("task_id", "")

    if not task_id or str(task_id).strip() == "":
        return jsonify({"error": "task_id required"}), 400

    try:
        user_idx = int(data.get("segment_index", 1))
    except (TypeError, ValueError):
        return jsonify({"error": lp["invalid_idx"]}), 400

    seg_idx = user_idx - 1
    new_text = data.get("new_text", "")

    if not isinstance(new_text, str) or not new_text.strip():
        return jsonify({"error": "new_text is required"}), 400

    voice = data.get("voice") or _default_edge_voice(
        data.get("target_lang") or data.get("lang")
    )

    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        control = AUTO_TASK_CONTROLS.get(task_id)
        if not task or not control:
            return jsonify({"error": lp["not_found"]}), 404

        segments_data = task.get("info", {}).get("segments_data", [])
        if seg_idx < 0 or seg_idx >= len(segments_data):
            return jsonify({"error": lp["invalid_idx"]}), 400

    try:
        from engines.tts import generate_audio

        raw_files = generate_audio(
            text=new_text, voice=voice, segments=[new_text],
            rate=task.get("info", {}).get("tts_rate"),
            pitch=task.get("info", {}).get("tts_pitch"),
        )
        new_files = _normalize_tts_result(raw_files)

        if not new_files:
            return jsonify({"error": lp["tts_failed"]}), 500

        from engines.voice_style_fx import apply_voice_style_fx

        style_cfg = _style_params_from_info(task.get("info"))
        apply_voice_style_fx(OUTPUT_DIR / new_files[0], style_cfg.get("voice_fx"), inplace=True)

        with STATE_LOCK:
            old_file = segments_data[seg_idx].get("file")
            segments_data[seg_idx]["text"] = new_text
            segments_data[seg_idx]["file"] = new_files[0]
            _identity_bind_after_regen(
                segments_data[seg_idx],
                new_text,
                new_files[0],
                segments_data=segments_data,
                stage="ui_segment_regen",
            )

            if old_file and old_file != new_files[0]:
                (OUTPUT_DIR / old_file).unlink(missing_ok=True)

            task["info"]["segments_data"] = segments_data
            timing_map = task["info"].get("timing_map_backup", [])
            target_duration = task["info"].get("target_duration_ms")

        if not timing_map:
            return jsonify(
                {
                    "ok": True,
                    "segment_index": user_idx,
                    "state": control["state"],
                    "warning": "timing_map_backup missing",
                }
            )

        timed_audio_obj, _ = _build_timed_dub_track(
            segments_data,
            timing_map,
            target_duration,
            task_id,
            style_params=_style_params_from_info(task.get("info")),
        )
        if timed_audio_obj is None:
            return jsonify({"error": lp["export_error"]}), 500

        base_id = task["info"].get("mux_base_id") or task_id[:8]
        timed_audio_path = str(_artifacts_dir(task.get("info")) / f"{base_id}_timed.mp3")

        export_ok = _safe_export_audio(timed_audio_obj, timed_audio_path)
        if not export_ok or not Path(timed_audio_path).exists():
            return jsonify({"error": lp["export_error"]}), 500

        remux_ok = True
        remux_errors: list = []
        new_output: str | None = None
        with STATE_LOCK:
            is_done = task.get("status") == "done"

        if is_done:
            remux_ok, new_output, remux_errors = _remux_done_output(task_id, timed_audio_path)

        with STATE_LOCK:
            task = AUTO_TASKS.get(task_id)
            if not task:
                return jsonify({"error": lp["not_found"]}), 404
            info = task["info"]
            info["timed_audio"] = timed_audio_path
            audits = info.get("translation_audits") or []
            for row in audits:
                if int(row.get("index", -1)) == seg_idx:
                    row["tts_text"] = new_text
                    row["user_edited"] = True
            info["translation_audits"] = audits
            from engines.translation_review import build_translation_review

            info["translation_review"] = build_translation_review(info)
            if is_done and remux_ok:
                _persist_task_review(task)

        resp: dict = {"ok": True, "segment_index": user_idx, "state": control["state"]}
        if new_output:
            resp["output_file"] = Path(new_output).name
        if is_done and not remux_ok:
            resp["warning"] = "segment_saved_remux_failed"
            resp["remux_errors"] = remux_errors[:5]
        return jsonify(resp)

    except Exception as e:
        return jsonify({"error": f"Сбой регенерации сегмента: {str(e)}"}), 500


@bp.get("/api/auto_dub/preview_segment/<task_id>/<segment_index>")
def api_auto_preview_segment(task_id, segment_index):
    """Превью-доступ к сгенерированному файлу сегмента."""
    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        if not task:
            return jsonify({"exists": False, "audio": None}), 404
        try:
            seg_idx = int(segment_index) - 1
            if seg_idx < 0:
                return jsonify({"exists": False, "audio": None})

            segments_data = task.get("info", {}).get("segments_data", [])
            if seg_idx >= len(segments_data):
                return jsonify({"exists": False, "audio": None})

            filename = segments_data[seg_idx].get("file")
            if filename and (OUTPUT_DIR / filename).exists():
                return jsonify({"exists": True, "audio": Path(filename).name})
        except Exception as e:
            logger.exception("preview_segment error task=%s seg=%s: %s", task_id, segment_index, e)
    return jsonify({"exists": False, "audio": None})


# ─────────────────────────────────────────────
#  Фоновый Асинхронный Пайплайн Обработки
# ─────────────────────────────────────────────


def _wait_for_control(task_id: str) -> bool:
    """
    Автомат состояний контроля выполнения пайплайна.
    Держит поток в режиме ожидания при паузах и корректно выходит при аварийном останове.
    """
    from engines.dub_task_state import is_cancel_requested

    while True:
        if is_cancel_requested(task_id):
            return False
        with STATE_LOCK:
            control = AUTO_TASK_CONTROLS.get(task_id)
            if not control:
                return False

            state = control.get("state", "running")
            editing = control.get("editing", False)
            editor_error = control.get("editor_error", False)

            if state == "cancelled":
                return False

            if state == "error" and not editing and not editor_error:
                return False

            if state == "paused" or editing or editor_error:
                if control.get("awaiting_translation_review") and state == "paused":
                    logger.debug(
                        "Task %s: waiting in _wait_for_control for translation review approve",
                        task_id,
                    )
            elif state == "running" and not editing:
                return True

        time.sleep(0.4)


def _ensure_control(task_id: str, ui_lang: str = "ru") -> bool:
    """
    Проверяет, можно ли продолжать пайплайн.
    При неожиданной остановке помечает задачу ошибкой (не оставляет «вечный» 65%).
    """
    from engines.dub_task_state import is_cancel_requested

    if _wait_for_control(task_id):
        return True

    lp = LOCALIZATION.get(ui_lang, LOCALIZATION["ru"])
    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        control = AUTO_TASK_CONTROLS.get(task_id)
        if not task:
            return False
        if task.get("status") in ("done", "error", "stalled", "cancelled"):
            return False
        if control and control.get("state") in ("cancelled", "stalled"):
            return False
        if is_cancel_requested(task_id):
            cancel_reason = str((control or {}).get("cancel_reason") or "")
            if cancel_reason in ("stalled", "cancel", "user"):
                return False
        if control and control.get("state") == "cancelled":
            return False
        if control and (
            control.get("editing")
            or control.get("state") == "paused"
            or control.get("editor_error")
        ):
            return False
        if task.get("status") == "running":
            from engines.dubbing_engine.pipeline_failure_diag import (
                STEP_TO_STAGE,
                STAGE_AUDIO_EXTRACTION,
            )

            step = str(task.get("step") or "")
            detail = (task.get("info") or {}).get("progress_detail") or {}
            phase = str(detail.get("phase") or "")
            stage_key = phase or step
            if is_cancel_requested(task_id):
                return False
            resolved_stage = STEP_TO_STAGE.get(stage_key, STAGE_AUDIO_EXTRACTION)
            _fail(
                task_id,
                [lp["pipeline_aborted"]],
                stage=resolved_stage,
                error_code="PIPELINE_ABORTED",
            )
    return False


def _build_agent_segments(source_segments: list, timing_map: list) -> list[dict]:
    """Build Translation Agent segment rows from STT output."""
    segments: list[dict] = []
    for i, raw in enumerate(source_segments):
        text = str(raw or "").strip()
        row: dict = {"index": i, "text": text}
        if i < len(timing_map):
            slot = timing_map[i] or {}
            row["start"] = slot.get("start", slot.get("start_ms"))
            row["end"] = slot.get("end", slot.get("end_ms"))
            if slot.get("speaker") is not None:
                row["speaker"] = slot.get("speaker")
        segments.append(row)
    return segments


def _run_director_agent_path(
    task_id: str,
    manifest_path: str,
    source_segments: list,
    timing_map: list,
    *,
    source_lang: str = "en",
) -> list[dict]:
    """Director Agent v1.0 — creative briefs before translation (READ ONLY)."""
    from engines.ai_core.director_agent import DirectorAgent
    from engines.ai_core.translation_agent.agent import load_manifest

    manifest = load_manifest(manifest_path)
    state = {
        "source_lang": source_lang,
        "segments": _build_agent_segments(source_segments, timing_map),
    }
    agent = DirectorAgent()
    result = agent.run(manifest, state, task_id)

    segments = list(result.updated_state.get("segments") or [])
    briefs = list(result.updated_state.get("creative_briefs") or [])

    with STATE_LOCK:
        if task_id in AUTO_TASKS:
            info = AUTO_TASKS[task_id].setdefault("info", {})
            info["director_agent_path"] = True
            info["director_agent_status"] = result.status
            info["director_report_path"] = result.updated_state.get("director_report_path")
            info["creative_briefs"] = briefs
            if segments:
                info["segments_data"] = _segments_data_from_agent(segments, info)

    return segments


def _run_translation_agent_path(
    task_id: str,
    manifest_path: str,
    source_segments: list,
    timing_map: list,
    translate_meta: list | None = None,
) -> list[str]:
    """Translation Agent v1.0 — raw MT only, no naturalizer/shortening."""
    from engines.ai_core.translation_agent.agent import TranslationAgent, load_manifest

    manifest = load_manifest(manifest_path)
    director_segments = _run_director_agent_path(
        task_id,
        manifest_path,
        source_segments,
        timing_map,
        source_lang=str(manifest.get("source_lang") or "en"),
    )
    state = {
        "segments": director_segments
        or _build_agent_segments(source_segments, timing_map),
        "director_agent_status": "success",
    }
    agent = TranslationAgent()
    result = agent.run(manifest, state, task_id)

    segments_data = result.updated_state.get("segments") or []
    translated = [
        str(seg.get("translated_text") or seg.get("text") or "").strip()
        for seg in segments_data
    ]

    meta = {
        "pipeline": "translation_agent_v1",
        "naturalizer_executed": False,
        "naturalizer_applied": False,
        "timing_aware_executed": False,
        "timing_aware_applied": False,
        "translation_agent": True,
        "semantic_agent": False,
        "translator_used": result.metrics.get("translator_used"),
        "avg_confidence": result.metrics.get("avg_overall"),
    }
    if translate_meta is not None:
        translate_meta.append(meta)

    with STATE_LOCK:
        if task_id in AUTO_TASKS:
            info = AUTO_TASKS[task_id].setdefault("info", {})
            info["translation_agent_path"] = True
            info["translation_report_path"] = result.updated_state.get(
                "translation_report_path"
            )
            info["segments_data"] = _segments_data_from_agent(segments_data, info)
            info["translation_agent_status"] = result.status
            info["translation_agent_metrics"] = result.metrics
            _ensure_agent_translation_audits(
                info,
                source_segments=list(info.get("source_segments") or []),
                translated=translated,
                engine=result.metrics.get("translator_used"),
                src_lang=str(manifest.get("source_lang") or info.get("source_lang") or "en"),
                tgt_lang=str(manifest.get("target_lang") or info.get("target_lang") or "uk"),
            )
            from engines.translation_validation import sync_final_text_to_task_info

            sync_final_text_to_task_info(info)

    return translated


def _run_semantic_agent_path(
    task_id: str,
    manifest_path: str,
    segments_data: list,
    *,
    translation_status: str | None = None,
) -> list[str]:
    """Semantic Agent v1.0 — natural rewrite; sets semantic_text for TTS."""
    from engines.ai_core.semantic_agent.agent import SemanticAgent, load_manifest

    manifest = load_manifest(manifest_path)

    with STATE_LOCK:
        source_segments = list(
            (AUTO_TASKS.get(task_id, {}).get("info") or {}).get("source_segments") or []
        )

    agent_segments = []
    for i, seg in enumerate(segments_data):
        source_text = ""
        if i < len(source_segments):
            source_text = str(source_segments[i] or "").strip()
        agent_segments.append(
            {
                "index": int(seg.get("index", i)),
                "text": source_text,
                "translated_text": str(
                    seg.get("translated_text")
                    or seg.get("translation_text")
                    or seg.get("text")
                    or ""
                ).strip(),
                "start": seg.get("start"),
                "end": seg.get("end"),
                "speaker": seg.get("speaker"),
            }
        )

    state = {
        "segments": agent_segments,
        "translation_agent_status": translation_status or "success",
    }
    agent = SemanticAgent()
    result = agent.run(manifest, state, task_id)

    out_segments = result.updated_state.get("segments") or []
    semantic_texts: list[str] = []

    with STATE_LOCK:
        if task_id not in AUTO_TASKS:
            return [
                str(s.get("translated_text") or s.get("text") or "").strip()
                for s in segments_data
            ]
        info = AUTO_TASKS[task_id].setdefault("info", {})
        sd = info.get("segments_data") or segments_data
        for i, seg in enumerate(out_segments):
            semantic = str(seg.get("semantic_text") or "").strip()
            if not semantic:
                semantic = str(seg.get("translated_text") or "").strip()
            semantic_texts.append(semantic)
            if i < len(sd):
                sd[i]["semantic_text"] = semantic
                sd[i]["semantic_engine_text"] = semantic
                sd[i]["text_for_tts"] = semantic
                sd[i]["text"] = semantic
                sd[i]["plain_text"] = semantic
                sd[i]["final_text"] = semantic
                sd[i]["voice_input"] = semantic
                sd[i]["translation_text"] = semantic
                sd[i]["translated_text"] = sd[i].get("translated_text") or semantic
                sd[i]["grammar_text"] = semantic
                sd[i]["timing_text"] = semantic
        info["segments_data"] = sd
        info["semantic_agent_path"] = True
        info["semantic_report_path"] = result.updated_state.get("semantic_report_path")
        info["semantic_quality_report_path"] = result.updated_state.get("semantic_quality_report_path")
        info["semantic_quality_summary"] = result.updated_state.get("semantic_quality_summary")
        info["semantic_agent_status"] = result.status
        info["semantic_agent_metrics"] = result.metrics
        info["semantic_naturalizer_shorten_disabled"] = True

    return semantic_texts


def _run_timing_agent_path(
    task_id: str,
    manifest_path: str,
    segments_data: list,
    *,
    semantic_status: str | None = None,
) -> list[str]:
    """Timing Agent v1.0 — slot-fit text; sets timing_text for TTS."""
    from engines.ai_core.timing_agent.agent import TimingAgent, load_manifest

    manifest = load_manifest(manifest_path)

    with STATE_LOCK:
        source_segments = list(
            (AUTO_TASKS.get(task_id, {}).get("info") or {}).get("source_segments") or []
        )

    agent_segments = []
    for i, seg in enumerate(segments_data):
        source_text = ""
        if i < len(source_segments):
            source_text = str(source_segments[i] or "").strip()
        semantic = str(
            seg.get("semantic_text")
            or seg.get("translated_text")
            or seg.get("text")
            or ""
        ).strip()
        agent_segments.append(
            {
                "index": int(seg.get("index", i)),
                "text": source_text,
                "translated_text": str(
                    seg.get("translated_text") or seg.get("translation_text") or ""
                ).strip(),
                "semantic_text": semantic,
                "start": seg.get("start"),
                "end": seg.get("end"),
                "speaker": seg.get("speaker"),
            }
        )

    state = {
        "segments": agent_segments,
        "semantic_agent_status": semantic_status or "success",
    }
    agent = TimingAgent()
    result = agent.run(manifest, state, task_id)

    out_segments = result.updated_state.get("segments") or []
    timing_texts: list[str] = []

    with STATE_LOCK:
        if task_id not in AUTO_TASKS:
            return [
                str(s.get("timing_text") or s.get("semantic_text") or s.get("text") or "").strip()
                for s in segments_data
            ]
        info = AUTO_TASKS[task_id].setdefault("info", {})
        sd = info.get("segments_data") or segments_data
        for i, seg in enumerate(out_segments):
            timing = str(seg.get("timing_text") or "").strip()
            if not timing:
                timing = str(seg.get("semantic_text") or "").strip()
            timing_texts.append(timing)
            if i < len(sd):
                sd[i]["timing_text"] = timing
                sd[i]["text_for_tts"] = timing
                sd[i]["text"] = timing
                sd[i]["plain_text"] = timing
        info["segments_data"] = sd
        info["timing_agent_path"] = True
        info["timing_report_path"] = result.updated_state.get("timing_report_path")
        info["timing_agent_status"] = result.status
        info["timing_agent_metrics"] = result.metrics
        info["timing_naturalizer_shorten_disabled"] = True

    return timing_texts


def _run_grammar_agent_path(
    task_id: str,
    manifest_path: str,
    segments_data: list,
    *,
    timing_status: str | None = None,
) -> list[str]:
    """Grammar Agent v1.0 — grammar polish; sets grammar_text for TTS."""
    from engines.ai_core.grammar_agent.agent import GrammarAgent, load_manifest

    manifest = load_manifest(manifest_path)

    with STATE_LOCK:
        source_segments = list(
            (AUTO_TASKS.get(task_id, {}).get("info") or {}).get("source_segments") or []
        )

    agent_segments = []
    for i, seg in enumerate(segments_data):
        source_text = ""
        if i < len(source_segments):
            source_text = str(source_segments[i] or "").strip()
        timing = str(
            seg.get("timing_text")
            or seg.get("semantic_text")
            or seg.get("translated_text")
            or seg.get("text")
            or ""
        ).strip()
        agent_segments.append(
            {
                "index": int(seg.get("index", i)),
                "text": source_text,
                "translated_text": str(
                    seg.get("translated_text") or seg.get("translation_text") or ""
                ).strip(),
                "semantic_text": str(
                    seg.get("semantic_text")
                    or seg.get("translated_text")
                    or ""
                ).strip(),
                "timing_text": timing,
                "start": seg.get("start"),
                "end": seg.get("end"),
                "speaker": seg.get("speaker"),
            }
        )

    state = {
        "segments": agent_segments,
        "timing_agent_status": timing_status or "success",
    }
    agent = GrammarAgent()
    result = agent.run(manifest, state, task_id)

    out_segments = result.updated_state.get("segments") or []
    grammar_texts: list[str] = []

    with STATE_LOCK:
        if task_id not in AUTO_TASKS:
            return [
                str(
                    s.get("grammar_text")
                    or s.get("timing_text")
                    or s.get("semantic_text")
                    or s.get("text")
                    or ""
                ).strip()
                for s in segments_data
            ]
        info = AUTO_TASKS[task_id].setdefault("info", {})
        sd = info.get("segments_data") or segments_data
        for i, seg in enumerate(out_segments):
            grammar = str(seg.get("grammar_text") or "").strip()
            if not grammar:
                grammar = str(seg.get("timing_text") or "").strip()
            grammar_texts.append(grammar)
            if i < len(sd):
                sd[i]["grammar_text"] = grammar
                sd[i]["text_for_tts"] = grammar
                sd[i]["text"] = grammar
                sd[i]["plain_text"] = grammar
        info["segments_data"] = sd
        info["grammar_agent_path"] = True
        info["grammar_report_path"] = result.updated_state.get("grammar_report_path")
        info["grammar_agent_status"] = result.status
        info["grammar_agent_metrics"] = result.metrics
        info["grammar_naturalizer_shorten_disabled"] = True

    return grammar_texts


def _run_quality_agent_path(
    task_id: str,
    manifest_path: str,
    segments_data: list,
    *,
    grammar_status: str | None = None,
) -> list[dict]:
    """Quality Agent v1.0 — audit segments; gate TTS on quality_decision."""
    from engines.ai_core.quality_agent.agent import QualityAgent, load_manifest

    manifest = load_manifest(manifest_path)

    with STATE_LOCK:
        source_segments = list(
            (AUTO_TASKS.get(task_id, {}).get("info") or {}).get("source_segments") or []
        )
        info = AUTO_TASKS.get(task_id, {}).get("info") or {}
        timing_report_path = info.get("timing_report_path")
        timing_metrics = info.get("timing_agent_metrics")

    agent_segments = []
    for i, seg in enumerate(segments_data):
        source_text = ""
        if i < len(source_segments):
            source_text = str(source_segments[i] or "").strip()
        agent_segments.append(
            {
                "index": int(seg.get("index", i)),
                "text": source_text,
                "translated_text": str(
                    seg.get("translated_text") or seg.get("translation_text") or ""
                ).strip(),
                "semantic_text": str(
                    seg.get("semantic_text")
                    or seg.get("translated_text")
                    or ""
                ).strip(),
                "timing_text": str(
                    seg.get("timing_text")
                    or seg.get("semantic_text")
                    or seg.get("translated_text")
                    or ""
                ).strip(),
                "grammar_text": str(
                    seg.get("grammar_text")
                    or seg.get("timing_text")
                    or ""
                ).strip(),
                "start": seg.get("start"),
                "end": seg.get("end"),
                "speaker": seg.get("speaker"),
            }
        )

    state = {
        "segments": agent_segments,
        "grammar_agent_status": grammar_status or "success",
        "timing_report_path": timing_report_path,
        "timing_agent_metrics": timing_metrics,
    }
    agent = QualityAgent()
    result = agent.run(manifest, state, task_id)

    out_segments = result.updated_state.get("segments") or []
    quality_rows: list[dict] = []

    with STATE_LOCK:
        if task_id not in AUTO_TASKS:
            return out_segments
        info = AUTO_TASKS[task_id].setdefault("info", {})
        sd = info.get("segments_data") or segments_data
        for i, seg in enumerate(out_segments):
            quality_rows.append(
                {
                    "index": seg.get("index", i),
                    "quality_decision": seg.get("quality_decision"),
                    "quality_passed": seg.get("quality_passed"),
                    "quality_scores": seg.get("quality_scores"),
                    "quality_retry_count": seg.get("quality_retry_count"),
                    "quality_routed_to_agent": seg.get("quality_routed_to_agent"),
                    "quality_reasons": seg.get("quality_reasons"),
                }
            )
            if i < len(sd):
                for key in (
                    "quality_decision",
                    "quality_passed",
                    "quality_scores",
                    "quality_retry_count",
                    "quality_routed_to_agent",
                    "quality_reasons",
                    "quality_fallback_text",
                ):
                    if key in seg:
                        sd[i][key] = seg[key]
                # TTS uses grammar_text for ACCEPT/WARNING/FALLBACK; block FAIL unless debug
                decision = str(seg.get("quality_decision") or "ACCEPT").upper()
                passed = bool(seg.get("quality_passed"))
                if passed or result.metrics.get("debug_mode"):
                    tts_text = str(
                        seg.get("quality_fallback_text")
                        or seg.get("grammar_text")
                        or sd[i].get("grammar_text")
                        or ""
                    ).strip()
                    if tts_text:
                        sd[i]["text_for_tts"] = tts_text
                        sd[i]["text"] = tts_text
                        sd[i]["plain_text"] = tts_text
                else:
                    sd[i]["tts_blocked"] = True
                    sd[i]["text_for_tts"] = ""
        info["segments_data"] = sd
        info["quality_agent_path"] = True
        info["quality_report_path"] = result.updated_state.get("quality_report_path")
        info["quality_agent_status"] = result.status
        info["quality_agent_metrics"] = result.metrics
        info["quality_summary"] = result.updated_state.get("quality_summary")

    return quality_rows


def _build_orchestrator_agent_segments(task_id: str, segments_data: list) -> list[dict]:
    """Build agent segment rows for orchestrator state."""
    with STATE_LOCK:
        source_segments = list(
            (AUTO_TASKS.get(task_id, {}).get("info") or {}).get("source_segments") or []
        )
    agent_segments = []
    for i, seg in enumerate(segments_data):
        source_text = ""
        if i < len(source_segments):
            source_text = str(source_segments[i] or "").strip()
        agent_segments.append(
            {
                "index": int(seg.get("index", i)),
                "text": source_text,
                "translated_text": str(
                    seg.get("translated_text")
                    or seg.get("translation_text")
                    or seg.get("text")
                    or ""
                ).strip(),
                "semantic_text": str(seg.get("semantic_text") or "").strip(),
                "timing_text": str(seg.get("timing_text") or "").strip(),
                "grammar_text": str(seg.get("grammar_text") or "").strip(),
                "start": seg.get("start"),
                "end": seg.get("end"),
                "speaker": seg.get("speaker"),
            }
        )
    return agent_segments


def _apply_orchestrator_segments_to_task(
    task_id: str,
    out_segments: list[dict],
    *,
    agent_name: str,
) -> list[str]:
    """Merge orchestrator segment output into task segments_data."""
    field_chain = {
        "semantic": ("semantic_text", "translated_text"),
        "timing": ("timing_text", "semantic_text"),
        "grammar": ("grammar_text", "timing_text"),
        "quality": ("grammar_text",),
    }
    primary, *fallbacks = field_chain.get(agent_name, ("text",))

    texts: list[str] = []
    with STATE_LOCK:
        if task_id not in AUTO_TASKS:
            return [
                str(s.get(primary) or s.get("text") or "").strip() for s in out_segments
            ]
        info = AUTO_TASKS[task_id].setdefault("info", {})
        sd = list(info.get("segments_data") or [])
        for i, seg in enumerate(out_segments):
            value = str(seg.get(primary) or "").strip()
            if not value:
                for fb in fallbacks:
                    value = str(seg.get(fb) or "").strip()
                    if value:
                        break
            texts.append(value)
            if i < len(sd):
                if agent_name in ("semantic", "timing", "grammar") and value:
                    sd[i][primary] = value
                    sd[i]["text_for_tts"] = value
                    sd[i]["text"] = value
                    sd[i]["plain_text"] = value
                if agent_name == "quality":
                    for key in (
                        "quality_decision",
                        "quality_passed",
                        "quality_scores",
                        "quality_retry_count",
                        "quality_routed_to_agent",
                        "quality_reasons",
                        "quality_fallback_text",
                    ):
                        if key in seg:
                            sd[i][key] = seg[key]
        info["segments_data"] = sd
    return texts


def _run_orchestrator_text_agents(
    task_id: str,
    video_path: str,
    manifest_path: str,
    segments_data: list,
) -> list[str] | None:
    """Run semantic→quality via AICoreOrchestrator (manifest path)."""
    from engines.ai_core.orchestrator import AICoreOrchestrator

    with STATE_LOCK:
        info = dict((AUTO_TASKS.get(task_id, {}) or {}).get("info") or {})

    if info.get("quality_agent_path"):
        return None

    agent_segments = _build_orchestrator_agent_segments(task_id, segments_data)
    state = {
        "scope": "text",
        "start_after": "translation",
        "stop_before": "voice",
        "segments": agent_segments,
        "segments_data": segments_data,
        "translation_agent_status": info.get("translation_agent_status") or "success",
        "semantic_agent_path": bool(info.get("semantic_agent_path")),
        "timing_agent_path": bool(info.get("timing_agent_path")),
        "grammar_agent_path": bool(info.get("grammar_agent_path")),
        "quality_agent_path": bool(info.get("quality_agent_path")),
        "timing_report_path": info.get("timing_report_path"),
        "timing_agent_metrics": info.get("timing_agent_metrics"),
        "grammar_agent_status": info.get("grammar_agent_status") or "success",
        "pipeline_mode": "streaming",
    }

    orch = AICoreOrchestrator(output_dir=OUTPUT_DIR)
    with _blocking_progress_heartbeat(
        task_id,
        "ai_core",
        interval=15.0,
        messages=[
            "AI Core: семантика и качество текста",
            "AI Core: обработка сегментов (LLM)",
            "AI Core: адаптация под тайминг",
        ],
    ):
        result = orch.run_pipeline(task_id, video_path, manifest_path, state)

    out_segments = list((result.updated_state or {}).get("segments") or agent_segments)
    segments_texts = [
        str(
            s.get("grammar_text")
            or s.get("timing_text")
            or s.get("semantic_text")
            or s.get("translated_text")
            or s.get("text")
            or ""
        ).strip()
        for s in out_segments
    ]

    with STATE_LOCK:
        if task_id not in AUTO_TASKS:
            return segments_texts
        info = AUTO_TASKS[task_id].setdefault("info", {})
        for flag in (
            "semantic_agent_path",
            "timing_agent_path",
            "grammar_agent_path",
            "quality_agent_path",
            "reviewer_agent_path",
        ):
            if result.updated_state.get(flag) or result.agent_results.get(
                flag.replace("_agent_path", "")
            ):
                info[flag] = True
        for key in (
            "semantic_agent_status",
            "timing_agent_status",
            "grammar_agent_status",
            "quality_agent_status",
            "semantic_report_path",
            "semantic_quality_report_path",
            "semantic_quality_summary",
            "timing_report_path",
            "grammar_report_path",
            "quality_report_path",
            "quality_summary",
            "reviewer_report_path",
            "reviewer_report",
            "reviewer_loop_log",
        ):
            if result.updated_state.get(key):
                info[key] = result.updated_state[key]
        info["orchestrator_text_result"] = {
            "status": result.status,
            "warnings": result.warnings,
            "errors": result.errors,
            "execution_time_ms": result.execution_time_ms,
        }
        info["pipeline_mode"] = str(
            (result.updated_state or {}).get("pipeline_mode") or "streaming"
        )
        sd = info.get("segments_data") or segments_data
        for i, seg in enumerate(out_segments):
            if i >= len(sd):
                continue
            for field in (
                "semantic_text",
                "timing_text",
                "grammar_text",
                "quality_decision",
                "quality_passed",
                "slot_fit_score",
                "grammar_score",
                "grammar_scores",
                "reviewer_approved",
                "reviewer_issues",
                "reviewer_retry_count",
                "reviewer_route_to",
                "final_text",
                "voice_input",
            ):
                if field in seg:
                    sd[i][field] = seg[field]
            tts_text = str(
                seg.get("grammar_text")
                or seg.get("timing_text")
                or seg.get("semantic_text")
                or sd[i].get("text")
                or ""
            ).strip()
            if tts_text:
                sd[i]["text_for_tts"] = tts_text
                sd[i]["text"] = tts_text
                sd[i]["plain_text"] = tts_text
        info["segments_data"] = sd
        info["semantic_naturalizer_shorten_disabled"] = True
        info["timing_naturalizer_shorten_disabled"] = True

        from engines.translation_validation import (
            build_validation_rows_from_info,
            sync_final_text_to_task_info,
            write_translation_validation_json,
        )

        sync_final_text_to_task_info(info)
        try:
            from engines.pipeline_language_gate import validate_segments_target_language
            from engines.translation_validation import recover_mismatched_segments

            tgt_lang = str(info.get("target_lang") or "uk")
            src_rows = list(info.get("source_segments") or [])
            lang_issues = validate_segments_target_language(
                sd,
                source_segments=src_rows,
                target_lang=tgt_lang,
                source_lang=str(info.get("source_lang") or info.get("detected_lang") or ""),
            )
            if lang_issues:
                recovered, still_bad = recover_mismatched_segments(
                    info,
                    lang_issues,
                    task_id=task_id,
                    source_lang=str(info.get("source_lang") or ""),
                    target_lang=tgt_lang,
                    app_dir=APP_DIR,
                )
                sync_final_text_to_task_info(info)
                bad_indices = {int(x.get("index", -1)) for x in still_bad}
                for i, seg in enumerate(sd):
                    if i in bad_indices:
                        seg["requires_llm_adaptation"] = True
                        seg["adaptation_status"] = "LANGUAGE_MISMATCH_PENDING"
                info["post_orchestrator_lang_recovery"] = {
                    "recovered": recovered,
                    "remaining": len(still_bad),
                }
                logger.warning(
                    "Task %s: post-orchestrator language recovery fixed=%d remaining=%d",
                    task_id,
                    recovered,
                    len(still_bad),
                )
        except Exception as lang_exc:
            logger.debug("post-orchestrator language recovery skipped: %s", lang_exc)
        try:
            project_uuid = str(
                (result.updated_state or {}).get("project_uuid")
                or info.get("project_uuid")
                or ""
            )
            validation_rows = build_validation_rows_from_info(info)
            write_translation_validation_json(
                task_id,
                validation_rows,
                project_uuid=project_uuid,
                app_dir=APP_DIR,
            )
            info["translation_validation_path"] = str(
                APP_DIR / "output" / "diagnostics" / task_id / "translation_validation.json"
            )
        except Exception as val_exc:
            logger.debug("translation_validation.json skipped: %s", val_exc)

        # MF-HOTFIX: Meaning Fit BEFORE LOCK (Naturalizer/AI-Core → MF → LOCK).
        # Happy Path (Simple / USE_ADVANCED_ADAPTATION=0): skip — one naturalizer only.
        try:
            from engines.happy_path import advanced_adaptation_enabled as _adv_mf

            _mf_advanced = _adv_mf(info)
        except Exception:
            _mf_advanced = False
        if not _mf_advanced:
            info["meaning_fit_skipped"] = "happy_path"
            logger.info(
                "Task %s: Meaning Fit skipped (happy_path / advanced_adaptation=off)",
                task_id,
            )
        else:
            try:
                from engines.meaning_fit import apply_meaning_fit_before_lock
                from engines.meaning_fit.flags import ensure_meaning_fit_enabled_for_dubbing

                _mf_env = ensure_meaning_fit_enabled_for_dubbing()
                info["meaning_fit_env"] = _mf_env
                _segs_mf = list(info.get("segments_data") or [])
                _mf_rep = apply_meaning_fit_before_lock(
                    _segs_mf,
                    task_info=info,
                    call_site=(
                        "api/auto_dub_api.py::_run_orchestrator_text_agents"
                        ":BEFORE_apply_translation_lock"
                    ),
                )
                logger.info(
                    "Task %s: Meaning Fit pre-LOCK enabled=%s applied=%s "
                    "processed=%s flags=%s",
                    task_id,
                    _mf_rep.get("enabled"),
                    _mf_rep.get("applied"),
                    _mf_rep.get("processed"),
                    _mf_env,
                )
            except Exception as _mf_pre_exc:
                logger.warning(
                    "Task %s: Meaning Fit pre-LOCK soft-fail: %s",
                    task_id,
                    _mf_pre_exc,
                )

        # Freeze TZ P0: TRANSLATION LOCK after Meaning Fit + Validation.
        try:
            from engines.translation_validation import apply_translation_lock_after_validation

            apply_translation_lock_after_validation(info)
        except Exception as lock_exc:
            logger.error("Task %s: TRANSLATION_LOCK failed: %s", task_id, lock_exc)
            raise

    try:
        from engines.ai_core.report import build_and_save_ai_core_report

        build_and_save_ai_core_report(task_id, task_info=info)
    except Exception:
        pass

    return segments_texts


def _streaming_segment_tts_handler(
    list_index: int,
    segment_index: int,
    seg: dict,
    manifest: dict,
    state: dict,
    task_id: str,
) -> dict:
    """Per-segment TTS for AI Core 4.2 streaming voice pool."""
    from engines.pipeline_integrity.tts_segment_fields import (
        apply_tts_synthesis_result,
        measure_playback_duration_ms,
        resolve_segment_text_for_tts,
    )

    out = dict(seg)
    voice = str(state.get("voice") or "uk-UA-OstapNeural")
    text = str(
        out.get("text_for_tts")
        or out.get("voice_input")
        or resolve_segment_text_for_tts(out)
        or ""
    ).strip()
    if not text:
        apply_tts_synthesis_result(
            out,
            tts_text="",
            tts_file_path=None,
            status="empty",
        )
        return out

    new_file = _regen_segment_tts_simple(
        text,
        voice,
        tts_rate=state.get("tts_rate"),
        tts_pitch=state.get("tts_pitch"),
    )
    if not new_file:
        apply_tts_synthesis_result(
            out,
            tts_text=text,
            tts_file_path=None,
            status="empty",
        )
        return out

    task_info = state.get("task_info") or {}
    audio_path = _resolve_segment_audio_path(new_file, task_info)
    duration = measure_playback_duration_ms(audio_path)
    apply_tts_synthesis_result(
        out,
        tts_text=text,
        tts_file_path=new_file,
        playback_duration=duration or None,
        status="generated",
    )
    return out


def _run_streaming_voice_for_task(
    task_id: str,
    segments_data: list,
    *,
    voice: str,
    target_lang: str,
    tts_rate: str | None = None,
    tts_pitch: str | None = None,
    manifest_path: str = "",
) -> bool:
    """Parallel per-segment TTS — replaces batch synthesis when streaming mode is on."""
    from engines.ai_core.streaming_pipeline.voice_stage import StreamingVoicePipeline
    from engines.ai_core.translation_agent.agent import load_manifest

    manifest: dict = {}
    if manifest_path and Path(manifest_path).is_file():
        try:
            manifest = load_manifest(manifest_path)
        except Exception:
            manifest = {}
    if not manifest:
        manifest = {"target_lang": target_lang}

    with STATE_LOCK:
        info = dict((AUTO_TASKS.get(task_id, {}) or {}).get("info") or {})

    agent_segments = _build_orchestrator_agent_segments(task_id, segments_data)
    for i, row in enumerate(agent_segments):
        if i < len(segments_data):
            sd = segments_data[i]
            for key in ("text", "text_for_tts", "plain_text", "voice_input", "final_text"):
                if sd.get(key):
                    row[key] = sd[key]

    state = {
        "segments": agent_segments,
        "voice": voice,
        "tts_rate": tts_rate,
        "tts_pitch": tts_pitch,
        "task_info": {**info, "task_id": task_id},
        "segment_tts_handler": _streaming_segment_tts_handler,
        "target_lang": target_lang,
        "pipeline_mode": "streaming",
    }

    pipe = StreamingVoicePipeline(manifest, state, task_id, app_dir=APP_DIR)
    result = pipe.run()
    out_segments = list((result.updated_state or {}).get("segments") or agent_segments)

    with STATE_LOCK:
        if task_id not in AUTO_TASKS:
            return bool(result.updated_state.get("streaming_voice_done"))
        info = AUTO_TASKS[task_id].setdefault("info", {})
        info["streaming_voice_done"] = True
        info["voice_agent_path"] = True
        info["voice_agent_status"] = result.status
        info["streaming_voice_metrics"] = dict(result.metrics or {})
        sd = info.get("segments_data") or segments_data
        for i, seg in enumerate(out_segments):
            if i >= len(sd):
                continue
            for field in (
                "tts_file_path",
                "playback_duration",
                "tts_text",
                "status",
            ):
                if field in seg:
                    sd[i][field] = seg[field]
        info["segments_data"] = sd

    logger.info(
        "Task %s: streaming voice done — synthesized=%s failed=%s ms=%s",
        task_id,
        result.metrics.get("tts_segments_done"),
        result.metrics.get("tts_segments_failed"),
        result.execution_time_ms,
    )
    return bool(result.updated_state.get("streaming_voice_done"))


def _run_voice_verification_for_task(
    task_id: str,
    segments_data: list,
    task_info: dict,
    *,
    voice: str,
    target_lang: str,
    tts_rate: str | None = None,
    tts_pitch: str | None = None,
    manifest_path: str = "",
) -> None:
    """Run Voice Verification Agent after TTS — ASR check before Mix / Dub Studio."""
    from engines.ai_core.voice_verification_agent import VoiceVerificationAgent
    from engines.ai_core.translation_agent.agent import load_manifest
    from engines.pipeline_integrity.tts_segment_fields import (
        apply_tts_synthesis_result,
        resolve_segment_text_for_tts,
    )

    manifest: dict = {}
    if manifest_path and Path(manifest_path).is_file():
        try:
            manifest = load_manifest(manifest_path)
        except Exception:
            manifest = {}
    if not manifest:
        manifest = {
            "source_lang": task_info.get("source_lang") or "en",
            "target_lang": target_lang,
            "project_uuid": task_info.get("project_uuid") or "",
        }

    agent_segments = _build_orchestrator_agent_segments(task_id, segments_data)
    active_count = sum(
        1 for s in segments_data if s.get("merged_into") is None
    )
    for i, seg in enumerate(segments_data):
        if i >= len(agent_segments):
            break
        row = agent_segments[i]
        row["file"] = seg.get("file") or seg.get("fitted_file")
        row["tts_file_path"] = seg.get("tts_file_path") or row.get("file")
        row["tts_text"] = seg.get("tts_text") or resolve_segment_text_for_tts(seg)
        row["playback_duration"] = seg.get("playback_duration") or seg.get("tts_ms")
        from engines.scheduler import update_time as _sched_update_time

        _asid = str(row.get("segment_id") or seg.get("segment_id") or "").strip()
        if _asid:
            if not row.get("segment_id"):
                row["segment_id"] = _asid
            _sched_update_time(
                [row],
                _asid,
                start_ms=int(seg.get("start_ms") or 0),
                end_ms=int(seg.get("end_ms") or 0),
            )
        row["start"] = seg.get("start")
        row["end"] = seg.get("end")

    def _resolve_for_verification(seg: dict):
        fn = seg.get("tts_file_path") or seg.get("file") or seg.get("fitted_file")
        return _resolve_segment_audio_path(fn, task_info)

    def _regen_for_verification(idx: int, seg: dict, reason: str):
        _update_progress_detail(
            task_id,
            phase="voice_verification",
            tts_substep="voice_verify",
            current_segment=idx + 1,
            total_segments=active_count or len(segments_data),
            verification_route=reason,
        )
        text = resolve_segment_text_for_tts(seg)
        if not text.strip():
            return None
        new_file = _regen_segment_tts_simple(
            text,
            voice,
            tts_rate=tts_rate,
            tts_pitch=tts_pitch,
        )
        if not new_file:
            return None
        try:
            from pydub import AudioSegment

            playback = int(len(AudioSegment.from_file(str(_artifacts_dir() / new_file))))
        except Exception:
            playback = 0
        if idx < len(segments_data):
            sd = segments_data[idx]
            sd["file"] = new_file
            sd["text_for_tts"] = text
            apply_tts_synthesis_result(
                sd,
                tts_text=text,
                tts_file_path=new_file,
                playback_duration=playback,
                status="generated",
            )
            from engines.pipeline_integrity.exceptions import IdentityMismatchError
            from engines.pipeline_integrity.identity_guard import bind_after_tts

            try:
                bind_after_tts(
                    sd,
                    tts_text=text,
                    audio_path=new_file,
                    stage="regen",
                    allow_rebind=True,
                    segments_data=segments_data,
                )
            except IdentityMismatchError:
                raise
            except Exception as _ig_r:
                logger.warning("IdentityGuard regen bind skipped: %s", _ig_r)
            seg = {**seg, **sd}
        else:
            apply_tts_synthesis_result(
                seg,
                tts_text=text,
                tts_file_path=new_file,
                playback_duration=playback,
                status="generated",
            )
            seg["file"] = new_file
            from engines.pipeline_integrity.exceptions import IdentityMismatchError
            from engines.pipeline_integrity.identity_guard import bind_after_tts

            try:
                bind_after_tts(
                    seg,
                    tts_text=text,
                    audio_path=new_file,
                    stage="regen",
                    allow_rebind=True,
                )
            except IdentityMismatchError:
                raise
            except Exception as _ig_r:
                logger.warning("IdentityGuard regen bind skipped: %s", _ig_r)
        seg["voice_verification_regen_reason"] = reason
        logger.info(
            "Task %s: voice verification regen seg=%s reason=%s file=%s",
            task_id,
            idx,
            reason,
            new_file,
        )
        return seg

    def _voice_verification_progress(
        *,
        segment_index: int,
        position: int,
        total: int,
        attempt: int,
        route: str,
    ) -> None:
        _update_progress_detail(
            task_id,
            phase="voice_verification",
            tts_substep="voice_verify",
            current_segment=position,
            total_segments=total,
            segments_done=max(0, position - 1),
            verification_attempt=attempt or None,
            verification_route=route or None,
        )

    _update_progress_detail(
        task_id,
        phase="voice_verification",
        tts_substep="voice_verify",
        total_segments=active_count or len(segments_data),
        segments_done=0,
        current_segment=0,
    )

    state = {
        "segments": agent_segments,
        "segments_data": segments_data,
        "target_lang": target_lang,
        "voice_verification_resolve_audio": _resolve_for_verification,
        "voice_verification_regen": _regen_for_verification,
        "voice_verification_progress": _voice_verification_progress,
    }

    agent = VoiceVerificationAgent(output_dir=OUTPUT_DIR)
    result = agent.run(manifest, state, task_id)

    out_segments = list((result.updated_state or {}).get("segments") or agent_segments)
    from engines.pipeline_integrity.identity_guard import resolve_row_by_identity

    _vv_keys = (
        "semantic_text",
        "timing_text",
        "grammar_text",
        "tts_text",
        "file",
        "tts_file_path",
        "playback_duration",
        "voice_verification_passed",
        "voice_verification_metrics",
        "voice_verification_issues",
        "voice_verification_retry_count",
        "voice_verification_asr_text",
        "voice_verification_route_to",
    )
    _sd_has_ids = any(
        isinstance(s, dict) and str(s.get("segment_id") or "").strip()
        for s in segments_data
    )
    for i, seg in enumerate(out_segments):
        if not isinstance(seg, dict):
            continue
        _sid_vv = str(seg.get("segment_id") or "").strip()
        if _sid_vv and _sd_has_ids:
            _tgt_vv, _how_vv = resolve_row_by_identity(
                segments_data, segment_id=_sid_vv, index=None
            )
            if _tgt_vv is None:
                logger.warning(
                    "IdentityGuard: voice-verify no row for segment_id=%s",
                    _sid_vv,
                )
                continue
        else:
            if i >= len(segments_data):
                continue
            _tgt_vv, _how_vv = resolve_row_by_identity(
                segments_data, segment_id="", index=i
            )
            if _tgt_vv is None:
                continue
            if _sd_has_ids:
                logger.warning(
                    "IdentityGuard: voice-verify copy index fallback idx=%s",
                    i,
                )
        for key in _vv_keys:
            if key in seg:
                _tgt_vv[key] = seg[key]

    task_info["voice_verification_agent_path"] = True
    task_info["voice_verification_passed"] = bool(
        (result.updated_state or {}).get("voice_verification_passed")
    )
    task_info["voice_verification_report_path"] = (result.updated_state or {}).get(
        "voice_verification_report_path"
    )
    task_info["voice_verification_report"] = (result.updated_state or {}).get(
        "voice_verification_report"
    )
    task_info["voice_verification_result"] = {
        "status": result.status,
        "warnings": result.warnings,
        "errors": result.errors,
    }


def _segments_data_from_agent(segments_data: list[dict], info: dict) -> list[dict]:
    """Map agent segments (translated_text) into pipeline segments_data rows."""
    old_by_idx = {
        int(s.get("index", i)): s for i, s in enumerate(info.get("segments_data") or [])
    }
    source_word_maps = info.get("source_word_maps") or []
    source_segments = list(info.get("source_segments") or [])
    out: list[dict] = []
    for i, seg in enumerate(segments_data):
        original = ""
        if i < len(source_segments):
            original = str(source_segments[i] or "").strip()
        if not original:
            original = str(seg.get("text") or "").strip()
        working = str(seg.get("translated_text") or "").strip()
        entry: dict = {
            "index": i,
            "original_text": original,
            "text": working,
            "plain_text": working,
            "translation_text": working,
            "translated_text": working,
            "file": None,
        }
        old = old_by_idx.get(i) or {}
        _keep_sid = str(old.get("segment_id") or seg.get("segment_id") or "").strip()
        if _keep_sid:
            entry["segment_id"] = _keep_sid
        if seg.get("start") is not None:
            entry["start"] = seg["start"]
        if seg.get("end") is not None:
            entry["end"] = seg["end"]
        if seg.get("speaker") is not None:
            entry["speaker"] = seg["speaker"]
        if i < len(source_word_maps):
            entry["source_word_map"] = source_word_maps[i]
        elif old.get("source_word_map"):
            entry["source_word_map"] = old["source_word_map"]
        if seg.get("confidence"):
            entry["translation_confidence"] = seg["confidence"]
        out.append(entry)
    _stamp_segment_identity(out)
    return out


def _prepare_translated_segments(
    task_id: str,
    source_segments: list,
    timing_map: list,
    translation_source_lang: str,
    target_lang: str,
    ui_lang: str = "ru",
    translate_meta: list | None = None,
) -> list[str]:
    """
    Гибридный перевод: пакеты с контекстом + натурализация + выравнивание timing_map.
    Никогда не бросает ValueError — только лог + авто-recovery.
    """
    # Stage 7b: Simple must never enter UniversalTranslationPipeline / Qwen here.
    try:
        from engines.simple_mt_path import run_locked_simple_mt, use_locked_simple_mt

        with STATE_LOCK:
            _info_prep = dict((AUTO_TASKS.get(task_id) or {}).get("info") or {})
        if use_locked_simple_mt(_info_prep):
            logger.info(
                "Task %s: _prepare_translated_segments redirected to locked Simple MT",
                task_id,
            )
            segs, stats = run_locked_simple_mt(
                list(source_segments),
                translation_source_lang,
                target_lang,
                app_dir=APP_DIR,
            )
            if translate_meta is not None:
                translate_meta.append(
                    {
                        "pipeline": str(stats.get("translate_method") or "marian_batch"),
                        "translation_sec": float(stats.get("mt_wall_sec") or 0),
                        "marian_sec": float(stats.get("mt_wall_sec") or 0),
                        "llm_adaptation_sec": 0.0,
                        "llm_adaptation_used": False,
                        "translation_agent": False,
                        **{
                            k: stats.get(k)
                            for k in (
                                "mt_wall_sec",
                                "mt_engine",
                                "mt_cache_hits",
                                "mt_cache_misses",
                                "mt_calls",
                                "translate_method",
                                "naturalizer_executed",
                                "naturalizer_applied",
                            )
                        },
                    }
                )
            with STATE_LOCK:
                if task_id in AUTO_TASKS:
                    from engines.simple_mt_path import stamp_simple_mt_lock

                    info = AUTO_TASKS[task_id].setdefault("info", {})
                    stamp_simple_mt_lock(info)
                    info["translate_method"] = str(
                        stats.get("translate_method") or "marian_batch"
                    )
                    for k, v in stats.items():
                        if k == "translation_timing":
                            info["translation_timing"] = v
                        else:
                            info[k] = v
            return segs
    except Exception as _redir_exc:
        logger.warning(
            "Task %s: Simple MT redirect failed in prepare: %s", task_id, _redir_exc
        )

    from engines.cleaner import align_segments_to_timing_map, split_by_timing_map

    lp = LOCALIZATION.get(ui_lang, LOCALIZATION["ru"])

    if not source_segments:
        logger.error("Task %s: source_segments empty before translate", task_id)
        raise RuntimeError(lp["empty_stt"])

    logger.info(
        "Task %s: natural translate %d segments %s -> %s",
        task_id,
        len(source_segments),
        translation_source_lang,
        target_lang,
    )

    if translate_meta is None:
        translate_meta = []

    def _translate_progress(done: int, total: int) -> None:
        frac = done / max(total, 1)
        llm_meta: dict = {}
        try:
            from engines.llm_adaptation_mode import detect_capabilities

            caps = detect_capabilities()
            from engines.llm_diagnostics import format_model_display, provider_label

            llm_meta = {
                "llm_model": caps.get("model"),
                "llm_provider": caps.get("provider"),
                "llm_model_display": format_model_display(
                    str(caps.get("model") or ""), provider=str(caps.get("provider") or "")
                ),
                "llm_provider_label": provider_label(str(caps.get("provider") or "")),
            }
        except Exception:
            pass
        with STATE_LOCK:
            t = AUTO_TASKS.get(task_id)
            if t and t.get("status") == "running":
                t["progress"] = round(55.0 + frac * 8.0, 1)
        _update_progress_detail(
            task_id,
            phase="translate",
            segments_done=done,
            total_segments=total,
            current_segment=min(done + 1, total) if total else done,
            operation="translation",
            eta_sec=_estimate_eta_sec(task_id, frac),
            live_message=None,
            **llm_meta,
        )

    # Event Bus path (TZ Stage 1) — agents communicate only via publish/subscribe.
    try:
        from core.event_pipeline import event_bus_enabled, run_translation_chain_sync

        if event_bus_enabled():
            logger.info("Task %s: translation via Event Bus", task_id)
            with _llm_inflight_heartbeat(task_id):
                bus_result = run_translation_chain_sync(
                    task_id=task_id,
                    source_segments=source_segments,
                    timing_map=timing_map,
                    source_lang=translation_source_lang,
                    target_lang=target_lang,
                    app_dir=APP_DIR,
                    translate_meta=translate_meta,
                    progress_cb=_translate_progress,
                )
            if not bus_result.ok:
                err = "; ".join(bus_result.errors) or "event_bus_translation_failed"
                logger.error("Task %s: Event Bus translation failed: %s", task_id, err)
                raise RuntimeError(err)
            segments = list(bus_result.segments)
            with STATE_LOCK:
                if task_id in AUTO_TASKS:
                    if bus_result.translation_audits:
                        AUTO_TASKS[task_id]["info"]["translation_audits"] = (
                            bus_result.translation_audits
                        )
                    meta = bus_result.translation_meta or {}
                    if meta.get("translation_trace_log"):
                        AUTO_TASKS[task_id]["info"]["translation_trace_log"] = meta[
                            "translation_trace_log"
                        ]
                    AUTO_TASKS[task_id]["info"]["timing_aware_applied"] = bool(
                        meta.get("timing_aware_applied")
                    )
                    AUTO_TASKS[task_id]["info"]["timing_aware_executed"] = bool(
                        meta.get("timing_aware_executed")
                    )
                    AUTO_TASKS[task_id]["info"]["naturalizer_applied"] = bool(
                        meta.get("naturalizer_applied")
                    )
                    AUTO_TASKS[task_id]["info"]["naturalizer_executed"] = bool(
                        meta.get("naturalizer_executed")
                    )
                    AUTO_TASKS[task_id]["info"]["pipeline_stages"] = (
                        meta.get("pipeline_stages") or {}
                    )
                    AUTO_TASKS[task_id]["info"]["timing_aware_records"] = (
                        meta.get("timing_aware_records") or []
                    )
                    AUTO_TASKS[task_id]["info"]["event_bus"] = True
            if timing_map and len(segments) != len(timing_map):
                logger.error(
                    "Task %s: segment alignment failed map=%d segments=%d",
                    task_id,
                    len(timing_map),
                    len(segments),
                )
                raise RuntimeError(lp["segment_mismatch"])
            logger.info(
                "Task %s: prepared %d translated segments via Event Bus (timing_map=%d)",
                task_id,
                len(segments),
                len(timing_map) if timing_map else 0,
            )
            return segments
    except RuntimeError:
        raise
    except Exception:
        logger.warning(
            "Task %s: Event Bus translation unavailable, using legacy path",
            task_id,
            exc_info=True,
        )

    from engines.cleaner import align_segments_to_timing_map, split_by_timing_map
    from engines.translation_pipeline import UniversalTranslationPipeline

    _update_progress_detail(
        task_id,
        phase="translate",
        live_message="Перевод: Marian MT → LLM адаптация…",
        ai_core_timeout=None,
        translation_subphase="marian_mt",
    )

    pipe = UniversalTranslationPipeline(app_dir=APP_DIR, task_id=task_id)

    with _llm_inflight_heartbeat(task_id):
        result = pipe.translate_segments(
            source_segments,
            timing_map,
            translation_source_lang,
            target_lang,
            translate_meta_out=translate_meta,
            progress_cb=_translate_progress,
        )
    translated_segments = result.segments
    pipe.flush_quality_log(
        src=translation_source_lang,
        tgt=target_lang,
        engines=result.meta.get("engines"),
    )

    with STATE_LOCK:
        if task_id in AUTO_TASKS:
            AUTO_TASKS[task_id]["info"]["translation_audits"] = pipe.quality_log.records_as_dicts()
            if result.meta.get("translation_trace_log"):
                AUTO_TASKS[task_id]["info"]["translation_trace_log"] = result.meta[
                    "translation_trace_log"
                ]
            AUTO_TASKS[task_id]["info"]["timing_aware_applied"] = bool(
                result.meta.get("timing_aware_applied")
            )
            AUTO_TASKS[task_id]["info"]["timing_aware_executed"] = bool(
                result.meta.get("timing_aware_executed")
            )
            AUTO_TASKS[task_id]["info"]["naturalizer_applied"] = bool(
                result.meta.get("naturalizer_applied")
            )
            AUTO_TASKS[task_id]["info"]["naturalizer_executed"] = bool(
                result.meta.get("naturalizer_executed")
            )
            AUTO_TASKS[task_id]["info"]["pipeline_stages"] = (
                result.meta.get("pipeline_stages") or {}
            )
            AUTO_TASKS[task_id]["info"]["timing_aware_records"] = (
                result.meta.get("timing_aware_records") or []
            )

    segments = align_segments_to_timing_map(translated_segments, timing_map)

    if timing_map and len(segments) != len(timing_map):
        block_text = "\n".join(translated_segments)
        segments = split_by_timing_map(block_text, timing_map)

    if timing_map and len(segments) != len(timing_map):
        logger.error(
            "Task %s: segment alignment failed map=%d segments=%d",
            task_id,
            len(timing_map),
            len(segments),
        )
        raise RuntimeError(lp["segment_mismatch"])

    logger.info(
        "Task %s: prepared %d translated segments (timing_map=%d)",
        task_id,
        len(segments),
        len(timing_map) if timing_map else 0,
    )
    return segments


def _run_pipeline(
    task_id,
    video_path,
    target_lang,
    voice,
    model_size,
    mix_mode,
    mix_volumes,
    keep_original_track,
    dub_mode,
    mix_volume,
    source_lang,
    target_duration_ms,
    skip_translate=False,
    ui_lang="ru",
    segmentation_mode="timing",
    ocr_enabled=False,
    dub_style="modern",
    skip_tts=False,
    tts_rate=None,
    tts_pitch=None,
    content_mode="movie",
):
    lp = LOCALIZATION.get(ui_lang, LOCALIZATION["ru"])

    # ── Project Session: isolation boundary for this dubbing run ─────────────
    # Each task gets its own session. Old session (if any) for a previous run
    # is NOT touched — this guarantees complete isolation between projects.
    try:
        from engines.dubbing_engine.project_session import create_session, finish_session
        _session = create_session(
            task_id=task_id,
            output_dir=OUTPUT_DIR,
            content_mode=content_mode,
        )
    except Exception as _sess_exc:
        _session = None
        logger.warning("[Session] could not create session: %s", _sess_exc)

    profiler = None
    from engines.model_manager.runtime import set_offline_only
    from engines.dub_runtime import apply_ml_thread_limits

    apply_ml_thread_limits()
    set_offline_only(True)
    # Simple / Happy Path: lock gates before any stage (TZ reference pipeline).
    try:
        from engines.simple_dub_pipeline import apply_simple_pipeline_policy

        with STATE_LOCK:
            if task_id in AUTO_TASKS:
                _info0 = AUTO_TASKS[task_id].setdefault("info", {})
                if str(_info0.get("user_mode") or "basic").lower() in (
                    "basic",
                    "simple",
                    "",
                ) or _info0.get("happy_path"):
                    apply_simple_pipeline_policy(
                        _info0, user_mode=str(_info0.get("user_mode") or "basic")
                    )
    except Exception as _pol_exc:
        logger.debug("Task %s: simple policy stamp skipped: %s", task_id, _pol_exc)
    # MF-HOTFIX: enable Meaning Fit on real dubbing path (env unset → ON; =0 wins)
    # Happy Path: do NOT auto-enable MF — keep advanced shorteners off.
    try:
        from engines.happy_path import advanced_adaptation_enabled as _adv_boot

        with STATE_LOCK:
            _info_boot = dict((AUTO_TASKS.get(task_id) or {}).get("info") or {})
        if not _adv_boot(_info_boot):
            with STATE_LOCK:
                if task_id in AUTO_TASKS:
                    AUTO_TASKS[task_id].setdefault("info", {})["meaning_fit_skipped"] = (
                        "happy_path"
                    )
            logger.info(
                "Task %s: Meaning Fit flag boot skipped (happy_path)", task_id
            )
        else:
            from engines.meaning_fit.flags import ensure_meaning_fit_enabled_for_dubbing

            _mf_boot = ensure_meaning_fit_enabled_for_dubbing()
            with STATE_LOCK:
                if task_id in AUTO_TASKS:
                    AUTO_TASKS[task_id].setdefault("info", {})["meaning_fit_env"] = _mf_boot
            logger.info("Task %s: Meaning Fit dubbing flags %s", task_id, _mf_boot)
    except Exception as _mf_boot_exc:
        logger.warning("Task %s: Meaning Fit flag boot skipped: %s", task_id, _mf_boot_exc)
    _ctx_token = None
    if _session:
        from engines.dubbing_engine.session_adapter import _ACTIVE_ARTIFACTS

        _ctx_token = _ACTIVE_ARTIFACTS.set(_session.session_dir)
    try:
        return _run_pipeline_inner(
            task_id,
            video_path,
            target_lang,
            voice,
            model_size,
            mix_mode,
            mix_volumes,
            keep_original_track,
            dub_mode,
            mix_volume,
            source_lang,
            target_duration_ms,
            skip_translate=skip_translate,
            ui_lang=ui_lang,
            segmentation_mode=segmentation_mode,
            ocr_enabled=ocr_enabled,
            dub_style=dub_style,
            skip_tts=skip_tts,
            tts_rate=tts_rate,
            tts_pitch=tts_pitch,
            lp=lp,
            content_mode=content_mode,
            project_session=_session,
        )
    finally:
        set_offline_only(False)
        if _ctx_token is not None:
            from engines.dubbing_engine.session_adapter import _ACTIVE_ARTIFACTS

            _ACTIVE_ARTIFACTS.reset(_ctx_token)
        try:
            if _session:
                from engines.dubbing_engine.project_session import finish_session

                finish_session(task_id)
        except Exception:
            pass


def _run_agent_safe(task_id, agent_name, fn, *args, fallback_fn=None, segment_idx=None, record_success=True, **kwargs):
    """Run agent fn; on error record to OpenDDF, use fallback or return None.

    In Debug/Learning mode (IS_DEBUG_LEARNING_MODE) all agent errors are
    downgraded to warnings and the pipeline continues. Outside debug mode the
    behaviour is identical — callers still decide what to do with None/fallback.

    Returns the function result on success, fallback_fn() result on error (if
    provided), or None when both fail.
    """
    try:
        result = fn(*args, **kwargs)
        if record_success:
            _open_ddf.record_agent(task_id, agent_name, called=True, success=True,
                                   segment_idx=segment_idx)
        return result
    except Exception as exc:
        _open_ddf.record_agent(task_id, agent_name, called=True, success=False,
                               error=str(exc), fallback_used=fallback_fn is not None,
                               segment_idx=segment_idx)
        logger.warning("[DDF] Agent %s failed: %s — continuing", agent_name, exc)
        if fallback_fn is not None:
            try:
                return fallback_fn(*args, **kwargs)
            except Exception as fb_exc:
                logger.warning("[DDF] Fallback for %s also failed: %s", agent_name, fb_exc)
        return None


def _run_ai_core_orchestrator(
    task_id: str,
    video_path: str,
    manifest_path: str,
    state: dict,
    *,
    agents: list[str],
    hooks=None,
) -> "PipelineResult":
    """Run AI Core orchestrator subset; never raises."""
    from engines.ai_core.orchestrator import AICoreOrchestrator, PipelineHooks

    try:
        orch = AICoreOrchestrator(hooks=hooks or PipelineHooks())
        return orch.run_pipeline(
            task_id,
            video_path,
            manifest_path,
            state,
            agents=agents,
        )
    except Exception as exc:
        logger.warning("[Orchestrator] task %s failed: %s — continuing", task_id, exc)
        from engines.ai_core.orchestrator import PipelineResult

        return PipelineResult(
            status="partial",
            state=state,
            warnings=[str(exc)],
            errors=[str(exc)],
        )


def _ensure_agent_translation_audits(
    info: dict,
    *,
    source_segments: list | None = None,
    translated: list[str] | None = None,
    engine: str | None = None,
    src_lang: str | None = None,
    tgt_lang: str | None = None,
) -> None:
    """Fill translation_audits after Translation Agent so Review has Raw MT."""
    from engines.translation_quality_log import synthesize_audits_from_segments

    sources = list(source_segments or info.get("source_segments") or [])
    if translated is None:
        sd = info.get("segments_data") or []
        translated = [
            str(
                s.get("translated_text")
                or s.get("translation_text")
                or s.get("text")
                or ""
            ).strip()
            for s in sd
        ]
    if not sources or not translated:
        return
    existing = list(info.get("translation_audits") or [])
    by_idx = {int(a.get("index", -1)): a for a in existing}
    need_synth = not existing or any(
        not str(by_idx.get(i, {}).get("raw_translation") or "").strip()
        for i in range(min(len(sources), len(translated)))
    )
    if not need_synth:
        return
    engine_id = (
        engine
        or (info.get("translation_agent_metrics") or {}).get("translator_used")
        or (info.get("translate_meta") or {}).get("translator_used")
        or "translation_agent"
    )
    audits = synthesize_audits_from_segments(
        sources,
        list(translated),
        src_lang or info.get("source_lang") or info.get("detected_lang") or "en",
        tgt_lang or info.get("target_lang") or "uk",
        engine=str(engine_id),
    )
    merged: list[dict] = []
    for a in audits:
        row = dict(a.__dict__)
        old = by_idx.get(int(row.get("index", -1)))
        if old:
            # Keep later-stage fields; never wipe Raw MT once set
            for key, val in old.items():
                if key in ("raw_translation", "whisper_text") and str(
                    row.get(key) or ""
                ).strip():
                    continue
                if key not in row or row.get(key) in (None, "", [], {}):
                    row[key] = val
                elif key in (
                    "semantic_text",
                    "semantic_engine_text",
                    "final_text",
                    "tts_text",
                    "naturalized_text",
                ) and str(old.get(key) or "").strip():
                    if key == "final_text" and not str(old.get(key) or "").strip():
                        pass
                    elif key != "raw_translation":
                        # Prefer existing semantic/final if agent synth overwrote,
                        # but never restore a multi-segment MT blob over a debleeded Final.
                        if key in ("semantic_text", "semantic_engine_text") and str(
                            old.get(key) or ""
                        ).strip():
                            try:
                                from engines.translation_validation import (
                                    is_shared_mt_blob_reclaim,
                                )

                                new_final = str(row.get("final_text") or "").strip()
                                old_sem = str(old.get(key) or "").strip()
                                if new_final and is_shared_mt_blob_reclaim(
                                    new_final, old_sem
                                ):
                                    pass
                                else:
                                    row[key] = old[key]
                            except Exception:
                                row[key] = old[key]
            if not str(row.get("raw_translation") or "").strip():
                row["raw_translation"] = str(old.get("raw_translation") or "")
        merged.append(row)
    info["translation_audits"] = merged


def _sync_orchestrator_segments_to_task(task_id: str, state: dict, agent_name: str) -> None:
    """Apply orchestrator segment state back to AUTO_TASKS info."""
    segments = list(state.get("segments") or [])
    if not segments:
        return
    with STATE_LOCK:
        if task_id not in AUTO_TASKS:
            return
        info = AUTO_TASKS[task_id].setdefault("info", {})
        info["segments_data"] = _segments_data_from_agent(segments, info)
        info[f"{agent_name}_agent_path"] = True
        info[f"{agent_name}_agent_status"] = "success"
        if agent_name == "translation":
            from engines.translation_validation import sync_final_text_to_task_info

            _ensure_agent_translation_audits(
                info,
                engine=(info.get("translation_agent_metrics") or {}).get(
                    "translator_used"
                ),
            )
            sync_final_text_to_task_info(info)


def _run_pipeline_inner(
    task_id,
    video_path,
    target_lang,
    voice,
    model_size,
    mix_mode,
    mix_volumes,
    keep_original_track,
    dub_mode,
    mix_volume,
    source_lang,
    target_duration_ms,
    skip_translate=False,
    ui_lang="ru",
    segmentation_mode="timing",
    ocr_enabled=False,
    dub_style="modern",
    skip_tts=False,
    tts_rate=None,
    tts_pitch=None,
    lp=None,
    content_mode="movie",
    project_session=None,
):
    if lp is None:
        lp = LOCALIZATION.get(ui_lang, LOCALIZATION["ru"])
    profiler = None
    pipeline_timer = None
    try:
        with STATE_LOCK:
            task = AUTO_TASKS[task_id]
            control = AUTO_TASK_CONTROLS[task_id]
            if project_session:
                from engines.dubbing_engine.session_adapter import SessionContextAdapter

                _session_ctx = SessionContextAdapter(project_session)
                _session_ctx.bind_task_info(task["info"])
                project_session.set_launch_config(
                    target_lang=target_lang,
                    voice=voice,
                    model_size=model_size,
                    content_mode=content_mode,
                    dub_style=dub_style,
                )
                base_id = _session_ctx.base_id
            else:
                base_id = uuid.uuid4().hex[:8]
            task["info"]["content_mode"] = content_mode

        from engines.dubbing_engine.pipeline_failure_diag import (
            RuntimeDiagnosticsRecorder,
            STAGE_AUDIO_EXTRACTION,
            STAGE_AUDIO_MIX,
            STAGE_FFMPEG,
            STAGE_SOURCE_SEPARATION,
            STAGE_STT,
            STAGE_TIMING,
            STAGE_TRANSLATION,
            STAGE_TTS,
        )

        runtime_diag = RuntimeDiagnosticsRecorder(task_id)
        try:
            from engines.pipeline_integrity.passive_openddf import start_diagnostic_run

            with STATE_LOCK:
                task = AUTO_TASKS.get(task_id)
                info = dict((task or {}).get("info") or {})
            start_diagnostic_run(task_id, task_info=info)
            with STATE_LOCK:
                task = AUTO_TASKS.get(task_id)
                if task:
                    task.setdefault("info", {})["openddf_run_id"] = task_id
                    task["info"]["passive_openddf"] = {
                        "run_id": task_id,
                        "mode": "passive",
                    }
        except ImportError:
            pass
        _set_step(task_id, "preparing", 1.0)
        _update_progress_detail(task_id, phase="preparing", live_message="Подготовка пайплайна…")

        with STATE_LOCK:
            _pinfo = AUTO_TASKS[task_id].setdefault("info", {})
            _pinfo["pipeline_started_at"] = time.time()
            _pinfo["task_id"] = task_id
            _pinfo["developer_preview_enabled"] = _dev_preview_enabled(_pinfo)
            if _pinfo.get("developer_preview_enabled"):
                from engines.developer_preview import record_agent_event

                record_agent_event(_pinfo, "extract", "running")

        # Launch Decision Trace: seed agent slots (no `not_called` allowed
        # anywhere) and stamp the "Video Loaded" stage as SUCCESS.
        _launch_trace_seed(task)
        _launch_trace_stage(
            task,
            "Video Loaded",
            status="SUCCESS",
            reason="pipeline_entered",
            line=6857,
            data={"video_path": str(video_path)},
        )

        # ── Planner Agent v3.0 (READ ONLY, before Whisper) ─────────────────
        try:
            from engines.ai_core.planner_agent import PlannerAgent
            from engines.core.feature_flags import IS_DEBUG_LEARNING_MODE

            _update_progress_detail(task_id, phase="preparing", live_message="Планирование…")
            planner = PlannerAgent()
            with _blocking_progress_heartbeat(
                task_id,
                "preparing",
                interval=25.0,
                messages=[
                    "Планирование…",
                    "Анализ видео…",
                    "Подготовка стратегии обработки…",
                ],
            ):
                plan_result = planner.run(
                    video_path, target_lang, source_lang, task_id=task_id
                )
            with STATE_LOCK:
                task = AUTO_TASKS[task_id]
                task["info"]["project_uuid"] = plan_result.updated_state["project_uuid"]
                task["info"]["manifest_path"] = plan_result.updated_state["manifest_path"]
                task["info"]["planner_report_path"] = plan_result.updated_state.get(
                    "planner_report_path"
                )
                task["info"]["processing_strategy"] = plan_result.updated_state.get(
                    "processing_strategy"
                )
                task["info"]["planner_status"] = plan_result.status
            if plan_result.status == "error" and not IS_DEBUG_LEARNING_MODE():
                critical = any(
                    e.startswith("video_not_found") for e in plan_result.errors
                )
                if critical:
                    return _fail(
                        task_id,
                        plan_result.errors,
                        stage=STAGE_AUDIO_EXTRACTION,
                        error_code="PLANNER_CRITICAL",
                    )
            elif plan_result.warnings:
                logger.warning(
                    "Task %s: planner warnings: %s", task_id, plan_result.warnings
                )
            _launch_trace_agent(
                task,
                "planner",
                called=True,
                called_by="api/auto_dub_api.py:PlannerAgent.run",
                line=6885,
                data={"status": plan_result.status},
            )
        except Exception as _planner_exc:
            logger.warning("Task %s: planner agent skipped: %s", task_id, _planner_exc)
            _launch_trace_agent(
                task,
                "planner",
                called=False,
                skipped_reason=f"planner_exception:{type(_planner_exc).__name__}",
                line=6899,
                data={"error": str(_planner_exc)[:200]},
            )

        runtime_diag.stage_begin(STAGE_AUDIO_EXTRACTION)

        from engines.dev_diagnostics import DevDiagnostics
        from engines.timing_fit import cleanup_stale_work_dirs
        from engines.pipeline_profiler import PipelineProfiler
        from engines.pipeline_timer import PipelineTimer
        from engines.hardware_probe import probe_hardware

        _update_progress_detail(task_id, phase="preparing", live_message="Инициализация…")
        with _blocking_progress_heartbeat(
            task_id,
            "preparing",
            interval=25.0,
            messages=[
                "Инициализация…",
                "Проверка оборудования…",
                "Подготовка к извлечению аудио…",
            ],
        ):
            dev_diag = DevDiagnostics(task_id, APP_DIR)
            from engines.word_timing_map.config import current_phase_label, sync_mode
            from engines.word_timing_map.phase0 import WtmCheckpointLog

            wtm_cp_log = WtmCheckpointLog(task_id, APP_DIR)
            pipeline_timer = None

            def _timer_task_update(timing: dict) -> None:
                with STATE_LOCK:
                    t = AUTO_TASKS.get(task_id)
                    if t:
                        t.setdefault("info", {})["pipeline_timing"] = timing

            pipeline_timer = PipelineTimer(task_id, APP_DIR, on_update=_timer_task_update)
            profiler = pipeline_timer.profiler
            profiler.set_meta(
                hardware=probe_hardware(),
                model_size=model_size,
                target_lang=target_lang,
                dub_style=dub_style,
            )
            cleanup_stale_work_dirs()

        if segmentation_mode not in ("timing", "sentence", "adaptive"):
            segmentation_mode = "timing"

        with STATE_LOCK:
            task = AUTO_TASKS[task_id]
            task["info"]["segments_data"] = []
            task["info"]["source_segments"] = []
            task["info"]["timing_map_backup"] = []
            task["info"]["timed_audio"] = None
            task["info"]["dev_diagnostics"] = dev_diag.paths()

        with STATE_LOCK:
            task = AUTO_TASKS[task_id]
            control = AUTO_TASK_CONTROLS[task_id]

            task["info"].update(
                {
                    "video_path_backup": video_path,
                    "mix_mode_backup": mix_mode,
                    "mix_volumes_backup": mix_volumes,
                    "keep_original_track": keep_original_track,
                    "dub_mode_backup": dub_mode,
                    "mix_volume_backup": mix_volume,
                    "target_duration_ms": target_duration_ms,
                    "dub_style": dub_style,
                    "skip_tts": skip_tts,
                    "tts_rate": tts_rate,
                    "tts_pitch": tts_pitch,
                }
            )

        # ══ ШАГ 1: Извлечение аудио ══════════════════════════════════════
        _set_step(task_id, "extract_audio", 2.0)
        profiler.start("ffmpeg")
        pipeline_timer.start("extract")
        runtime_diag.stage_begin(STAGE_AUDIO_EXTRACTION)

        from engines.audio_extraction import (
            error_code_for_result,
            extract_audio_from_video,
            record_audio_extraction_openddf,
            user_friendly_extract_error,
        )
        from engines.dubbing_engine.pipeline_failure_diag import STAGE_AUDIO_EXTRACTION

        with STATE_LOCK:
            _project_uuid = (task.get("info") or {}).get("project_uuid")
        _audio_out = _artifacts_dir(task.get("info")) / f"{base_id}_extracted.mp3"
        _extract_result = extract_audio_from_video(
            video_path,
            str(_artifacts_dir(task.get("info"))),
            task_id,
            output_path=str(_audio_out),
            max_retries=3,
            app_dir=APP_DIR,
        )
        record_audio_extraction_openddf(
            task_id,
            _extract_result,
            app_dir=APP_DIR,
            project_uuid=_project_uuid,
        )
        audio_path = _extract_result.output_path
        if not _extract_result.success:
            _diag_hint = f" /api/auto_dub/diagnostics/{task_id}/zip"
            _user_msg = user_friendly_extract_error(_extract_result)
            _err_code = error_code_for_result(_extract_result)
            _launch_trace_stage(
                task,
                "Audio Extracted",
                status="FAILED",
                reason=f"extract_error:{_err_code or 'unknown'}",
                line=7043,
                data={"error": str(_extract_result.error or _user_msg)[:200]},
            )
            return _fail(
                task_id,
                [
                    f"{_user_msg}. Скачайте диагностику (ZIP){_diag_hint}",
                ],
                stage=STAGE_AUDIO_EXTRACTION,
                exc=RuntimeError(_extract_result.error or _user_msg),
                error_code=_err_code,
            )
        _launch_trace_stage(
            task,
            "Audio Extracted",
            status="SUCCESS",
            reason="ffmpeg_ok",
            line=7043,
            data={"audio_path": str(audio_path)},
        )

        if not target_duration_ms:
            vid_dur = _video_duration_ms(video_path)
            if vid_dur:
                target_duration_ms = vid_dur
                with STATE_LOCK:
                    task["info"]["target_duration_ms"] = target_duration_ms

        if keep_original_track:
            projects_dir = APP_DIR / "projects"
            projects_dir.mkdir(parents=True, exist_ok=True)
            stem = Path(video_path).stem
            preserved = projects_dir / f"{stem}_{base_id}_original.mp3"
            try:
                shutil.copy2(audio_path, preserved)
                with STATE_LOCK:
                    task["info"]["original_audio_path"] = str(preserved)
                logger.info("Task %s: original audio preserved at %s", task_id, preserved)
            except Exception as copy_err:
                logger.warning("Task %s: could not preserve original audio: %s", task_id, copy_err)

        profiler.stop("ffmpeg")
        if pipeline_timer is not None:
            pipeline_timer.stop("extract")
        _runtime_stage_record(task_id, runtime_diag, 1, STAGE_AUDIO_EXTRACTION)

        # ══ Source separation (dialogue vs music+SFX) — before STT ═══════════
        stt_audio_path = audio_path
        try:
            from engines.source_separation import try_separate_audio

            runtime_diag.stage_begin(STAGE_SOURCE_SEPARATION)
            sep_result = try_separate_audio(
                video_path=video_path,
                mono_audio_path=audio_path,
                artifacts_dir=_artifacts_dir(task.get("info")),
                base_id=base_id,
                task_id=task_id,
                task_info=task.get("info") or {},
            )
            with STATE_LOCK:
                task["info"]["source_separation"] = sep_result.to_dict()
            if sep_result.dialogue_stt_path and Path(sep_result.dialogue_stt_path).is_file():
                stt_audio_path = sep_result.dialogue_stt_path
            # Always surface the outcome — music in the final mix depends on it.
            if sep_result.success:
                logger.info(
                    "Task %s: source separation SUCCESS method=%s accompaniment=%s "
                    "(music/ambient WILL be mixed into final dub)",
                    task_id,
                    sep_result.method,
                    sep_result.accompaniment_path,
                )
            else:
                import shutil as _sh

                hint = ""
                if not _sh.which("demucs"):
                    hint = (
                        " — demucs not installed; install it for proper music/vocal "
                        "separation (pip install demucs)"
                    )
                logger.warning(
                    "Task %s: source separation FALLBACK reason=%s%s — final dub may "
                    "lack original music/ambient. %s",
                    task_id,
                    sep_result.error or "unknown",
                    hint,
                    sep_result.warning or "",
                )
            _runtime_stage_record(task_id, runtime_diag, 2, STAGE_SOURCE_SEPARATION)
        except Exception as sep_err:
            logger.warning(
                "Task %s: source separation hook failed, using legacy path: %s",
                task_id,
                sep_err,
            )
            with STATE_LOCK:
                task["info"]["source_separation"] = {
                    "enabled": True,
                    "attempted": True,
                    "success": False,
                    "fallback_used": True,
                    "warning": "Source separation hook error; legacy path used.",
                    "error": str(sep_err),
                }

        # ══ ШАГ 2: STT (Whisper) ══════════════════════════════════════════
        if not _ensure_control(task_id, ui_lang):
            _launch_trace_stage(
                task,
                "STT Started",
                status="SKIPPED",
                reason="control_cancelled_before_stt",
                line=7132,
            )
            return

        runtime_diag.stage_begin(STAGE_STT)
        _launch_trace_stage(
            task,
            "STT Started",
            status="SUCCESS",
            reason="stage_begin",
            line=7135,
        )

        with STATE_LOCK:
            preload = copy.deepcopy(task["info"].get("preload") or {})

        pre_source = preload.get("source_segments") or []
        pre_timing = preload.get("timing_map") or []
        stt_word_timestamps = False
        try:
            from engines.core.feature_flags import is_enabled as _ff_enabled

            stt_word_timestamps = _ff_enabled("word_timing", developer_session=True)
        except Exception as _wt_flag_exc:
            # Explicit degradation: word_timing flag lookup failed.
            # Silent fallback would hide the branch that ultimately fed
            # the STT_LAUNCH root-cause (UnboundLocalError below).
            stt_word_timestamps = False
            _launch_trace_stage(
                task,
                "STT Started",
                status="SKIPPED",
                reason=(
                    f"word_timing_flag_lookup_failed:"
                    f"{type(_wt_flag_exc).__name__}"
                ),
                line=7156,
                data={"error": str(_wt_flag_exc)[:200]},
            )

        if pre_source:
            source_text = "\n".join(str(s).strip() for s in pre_source if str(s).strip())
            timing_map = copy.deepcopy(pre_timing)
            detected_lang = source_lang or "auto"
            logger.info(
                "Task %s: using preloaded subtitles (%d segments)",
                task_id,
                len(pre_source),
            )
        else:
            from engines.pipeline_cache import load_whisper_cache, save_whisper_cache

            # Stage 8: Simple/Happy Path — cap model at small, beam=1, no word TS.
            _stt_beam = None
            _stt_vad = True
            _stt_device = ""
            _stt_compute = ""
            try:
                from engines.simple_stt_policy import (
                    apply_simple_stt_policy,
                    should_force_simple_stt,
                )

                with STATE_LOCK:
                    _info_stt = dict(task.get("info") or {})
                if should_force_simple_stt(_info_stt):
                    apply_simple_stt_policy(_info_stt, requested_model=model_size)
                    model_size = str(_info_stt.get("stt_model") or model_size)
                    _stt_beam = int(_info_stt.get("stt_beam_size") or 1)
                    stt_word_timestamps = False
                    _stt_device = str(_info_stt.get("stt_device") or "")
                    _stt_compute = str(_info_stt.get("stt_compute_type") or "")
                    with STATE_LOCK:
                        task["info"].update(
                            {
                                k: _info_stt[k]
                                for k in (
                                    "model_size",
                                    "stt_model",
                                    "stt_engine",
                                    "stt_beam_size",
                                    "stt_vad_filter",
                                    "stt_word_timestamps",
                                    "stt_device",
                                    "stt_compute_type",
                                    "stt_best_of",
                                    "simple_stt_locked",
                                    "voice_verification_asr_allowed",
                                    "post_tts_restt_allowed",
                                )
                                if k in _info_stt
                            }
                        )
                    logger.info(
                        "Task %s: Simple STT lock model=%s beam=%s device=%s/%s",
                        task_id,
                        model_size,
                        _stt_beam,
                        _stt_device,
                        _stt_compute,
                    )
            except Exception as _s8_pol_exc:
                logger.debug("Simple STT policy skipped: %s", _s8_pol_exc)

            if not _stt_device:
                try:
                    from engines.hardware_probe import probe_whisper_device

                    _stt_device, _stt_compute = probe_whisper_device()
                except Exception:
                    _stt_device, _stt_compute = "cpu", "int8"

            _set_step(task_id, "transcribe", 15.0)
            _update_progress_detail(
                task_id,
                phase="transcribe",
                live_message=f"Распознавание речи ({model_size} / {_stt_device})…",
                stt_model=model_size,
                stt_device=_stt_device,
            )
            profiler.start("whisper")
            if pipeline_timer is not None:
                pipeline_timer.start("whisper")
            _stt_t0 = time.perf_counter()
            wh_hit = load_whisper_cache(
                APP_DIR,
                video_path,
                model_size=model_size,
                source_lang=source_lang,
                beam_size=_stt_beam,
                compute_type=_stt_compute,
                device=_stt_device,
            )
            _stt_cache_hit = bool(wh_hit)
            if wh_hit:
                source_text = str(wh_hit.get("source_text") or "")
                timing_map = copy.deepcopy(wh_hit.get("timing_map") or [])
                detected_lang = str(wh_hit.get("detected_lang") or source_lang or "en")
                profiler.set_meta(whisper_cache="hit")
            else:
                from engines.stt_engine import get_last_stt_meta, transcribe

                with _blocking_progress_heartbeat(
                    task_id,
                    "transcribe",
                    interval=20.0,
                    messages=[
                        f"Загрузка Whisper {model_size} ({_stt_device})…",
                        "Распознавание речи…",
                        "Whisper обрабатывает аудио…",
                    ],
                ):
                    _tr_kwargs = {
                        "language": source_lang,
                        "model_size": model_size,
                        "word_timestamps": stt_word_timestamps,
                    }
                    if _stt_beam is not None:
                        _tr_kwargs["beam_size"] = _stt_beam
                    source_text, _, timing_map, detected_lang = transcribe(
                        stt_audio_path,
                        **_tr_kwargs,
                    )
                # Sparse STT (1 short island) — retry with forced CJK lang if needed
                try:
                    from engines.pipeline_cache import _timing_coverage_ok

                    _cjk_n = sum(
                        1
                        for ch in str(source_text or "")
                        if "\u4e00" <= ch <= "\u9fff"
                    )
                    if not _timing_coverage_ok(timing_map or []) and (
                        not source_lang or _cjk_n >= 12
                    ):
                        _force = (source_lang or detected_lang or "zh").split("-")[0]
                        if _cjk_n >= 12:
                            _force = "zh"
                        logger.warning(
                            "Task %s: sparse STT (%d slots) — retry language=%s",
                            task_id,
                            len(timing_map or []),
                            _force,
                        )
                        source_text, _, timing_map, detected_lang = transcribe(
                            stt_audio_path,
                            language=_force,
                            model_size=model_size,
                            word_timestamps=stt_word_timestamps,
                            beam_size=_stt_beam,
                        )
                except Exception as _stt_retry_exc:
                    logger.debug("STT coverage retry skipped: %s", _stt_retry_exc)
                try:
                    _meta_fw = get_last_stt_meta()
                    if _meta_fw.get("stt_device"):
                        _stt_device = str(_meta_fw.get("stt_device"))
                    if _meta_fw.get("stt_compute_type"):
                        _stt_compute = str(_meta_fw.get("stt_compute_type"))
                    if _meta_fw.get("stt_beam_size") is not None:
                        _stt_beam = int(_meta_fw.get("stt_beam_size"))
                    if _meta_fw.get("stt_model"):
                        model_size = str(_meta_fw.get("stt_model"))
                except Exception:
                    pass
                save_whisper_cache(
                    APP_DIR,
                    video_path,
                    model_size=model_size,
                    source_lang=source_lang,
                    source_text=source_text,
                    timing_map=timing_map,
                    detected_lang=detected_lang,
                    beam_size=_stt_beam,
                    compute_type=_stt_compute,
                    device=_stt_device,
                )
                profiler.set_meta(whisper_cache="miss")
            _stt_wall = round(time.perf_counter() - _stt_t0, 3)
            profiler.stop("whisper")
            if pipeline_timer is not None:
                pipeline_timer.stop("whisper")
                try:
                    pipeline_timer.set_meta(
                        stt_wall_sec=_stt_wall,
                        stt_model=model_size,
                        stt_device=_stt_device,
                        stt_compute_type=_stt_compute,
                        stt_beam_size=_stt_beam if _stt_beam is not None else "",
                        stt_cache_hit=_stt_cache_hit,
                    )
                except Exception:
                    pass

            _stt_stats = {
                "stt_wall_sec": _stt_wall,
                "stt_model": model_size,
                "stt_device": _stt_device,
                "stt_compute_type": _stt_compute,
                "stt_beam_size": int(_stt_beam) if _stt_beam is not None else (
                    1 if model_size in ("tiny", "base", "small") else 5
                ),
                "stt_vad_filter": True,
                "stt_engine": "faster-whisper",
                "stt_segments_raw": len(timing_map or []),
                "stt_cache_hit": _stt_cache_hit,
                "stt_word_timestamps": bool(stt_word_timestamps),
            }
            with STATE_LOCK:
                info_stt = task.setdefault("info", {})
                info_stt.update(_stt_stats)
                info_stt["model_size"] = model_size
                info_stt["stt_speedup"] = dict(_stt_stats)
            try:
                import json as _json_s8

                (APP_DIR / "output" / f"stt_speedup_{task_id}.json").write_text(
                    _json_s8.dumps(
                        {"task_id": task_id, **_stt_stats},
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            except Exception:
                pass
            logger.info(
                "Task %s: STT wall=%.2fs model=%s device=%s/%s beam=%s cache=%s segs=%d",
                task_id,
                _stt_wall,
                model_size,
                _stt_device,
                _stt_compute,
                _stt_stats["stt_beam_size"],
                "hit" if _stt_cache_hit else "miss",
                len(timing_map or []),
            )

            # Spec v3: PyAnnote diarization + per-speaker reference clips.
            # Safe no-op unless spec_v3 / VM_DIARIZE opted in and pyannote is installed.
            try:
                from engines.diarization import (
                    assign_speakers_to_segments,
                    build_speaker_profiles,
                    is_diarization_enabled,
                    run_diarization,
                )

                _info_dia = task.get("info") or {}
                if is_diarization_enabled(_info_dia):
                    _dia_audio = (
                        stt_audio_path
                        if Path(stt_audio_path).is_file()
                        else audio_path
                    )
                    dia_res = run_diarization(_dia_audio, task_info=_info_dia)
                    if timing_map:
                        assign_speakers_to_segments(timing_map, dia_res)
                    _profiles_dir = (
                        Path(_info_dia.get("session_dir") or APP_DIR / "output")
                        / f"speaker_profiles_{task_id}"
                    )
                    _profiles = (
                        build_speaker_profiles(_dia_audio, dia_res, str(_profiles_dir))
                        if dia_res.success
                        else {}
                    )
                    with STATE_LOCK:
                        task["info"]["diarization"] = dia_res.to_dict()
                        task["info"]["speaker_profiles"] = _profiles
                        task["info"]["speakers"] = list(dia_res.speakers or [])
                    logger.info(
                        "Task %s: diarization method=%s speakers=%s profiles=%s",
                        task_id,
                        dia_res.method,
                        len(dia_res.speakers or []),
                        sum(1 for p in _profiles.values() if p.get("ok")),
                    )
            except Exception as _dia_exc:
                logger.debug("Task %s: diarization skipped: %s", task_id, _dia_exc)

            logger.debug(
                "STT completed lang=%s chars=%s",
                detected_lang,
                len(source_text) if isinstance(source_text, str) else 0,
            )
            # Leave "transcribe"/15% immediately — SV3/normalizer can take minutes
            # and must not look like a Whisper hang.
            _set_step(task_id, "segment_prep", 22.0)
            _update_progress_detail(
                task_id,
                phase="segment_prep",
                live_message="Whisper finished — preparing segments",
                substep="stt_done",
                stt_wall_sec=_stt_wall,
                stt_model=model_size,
                stt_device=_stt_device,
            )
        _open_ddf.record_agent(
            task_id, "Whisper/STT", called=True,
            success=bool(source_text and source_text.strip()),
            decision=f"detected_lang={detected_lang}",
        )

        if not source_text.strip():
            from engines.dubbing_engine.pipeline_failure_diag import STAGE_STT

            # No silent fall-through: emit an explicit trace record so the
            # UI never sees a Meaning Pipeline that "just didn't run".
            _launch_trace_stage(
                task,
                "STT Started",
                status="FAILED",
                reason="stt_empty_no_source_text",
                line=7232,
                data={"detected_lang": detected_lang},
            )
            _launch_trace_stage(
                task,
                "Words Built",
                status="SKIPPED",
                reason="upstream_stt_empty",
                line=7232,
            )
            _launch_trace_stage(
                task,
                "Meaning Pipeline",
                status="SKIPPED",
                reason="upstream_stt_empty",
                line=7232,
            )
            return _fail(task_id, [lp["empty_stt"]], stage=STAGE_STT, error_code="STT_EMPTY")

        _runtime_stage_record(task_id, runtime_diag, 2, STAGE_STT)

        raw_lines = [
            line.strip()
            for line in source_text.splitlines()
            if line.strip()
        ] or [str(s).strip() for s in pre_source if str(s).strip()]
        # zh drama Whisper homophones (单纯→单传, 绑费→绑匪) — fix before MT
        try:
            from engines.mt.zh_asr_correct import (
                correct_zh_asr_segments,
                correct_zh_asr_timing_map,
            )

            _asr_lang = str(detected_lang or source_lang or "")
            raw_lines = correct_zh_asr_segments(raw_lines, language=_asr_lang)
            timing_map = correct_zh_asr_timing_map(timing_map, language=_asr_lang)
            if raw_lines:
                source_text = "\n".join(raw_lines)
        except Exception as _asr_fix_exc:
            logger.debug("zh ASR correct skipped: %s", _asr_fix_exc)
        raw_count = len(raw_lines)
        raw_timing_backup = copy.deepcopy(timing_map)

        text_source = (
            "preloaded_subtitles" if pre_source else "whisper_stt"
        )

        ocr_result: dict = {"enabled": False, "segments": []}
        if ocr_enabled:
            from engines.ocr_engine import extract_video_text

            ocr_result = extract_video_text(
                video_path, enabled=True, sample_interval_sec=2.0
            )
            dev_diag.log_ocr(
                text_source=text_source,
                note=f"ocr_enabled=true lines={len(ocr_result.get('segments') or [])} "
                f"(NOT merged into speech pipeline)",
            )
        else:
            dev_diag.log_ocr(
                text_source=text_source,
                note="ocr_enabled=false — speech dub uses Whisper only",
            )

        profiler.start("segmentation")
        # Adaptive mode uses pause-based merge as Whisper starting point, then
        # Adaptive Segmentation 2.0 reshapes for dubbing (separate stage).
        # Happy Path (TZ Stage 2): glue to ≥5.0s, pause < 0.9s — mandatory.
        _hp_seg = False
        try:
            from engines.happy_path import skip_advanced_text_shorteners as _hp_skip

            with STATE_LOCK:
                _hp_seg = bool(_hp_skip(dict(task.get("info") or {})))
        except Exception:
            _hp_seg = True
        if segmentation_mode == "sentence" and not _hp_seg:
            from engines.segment_merger import merge_stt_by_sentences

            merged_lines, merged_timing = merge_stt_by_sentences(
                raw_lines, raw_timing_backup
            )
        elif _hp_seg:
            from engines.segment_merger import merge_stt_segments_happy_path

            # Stage 29 §D — UK Simple ~4/7/12s glue floor from policy stamp.
            _merge_kw: dict = {}
            try:
                _info_glue = dict(task.get("info") or {})
                _glue_min = int(_info_glue.get("segment_min_ms") or 0)
                if _glue_min > 0:
                    _merge_kw["min_safe_ms"] = _glue_min
                _glue_max = int(_info_glue.get("segment_max_ms") or 0)
                if _glue_max > 0:
                    _merge_kw["max_span_ms"] = _glue_max
            except Exception:
                pass
            merged_lines, merged_timing = merge_stt_segments_happy_path(
                raw_lines, timing_map or raw_timing_backup, **_merge_kw
            )
            _before_n = len(raw_lines)
            _after_n = len(merged_lines or [])
            with STATE_LOCK:
                task["info"]["stt_merge_mode"] = "happy_path"
                task["info"]["stt_merge_before"] = _before_n
                task["info"]["stt_merge_after"] = _after_n
                task["info"]["stt_segments_after_glue"] = _after_n
                # TZ Stage 2 report aliases
                task["info"]["segments_before"] = _before_n
                task["info"]["segments_after"] = _after_n
                _speed = task["info"].get("stt_speedup")
                if isinstance(_speed, dict):
                    _speed = dict(_speed)
                    _speed["stt_segments_after_glue"] = _after_n
                    task["info"]["stt_speedup"] = _speed
            logger.info(
                "Task %s: Happy Path STT merge segments_before=%d → "
                "segments_after=%d (min≥5.0s, gap<0.9s)",
                task_id,
                _before_n,
                _after_n,
            )
            try:
                import json as _json_glue

                _glue_path = APP_DIR / "output" / f"stt_speedup_{task_id}.json"
                if _glue_path.is_file():
                    _gj = _json_glue.loads(_glue_path.read_text(encoding="utf-8"))
                    _gj["stt_segments_after_glue"] = _after_n
                    _glue_path.write_text(
                        _json_glue.dumps(_gj, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
            except Exception:
                pass
        else:
            from engines.segment_merger import merge_stt_segments

            merged_lines, merged_timing = merge_stt_segments(raw_lines, timing_map)
        # CJK: rescue single overlong Whisper blob (drama monologue in one island)
        try:
            from engines.segment_merger import split_overlong_cjk_segments

            _vid_ms = int(target_duration_ms or _video_duration_ms(video_path) or 0)
            _split_src = merged_lines if merged_lines else raw_lines
            _split_tm = (
                merged_timing
                if merged_lines and merged_timing
                else (timing_map or raw_timing_backup)
            )
            _split_lines, _split_timing = split_overlong_cjk_segments(
                _split_src,
                _split_tm,
                video_duration_ms=_vid_ms,
            )
            if _split_lines and len(_split_lines) > len(_split_src):
                merged_lines, merged_timing = _split_lines, _split_timing
                logger.info(
                    "Task %s: CJK overlong STT split %d -> %d segments",
                    task_id,
                    len(_split_src),
                    len(_split_lines),
                )
        except Exception as _split_exc:
            logger.debug("CJK overlong split skipped: %s", _split_exc)
        seg_lines = merged_lines if merged_lines else raw_lines
        seg_timing = merged_timing if merged_lines and merged_timing else raw_timing_backup
        if not seg_timing and seg_lines:
            from engines.segment_merger import ensure_timing_map_for_segments

            seg_timing = ensure_timing_map_for_segments(
                seg_lines,
                seg_timing,
                duration_ms=target_duration_ms or _video_duration_ms(video_path),
            )
            logger.warning(
                "Task %s: rebuilt timing_map for %d segments (STT had no timing slots)",
                task_id,
                len(seg_lines),
            )
        if seg_lines:
            source_text = "\n".join(seg_lines)
            timing_map = seg_timing
            # Keep source/timing lengths locked after Happy Path glue.
            try:
                from engines.segment_merger import ensure_timing_map_for_segments

                timing_map = ensure_timing_map_for_segments(
                    seg_lines,
                    timing_map,
                    duration_ms=target_duration_ms or _video_duration_ms(video_path),
                )
                seg_timing = timing_map
            except Exception:
                pass
            with STATE_LOCK:
                if _hp_seg:
                    task["info"]["segments_before"] = task["info"].get(
                        "segments_before", raw_count
                    )
                    task["info"]["segments_after"] = len(seg_lines)
                    task["info"]["stt_merge_after"] = len(seg_lines)

        profiler.stop("segmentation")

        if not seg_lines:
            from engines.semantic_v3.launch_decision_trace import fail_stt_zero_segments

            with STATE_LOCK:
                _info = task.get("info")
            fail_stt_zero_segments(
                task_info=_info,
                raw_count=raw_count,
                line=7444,
            )

        word_timing_enabled = stt_word_timestamps

        if word_timing_enabled:
            from engines.word_timing import (
                build_from_whisper,
                build_merged_maps,
                persist_to_task_info,
                sync_timing_map,
            )
            from engines.core.events import get_event_bus

            raw_word_maps = build_from_whisper(raw_lines, raw_timing_backup)
            seg_lines_wtm = merged_lines if merged_lines else raw_lines
            seg_timing_wtm = (
                merged_timing
                if merged_lines and merged_timing
                else (seg_timing if seg_lines else raw_timing_backup)
            )
            merged_word_maps = build_merged_maps(
                raw_lines,
                raw_timing_backup,
                seg_lines_wtm,
                seg_timing_wtm,
                raw_maps=raw_word_maps,
            )
            if merged_word_maps:
                timing_map = sync_timing_map(seg_timing_wtm, merged_word_maps)
            get_event_bus().emit(
                "word_timing",
                {"segments": len(merged_word_maps), "task_id": task_id},
            )
        else:
            merged_word_maps = []

        # ── Semantic V3 Phase 2 (Meaning First): Whisper = ASR only; Speech/Audio Units ──
        try:
            from engines.semantic_v3 import (
                phase2_to_orchestrator_arrays,
                run_semantic_v3_phase2,
                semantic_v3_enabled,
                semantic_v3_native_te_enabled,
            )

            _semantic_v3_on = semantic_v3_enabled()
            if _semantic_v3_on and seg_lines:
                # Prefer word timestamps on Semantic V3 path
                if not word_timing_enabled:
                    try:
                        from engines.word_timing import (
                            build_from_whisper,
                            build_merged_maps,
                            sync_timing_map,
                        )

                        raw_word_maps = build_from_whisper(raw_lines, raw_timing_backup)
                        merged_word_maps = build_merged_maps(
                            raw_lines,
                            raw_timing_backup,
                            seg_lines,
                            timing_map,
                            raw_maps=raw_word_maps,
                        )
                        if merged_word_maps:
                            timing_map = sync_timing_map(timing_map, merged_word_maps)
                        word_timing_enabled = True
                        _launch_trace_stage(
                            task,
                            "Words Built",
                            status="SUCCESS",
                            reason="sv3_forced_wtm_ok",
                            line=7392,
                            data={
                                "segments": len(merged_word_maps or []),
                                "path": "sv3_forced_wtm",
                            },
                        )
                    except Exception as _wtm_exc:
                        logger.warning(
                            "Task %s: SemanticV3 forced WTM failed: %s",
                            task_id,
                            _wtm_exc,
                        )
                        _launch_trace_stage(
                            task,
                            "Words Built",
                            status="FAILED",
                            reason=(
                                f"sv3_forced_wtm_exception:"
                                f"{type(_wtm_exc).__name__}"
                            ),
                            line=7401,
                            data={"error": str(_wtm_exc)[:200]},
                        )
                _native_te = semantic_v3_native_te_enabled()
                # Hard wall: Phase2 must not freeze the pipeline/UI at 15–22%.
                # On timeout we fall through to legacy translation (proven path).
                _sv3_timeout = float(
                    os.environ.get("VM_SEMANTIC_V3_TIMEOUT_SEC", "120") or "120"
                )
                _sv3 = None
                with _blocking_progress_heartbeat(
                    task_id,
                    "segment_prep",
                    interval=15.0,
                    messages=[
                        "Semantic V3: смысл и сегменты…",
                        "Semantic V3: ещё работает…",
                        "Semantic V3: почти готово…",
                    ],
                ):
                    from concurrent.futures import ThreadPoolExecutor
                    from concurrent.futures import TimeoutError as FuturesTimeout

                    with ThreadPoolExecutor(max_workers=1) as _sv3_pool:
                        _sv3_fut = _sv3_pool.submit(
                            run_semantic_v3_phase2,
                            list(seg_lines),
                            list(timing_map),
                            word_maps=merged_word_maps or None,
                            src_lang=str(detected_lang or "en"),
                            tgt_lang=str(target_lang or "uk"),
                            voice=str(voice or "default"),
                            translate=_native_te,
                            app_dir=APP_DIR,
                        )
                        try:
                            _sv3 = _sv3_fut.result(timeout=max(30.0, _sv3_timeout))
                        except FuturesTimeout:
                            logger.warning(
                                "Task %s: SemanticV3 Phase2 timed out after %.0fs — "
                                "continuing legacy path",
                                task_id,
                                _sv3_timeout,
                            )
                            _launch_trace_stage(
                                task,
                                "Meaning Pipeline",
                                status="FAILED",
                                reason=f"sv3_timeout_{int(_sv3_timeout)}s",
                                line=7925,
                                data={"timeout_sec": _sv3_timeout},
                            )
                            _sv3 = None
                if _sv3 is not None:
                    _sources, _new_tm, _translated = phase2_to_orchestrator_arrays(_sv3)
                    if _sources and _new_tm and len(_sources) == len(_new_tm):
                        _sent_rows = [s.to_dict() for s in _sv3.sentences]
                        with STATE_LOCK:
                            task["info"]["asr_whisper_archive"] = list(_sv3.asr_archive)
                            task["info"]["semantic_v3"] = {
                                "enabled": True,
                                "phase2": True,
                                "bridge": False,
                                "project_uuid": _sv3.project_uuid,
                                "unit_type": _sv3.unit_type,
                                "phase": _sv3.phase,
                                "meta": _sv3.meta,
                                "sentences": _sent_rows,
                                "native_te": _native_te,
                            }
                            # P31: native TE owns translation — skip legacy array TE
                            if (
                                _native_te
                                and _translated
                                and len(_translated) == len(_sources)
                            ):
                                task["info"]["translated_segments"] = list(_translated)
                                task["info"]["skip_translate"] = True
                                task["info"]["pre_translated"] = list(_sources)
                        logger.info(
                            "Task %s: SemanticV3 Phase2 whisper=%d → speech_units=%d "
                            "native_te=%s bridge=False",
                            task_id,
                            len(seg_lines),
                            len(_sources),
                            _native_te,
                        )
                        seg_lines = _sources
                        timing_map = _new_tm
                        source_text = "\n".join(seg_lines)
                        _launch_trace_stage(
                            task,
                            "Meaning Pipeline",
                            status="SUCCESS",
                            reason="sv3_phase2_bridge_ok",
                            line=7462,
                            data={
                                "sources": len(_sources),
                                "sentences": len(_sent_rows),
                                "native_te": bool(_native_te),
                            },
                        )
                        _launch_trace_agent(
                            task,
                            "semantic",
                            called=True,
                            called_by="engines/semantic_v3/phase2.py:run_semantic_v3_phase2",
                            line=7462,
                            data={"sentences": len(_sent_rows)},
                        )
                        if _native_te and _translated and len(_translated) == len(_sources):
                            _launch_trace_agent(
                                task,
                                "translation",
                                called=True,
                                called_by=(
                                    "engines/semantic_v3/native_translate.py:"
                                    "translate_sentences_native"
                                ),
                                line=7462,
                                data={"translated": len(_translated)},
                            )
            elif not _semantic_v3_on:
                _launch_trace_stage(
                    task,
                    "Meaning Pipeline",
                    status="SKIPPED",
                    reason="semantic_v3_disabled_by_flag",
                    line=7462,
                    data={"flag_value": False},
                )
            elif not seg_lines:
                _launch_trace_stage(
                    task,
                    "Meaning Pipeline",
                    status="SKIPPED",
                    reason="no_source_segments_from_stt",
                    line=7462,
                    data={"raw_lines": len(raw_lines)},
                )
        except Exception as _sv3_exc:
            logger.warning("Task %s: SemanticV3 skipped: %s", task_id, _sv3_exc)
            _launch_trace_stage(
                task,
                "Meaning Pipeline",
                status="FAILED",
                reason=f"sv3_exception:{type(_sv3_exc).__name__}",
                line=7476,
                data={"error": str(_sv3_exc)[:200]},
            )

        # ── Segment Normalizer (PSA4): micro/fragment/mid-name merge ──
        try:
            from engines.pipeline_integrity.segment_normalizer import normalize_segments
            from engines.pipeline_integrity.v2_gates import segment_normalizer_enabled

            if segment_normalizer_enabled() and seg_lines:
                _norm_texts, _norm_tm, _norm_rep = normalize_segments(
                    list(seg_lines),
                    list(timing_map or []),
                    min_ms=850,
                    run_smart_split=False,
                )
                _micro = _norm_rep.get("micro") or _norm_rep
                _merged_n = int(
                    _micro.get("merged", 0) or 0
                ) + int(_micro.get("continuation_merged", 0) or 0)
                if _norm_texts and (
                    _merged_n > 0
                    or _norm_rep.get("boundaries_changed")
                    or len(_norm_texts) != len(seg_lines)
                ):
                    seg_lines = list(_norm_texts)
                    timing_map = list(_norm_tm)
                with STATE_LOCK:
                    task["info"]["segment_normalizer"] = _norm_rep
                logger.info(
                    "Task %s: SegmentNormalizer micro-merge %s→%s (merged=%s)",
                    task_id,
                    _micro.get("before"),
                    _micro.get("after") or len(_norm_texts),
                    _micro.get("merged"),
                )
        except Exception as _norm_exc:
            logger.warning(
                "Task %s: SegmentNormalizer skipped: %s", task_id, _norm_exc
            )

        # ── Adaptive Segmentation 2.0 (post-Whisper / post-merge, pre-MT) ──
        _adaptive_report: dict = {}
        try:
            _adaptive_cfg_ov = None
            with STATE_LOCK:
                _adaptive_cfg_ov = (task.get("info") or {}).get(
                    "adaptive_segmentation_settings"
                )
            # Settings.enabled=False is hard opt-out (TZ §13).
            _settings_off = (
                isinstance(_adaptive_cfg_ov, dict)
                and _adaptive_cfg_ov.get("enabled") is False
            )
            _run_adaptive = False
            if not _settings_off:
                if segmentation_mode == "adaptive":
                    _run_adaptive = True
                else:
                    try:
                        from engines.core.feature_flags import (
                            is_enabled as _ff_adaptive,
                        )

                        _run_adaptive = bool(_ff_adaptive("adaptive_segmentation"))
                    except Exception:
                        _run_adaptive = False
            # Happy Path TZ Stage 2: do NOT reshape after mandatory ≥5s glue —
            # Adaptive Seg can reintroduce micro-slots and defeat batch MT.
            if _hp_seg and _run_adaptive:
                _run_adaptive = False
                with STATE_LOCK:
                    task["info"]["adaptive_segmentation_skipped"] = "happy_path"
                logger.info(
                    "Task %s: Adaptive Segmentation skipped (happy_path — "
                    "keep STT glue blocks)",
                    task_id,
                )
            if _run_adaptive and seg_lines:
                from engines.adaptive_segmentation import adapt_source_segments

                _as_overrides = (
                    dict(_adaptive_cfg_ov)
                    if isinstance(_adaptive_cfg_ov, dict)
                    else {}
                )
                # Happy Path: keep Adaptive Seg but enforce TZ Stage 2 floors.
                if _hp_seg:
                    _as_overrides.setdefault("min_ms", 5000)
                    _as_overrides.setdefault("soft_min_ms", 4500)
                    _as_overrides.setdefault("preferred_ms", 7000)
                    _as_overrides.setdefault("use_meaning", False)
                    _as_overrides.setdefault("use_tts_forecast", False)
                _as_result = adapt_source_segments(
                    list(seg_lines),
                    list(timing_map or []),
                    src_lang=source_lang or detected_lang or "en",
                    tgt_lang=target_lang or "uk",
                    overrides=_as_overrides or None,
                )
                _adaptive_report = dict(_as_result.report or {})
                if _as_result.changed and _as_result.segments:
                    seg_lines = list(_as_result.segments)
                    timing_map = list(_as_result.timing_map)
                    # Keep merger snapshots aligned for diagnostics / WTM rebuild
                    merged_lines = list(seg_lines)
                    merged_timing = list(timing_map)
                    # TZ §4: Adaptive runs before Translation — invalidate SV3
                    # native TE preload so affected sources are re-translated.
                    with STATE_LOCK:
                        if task["info"].get("skip_translate") or task["info"].get(
                            "translated_segments"
                        ):
                            task["info"].pop("skip_translate", None)
                            task["info"].pop("translated_segments", None)
                            task["info"].pop("pre_translated", None)
                            task["info"]["adaptive_invalidated_native_te"] = True
                            logger.info(
                                "Task %s: Adaptive Seg invalidated native TE "
                                "(re-translate after reshape)",
                                task_id,
                            )
                    if word_timing_enabled:
                        try:
                            from engines.word_timing import (
                                build_from_whisper as _bw,
                                build_merged_maps as _bm,
                                sync_timing_map as _stm,
                            )

                            _raw_wm = _bw(raw_lines, raw_timing_backup)
                            merged_word_maps = _bm(
                                raw_lines,
                                raw_timing_backup,
                                seg_lines,
                                timing_map,
                                raw_maps=_raw_wm,
                            )
                            if merged_word_maps:
                                timing_map = _stm(timing_map, merged_word_maps)
                        except Exception as _wtm_as_exc:
                            logger.debug(
                                "Task %s: WTM rebuild after AdaptiveSeg skipped: %s",
                                task_id,
                                _wtm_as_exc,
                            )
                    logger.info(
                        "Task %s: Adaptive Segmentation %d→%d (spread %s→%s)",
                        task_id,
                        _adaptive_report.get("before_count"),
                        _adaptive_report.get("after_count"),
                        (_adaptive_report.get("stats_before") or {}).get(
                            "spread_ratio"
                        ),
                        (_adaptive_report.get("stats_after") or {}).get(
                            "spread_ratio"
                        ),
                    )
                _launch_trace_stage(
                    task,
                    "Adaptive Segmentation",
                    status="SUCCESS" if _as_result.changed else "SKIPPED",
                    reason=(
                        "reshaped"
                        if _as_result.changed
                        else "no_change_or_disabled"
                    ),
                    module="engines/adaptive_segmentation/core.py",
                    line=1,
                    data={
                        "before": _adaptive_report.get("before_count"),
                        "after": _adaptive_report.get("after_count"),
                        "actions": _adaptive_report.get("actions"),
                    },
                )
        except Exception as _as_exc:
            logger.warning(
                "Task %s: Adaptive Segmentation skipped: %s", task_id, _as_exc
            )
            _launch_trace_stage(
                task,
                "Adaptive Segmentation",
                status="FAILED",
                reason=f"adaptive_seg_exception:{type(_as_exc).__name__}",
                module="engines/adaptive_segmentation/core.py",
                line=1,
                data={"error": str(_as_exc)[:200]},
            )

        # Re-merge discourse openers after Adaptive (may re-introduce «And at»).
        try:
            from engines.pipeline_integrity.segment_normalizer import merge_micro_slots
            from engines.pipeline_integrity.v2_gates import segment_normalizer_enabled

            if segment_normalizer_enabled() and seg_lines:
                _post_t, _post_tm, _post_rep = merge_micro_slots(
                    list(seg_lines), list(timing_map or []), min_ms=850
                )
                if _post_t and (
                    int(_post_rep.get("merged", 0) or 0) > 0
                    or len(_post_t) != len(seg_lines)
                ):
                    logger.info(
                        "Task %s: post-Adaptive micro-merge %s→%s",
                        task_id,
                        _post_rep.get("before"),
                        _post_rep.get("after"),
                    )
                    seg_lines = list(_post_t)
                    timing_map = list(_post_tm)
                    merged_lines = list(seg_lines)
                    merged_timing = list(timing_map)
        except Exception as _post_norm_exc:
            logger.debug(
                "Task %s: post-Adaptive micro-merge skipped: %s",
                task_id,
                _post_norm_exc,
            )

        dev_diag.log_segmentation(
            raw_segments=raw_lines,
            raw_timing=raw_timing_backup,
            merged_segments=merged_lines,
            merged_timing=merged_timing,
            source=text_source,
        )

        with STATE_LOCK:
            task["info"]["source_segments"] = list(seg_lines)
            task["info"]["detected_lang"] = detected_lang
            task["info"]["timing_map_backup"] = copy.deepcopy(timing_map)
            if merged_word_maps and word_timing_enabled:
                # STT_LAUNCH_ROOT_CAUSE: `persist_to_task_info` was only
                # imported inside the `if word_timing_enabled:` branch above.
                # When that branch was skipped (feature flag off / basic
                # mode) but the Semantic V3 forced-WTM branch flipped
                # `word_timing_enabled=True` and populated merged_word_maps,
                # the name resolved to UnboundLocalError, aborting the whole
                # STT stage before any segment was recorded (segment_count=0).
                # Local import keeps the symbol scoped and available on every
                # entry path with zero side effects.
                from engines.word_timing import (
                    persist_to_task_info as _persist_wtm_to_task_info,
                )

                _persist_wtm_to_task_info(
                    task["info"],
                    merged_word_maps,
                    timing_map=timing_map,
                )
                _launch_trace_stage(
                    task,
                    "Words Built",
                    status="SUCCESS",
                    reason="wtm_persisted",
                    module="api/auto_dub_api.py",
                    line=7410,
                    data={
                        "segments": len(merged_word_maps),
                        "word_timing_enabled": True,
                    },
                )
            else:
                _launch_trace_stage(
                    task,
                    "Words Built",
                    status="SKIPPED",
                    reason=(
                        "no_word_maps"
                        if not merged_word_maps
                        else "word_timing_disabled"
                    ),
                    module="api/auto_dub_api.py",
                    line=7410,
                    data={
                        "word_timing_enabled": bool(word_timing_enabled),
                        "merged_word_maps": len(merged_word_maps or []),
                    },
                )
            task["info"]["wtm_sync_mode"] = sync_mode()
            task["info"]["wtm_phase"] = current_phase_label()
            task["info"]["stt_segment_count_raw"] = raw_count
            task["info"]["stt_segment_count_merged"] = len(seg_lines)
            task["info"]["text_source"] = text_source
            task["info"]["segmentation_mode"] = segmentation_mode
            if _adaptive_report:
                task["info"]["adaptive_segmentation"] = _adaptive_report
            task["info"]["ocr_enabled"] = ocr_enabled
            task["info"]["ocr_result"] = ocr_result if ocr_enabled else None
            task["info"]["dev_diagnostics"] = dev_diag.paths()

        _wtm_record_checkpoint(wtm_cp_log, task_id, "post_merge")

        # ══ ШАГ 3: Перевод (batch + контекст через naturalizer) ═══════════════
        if not _ensure_control(task_id, ui_lang):
            return
        _set_step(task_id, "translate", 55.0)
        runtime_diag.stage_begin(STAGE_TRANSLATION)

        with STATE_LOCK:
            source_segments_snapshot = list(task["info"].get("source_segments", []))
            timing_map_for_translate = copy.deepcopy(
                task["info"].get("timing_map_backup", [])
            )
            pre_translated = preload.get("translated_segments") or []
            skip_translate_flag = bool(task["info"].get("skip_translate", skip_translate))

        translation_source_lang = source_lang or detected_lang or "en"
        translate_meta: list = []

        if (
            skip_translate_flag
            and pre_translated
            and len(pre_translated) == len(source_segments_snapshot)
        ):
            segments = [str(s).strip() for s in pre_translated]
            translate_method = "preloaded_skip_translate"
            from engines.translation_quality_log import synthesize_audits_from_segments

            pre_audits = synthesize_audits_from_segments(
                source_segments_snapshot,
                segments,
                translation_source_lang,
                target_lang,
                engine="preloaded",
            )
            with STATE_LOCK:
                task["info"]["translation_audits"] = [a.__dict__ for a in pre_audits]
            logger.info(
                "Task %s: using preloaded translation (%d segments)",
                task_id,
                len(segments),
            )
        else:
            from engines.pipeline_cache import load_translate_cache, save_translate_cache

            cached_tr = load_translate_cache(
                APP_DIR,
                source_segments_snapshot,
                translation_source_lang,
                target_lang,
            )
            if cached_tr and len(cached_tr) == len(source_segments_snapshot):
                segments = cached_tr
                try:
                    from engines.mt.glossary_en_uk import finalize_mt_text

                    segments = [
                        finalize_mt_text(translation_source_lang, target_lang, s)
                        for s in segments
                    ]
                except Exception:
                    pass
                _job_eng = (
                    "cache+glossary"
                    if str(translation_source_lang or "").lower() == "en"
                    and str(target_lang or "").lower() == "uk"
                    else "cache"
                )
                translate_method = _job_eng
                profiler.set_meta(translate_cache="hit")
                from engines.translation_quality_log import synthesize_audits_from_segments

                cached_audits = synthesize_audits_from_segments(
                    source_segments_snapshot,
                    segments,
                    translation_source_lang,
                    target_lang,
                    engine=_job_eng,
                )
                with STATE_LOCK:
                    task["info"]["translation_audits"] = [
                        {
                            **{k: v for k, v in a.__dict__.items()},
                        }
                        for a in cached_audits
                    ]
                    from engines.translation_trace import TranslationTraceLog

                    trace = TranslationTraceLog(APP_DIR, task_id=task_id)
                    for a in cached_audits:
                        trace.upsert_from_audit(a.__dict__)
                    trace.flush(
                        phase="post_cache",
                        extra={
                            "src": translation_source_lang,
                            "tgt": target_lang,
                            "pipeline": "cache_hit",
                        },
                    )
                    task["info"]["translation_trace_log"] = trace.path
                    # Stage 7 metrics + skip streaming text on Simple warm hit.
                    _mt_cache_stats = {
                        "mt_wall_sec": 0.0,
                        "mt_segments": len(segments),
                        "mt_batch_size": 0,
                        "mt_calls": 0,
                        "mt_engine": "job_cache",
                        "mt_cache_hits": len(segments),
                        "mt_cache_misses": 0,
                        "mt_concurrency_used": 1,
                        "mt_retries": 0,
                        "mt_path": "mt_cache",
                        "translate_method": "mt_cache",
                        "translation_agent_path": False,
                        "llm_adaptation_used": False,
                        "simple_mt_locked": True,
                    }
                    try:
                        from engines.simple_mt_path import (
                            build_simple_mt_ui_timing,
                            stamp_simple_mt_lock,
                            use_locked_simple_mt,
                        )

                        if use_locked_simple_mt(task["info"]):
                            stamp_simple_mt_lock(task["info"])
                            translate_method = "mt_cache"
                            _ui = build_simple_mt_ui_timing(
                                subphase="done",
                                wall_sec=0.0,
                                segments_done=len(segments),
                                segments_total=len(segments),
                                cache_mode=True,
                            )
                            task["info"]["translation_timing"] = _ui
                            task["info"]["translation_timing_breakdown"] = _ui
                            _mt_cache_stats["translation_timing"] = _ui
                    except Exception:
                        pass
                    task["info"]["mt_speedup"] = dict(_mt_cache_stats)
                    for _k, _v in _mt_cache_stats.items():
                        if _k != "translation_timing":
                            task["info"][_k] = _v
                    if task["info"].get("simple_pipeline") or task["info"].get(
                        "happy_path"
                    ):
                        task["info"]["tps_skip_orchestrator"] = True
                    if translate_meta is not None:
                        translate_meta.append(
                            {
                                "pipeline": "mt_cache",
                                "translation_sec": 0.0,
                                "marian_sec": 0.0,
                                "llm_adaptation_sec": 0.0,
                                "llm_adaptation_used": False,
                                "translation_agent": False,
                                **{
                                    k: v
                                    for k, v in _mt_cache_stats.items()
                                    if k != "translation_timing"
                                },
                            }
                        )
                try:
                    import json as _json_s7c

                    (APP_DIR / "output" / f"mt_speedup_{task_id}.json").write_text(
                        _json_s7c.dumps(
                            {"task_id": task_id, **_mt_cache_stats},
                            ensure_ascii=False,
                            indent=2,
                        ),
                        encoding="utf-8",
                    )
                except Exception:
                    pass
            else:
                with STATE_LOCK:
                    _manifest_path = (AUTO_TASKS.get(task_id, {}).get("info") or {}).get(
                        "manifest_path"
                    )
                    _info_mt_gate = dict(task.get("info") or {})
                from engines.simple_mt_path import (
                    run_locked_simple_mt,
                    stamp_simple_mt_lock,
                    use_locked_simple_mt,
                )

                _simple_mt_fast = use_locked_simple_mt(_info_mt_gate)
                _stage7_done = False
                # Stage 7b LOCK: Simple/Happy Path → ONLY Marian batch + cache.
                # Never Director / AI-Core translation agent / Qwen adaptation.
                if _simple_mt_fast:
                    try:
                        from engines.translation_quality_log import (
                            synthesize_audits_from_segments,
                        )

                        with STATE_LOCK:
                            stamp_simple_mt_lock(task.setdefault("info", {}))
                            task["info"]["translate_method"] = "marian_batch"

                        def _mt_prog(done: int, total: int, ui: dict) -> None:
                            frac = done / max(total, 1)
                            with STATE_LOCK:
                                t = AUTO_TASKS.get(task_id)
                                if t and t.get("status") == "running":
                                    t["progress"] = round(55.0 + frac * 8.0, 1)
                                    info_p = t.setdefault("info", {})
                                    info_p["translation_timing"] = dict(ui or {})
                                    info_p["llm_adaptation_used"] = False
                            cache_mode = bool(
                                (ui or {}).get("ui_labels", {}).get("marian_mt")
                                == "Кэш перевода"
                            )
                            _update_progress_detail(
                                task_id,
                                phase="translate",
                                segments_done=done,
                                total_segments=total,
                                operation="translation",
                                translation_subphase="marian_mt",
                                translation_timing=dict(ui or {}),
                                live_message=(
                                    "Перевод: кэш…"
                                    if cache_mode
                                    else "Перевод: Marian MT…"
                                ),
                            )

                        segments, _mt_stats = run_locked_simple_mt(
                            list(source_segments_snapshot),
                            translation_source_lang,
                            target_lang,
                            app_dir=APP_DIR,
                            on_progress=_mt_prog,
                        )
                        translate_method = str(
                            _mt_stats.get("translate_method") or "marian_batch"
                        )
                        _stage7_done = True
                        meta = {
                            "pipeline": translate_method,
                            "naturalizer_executed": bool(
                                _mt_stats.get("naturalizer_executed")
                            ),
                            "naturalizer_applied": bool(
                                _mt_stats.get("naturalizer_applied")
                            ),
                            "timing_aware_executed": False,
                            "timing_aware_applied": False,
                            "translation_agent": False,
                            "llm_adaptation_used": False,
                            "translation_sec": float(_mt_stats.get("mt_wall_sec") or 0),
                            "marian_sec": float(_mt_stats.get("mt_wall_sec") or 0),
                            "naturalizer_sec": 0.0,
                            "llm_adaptation_sec": 0.0,
                            "engines": [str(_mt_stats.get("mt_engine") or "marian")],
                            "groups": int(_mt_stats.get("mt_calls") or 0),
                            "translation_timing_breakdown": _mt_stats.get(
                                "translation_timing"
                            ),
                            **{
                                k: _mt_stats.get(k)
                                for k in (
                                    "mt_wall_sec",
                                    "mt_segments",
                                    "mt_batch_size",
                                    "mt_calls",
                                    "mt_engine",
                                    "mt_cache_hits",
                                    "mt_cache_misses",
                                    "mt_concurrency_used",
                                    "mt_retries",
                                    "mt_path",
                                    "translate_method",
                                    "translation_agent_path",
                                    "llm_adaptation_used",
                                    "simple_mt_locked",
                                )
                            },
                        }
                        if translate_meta is not None:
                            translate_meta.append(meta)
                        stage7_audits = synthesize_audits_from_segments(
                            source_segments_snapshot,
                            segments,
                            translation_source_lang,
                            target_lang,
                            engine=str(_mt_stats.get("mt_engine") or "marian_batch"),
                            engines=list(_mt_stats.get("mt_segment_engines") or []),
                        )
                        with STATE_LOCK:
                            info_s7 = task.setdefault("info", {})
                            stamp_simple_mt_lock(info_s7)
                            info_s7["translate_method"] = translate_method
                            info_s7["mt_path"] = translate_method
                            info_s7["translation_audits"] = [
                                {k: v for k, v in a.__dict__.items()}
                                for a in stage7_audits
                            ]
                            info_s7["mt_speedup"] = dict(_mt_stats)
                            for _k, _v in _mt_stats.items():
                                if _k == "translation_timing":
                                    info_s7["translation_timing"] = _v
                                    info_s7["translation_timing_breakdown"] = _v
                                else:
                                    info_s7[_k] = _v
                            info_s7["translation_agent_status"] = "skipped_simple_mt"
                            info_s7["tps_skip_orchestrator"] = True
                            _sd0 = list(info_s7.get("segments_data") or [])
                            if not _sd0 or len(_sd0) != len(segments):
                                _sd0 = [
                                    {
                                        "index": i,
                                        "text": str(segments[i] or ""),
                                        "translated_text": str(segments[i] or ""),
                                        "plain_text": str(segments[i] or ""),
                                        "original": str(
                                            source_segments_snapshot[i]
                                            if i < len(source_segments_snapshot)
                                            else ""
                                        ),
                                    }
                                    for i in range(len(segments))
                                ]
                            else:
                                for i, row in enumerate(_sd0):
                                    if not isinstance(row, dict):
                                        continue
                                    row["translated_text"] = str(segments[i] or "")
                                    row["text"] = str(segments[i] or "")
                                    row["plain_text"] = str(segments[i] or "")
                            info_s7["segments_data"] = _sd0
                        try:
                            import json as _json_s7

                            (
                                APP_DIR / "output" / f"mt_speedup_{task_id}.json"
                            ).write_text(
                                _json_s7.dumps(
                                    {
                                        "task_id": task_id,
                                        "translate_method": translate_method,
                                        **{
                                            k: _mt_stats.get(k)
                                            for k in _mt_stats
                                            if k != "translation_timing"
                                        },
                                    },
                                    ensure_ascii=False,
                                    indent=2,
                                ),
                                encoding="utf-8",
                            )
                        except Exception:
                            pass
                        save_translate_cache(
                            APP_DIR,
                            source_segments_snapshot,
                            translation_source_lang,
                            target_lang,
                            segments,
                            route_label=translate_method,
                            engine=str(_mt_stats.get("mt_engine") or "marian"),
                            quality_score=0.0,
                        )
                        profiler.set_meta(translate_cache=translate_method)
                        logger.info(
                            "Task %s: Simple MT LOCK method=%s wall=%.2fs engine=%s hits=%s",
                            task_id,
                            translate_method,
                            float(_mt_stats.get("mt_wall_sec") or 0),
                            _mt_stats.get("mt_engine"),
                            _mt_stats.get("mt_cache_hits"),
                        )
                    except Exception as _s7_exc:
                        # Simple must NOT fall back to AI-Core / Qwen path.
                        logger.error(
                            "Task %s: Simple MT LOCK failed (%s) — no agent/Qwen fallback",
                            task_id,
                            _s7_exc,
                        )
                        with STATE_LOCK:
                            stamp_simple_mt_lock(task.setdefault("info", {}))
                            task["info"]["mt_stage7_error"] = str(_s7_exc)
                            task["info"]["translate_method"] = "marian_batch_failed"
                        from engines.dubbing_engine.pipeline_failure_diag import (
                            STAGE_TRANSLATION,
                        )

                        _fail(
                            task_id,
                            [
                                lp.get(
                                    "translate_failed",
                                    "Ошибка перевода (Simple Marian batch).",
                                )
                            ],
                            stage=STAGE_TRANSLATION,
                            exc=_s7_exc,
                            error_code="SIMPLE_MT_FAILED",
                        )
                        return
                if (not _stage7_done) and _manifest_path and Path(_manifest_path).is_file():
                    # Pro/Studio only — Simple already returned or set _stage7_done.
                    _agent_segments = _build_agent_segments(
                        source_segments_snapshot, timing_map_for_translate
                    )
                    _dir_segments = _run_agent_safe(
                        task_id,
                        "Director/v1",
                        lambda: _run_director_agent_path(
                            task_id,
                            _manifest_path,
                            source_segments_snapshot,
                            timing_map_for_translate,
                            source_lang=translation_source_lang,
                        ),
                        fallback_fn=lambda: _agent_segments,
                        record_success=False,
                    )
                    if _dir_segments:
                        _agent_segments = _dir_segments
                    _orch_tr_state = {
                        "video_path": video_path,
                        "target_lang": target_lang,
                        "source_lang": translation_source_lang,
                        "segments": _agent_segments,
                        "director_agent_status": (
                            (AUTO_TASKS.get(task_id, {}).get("info") or {}).get(
                                "director_agent_status"
                            )
                            or "success"
                        ),
                    }
                    _tr_orch = _run_ai_core_orchestrator(
                        task_id,
                        video_path,
                        _manifest_path,
                        _orch_tr_state,
                        agents=["translation"],
                    )
                    _tr_result = _tr_orch.agent_results.get("translation")
                    if _tr_result and _tr_result.status in ("success", "warning"):
                        _sync_orchestrator_segments_to_task(
                            task_id, _tr_orch.state, "translation"
                        )
                        segments_data = _tr_orch.state.get("segments") or []
                        segments = [
                            str(s.get("translated_text") or s.get("text") or "").strip()
                            for s in segments_data
                        ]
                        meta = {
                            "pipeline": "translation_agent_v1",
                            "naturalizer_executed": False,
                            "naturalizer_applied": False,
                            "timing_aware_executed": False,
                            "timing_aware_applied": False,
                            "translation_agent": True,
                            "semantic_agent": False,
                            "translator_used": (_tr_result.metrics or {}).get(
                                "translator_used"
                            ),
                            "avg_confidence": (_tr_result.metrics or {}).get(
                                "avg_overall"
                            ),
                        }
                        if translate_meta is not None:
                            translate_meta.append(meta)
                        with STATE_LOCK:
                            if task_id in AUTO_TASKS:
                                info = AUTO_TASKS[task_id].setdefault("info", {})
                                info["translation_agent_path"] = True
                                info["translation_agent_status"] = _tr_result.status
                                info["translation_agent_metrics"] = _tr_result.metrics
                    else:
                        segments = _run_agent_safe(
                            task_id,
                            "Translation/v1",
                            lambda: _run_translation_agent_path(
                                task_id,
                                _manifest_path,
                                source_segments_snapshot,
                                timing_map_for_translate,
                                translate_meta=translate_meta,
                            ),
                            fallback_fn=lambda: _prepare_translated_segments(
                                task_id,
                                source_segments_snapshot,
                                timing_map_for_translate,
                                translation_source_lang,
                                target_lang,
                                ui_lang,
                                translate_meta=translate_meta,
                            ),
                            record_success=False,
                        )
                    with STATE_LOCK:
                        _used_agent = bool(
                            (AUTO_TASKS.get(task_id, {}).get("info") or {}).get(
                                "translation_agent_path"
                            )
                        )
                    translate_method = (
                        "translation_agent_v1" if _used_agent else "naturalizer_per_group_fallback"
                    )
                    profiler.set_meta(translate_cache="agent")
                elif not _stage7_done:
                    _conveyor_done = False
                    if not skip_tts:
                        try:
                            from engines.happy_path import skip_advanced_text_shorteners as _hp_fc

                            with STATE_LOCK:
                                _info_fc_gate = dict(task.get("info") or {})
                            # Stage 6: Simple owns TTS via parallel+cache — skip full conveyor TTS.
                            if _hp_fc(_info_fc_gate) or _info_fc_gate.get("simple_pipeline"):
                                raise RuntimeError("skip_full_conveyor_simple_stage6")
                            from engines.pipeline_orchestrator.dub_conveyor_bridge import (
                                try_run_full_conveyor,
                            )

                            with STATE_LOCK:
                                _tts_eng_fc = str(
                                    task["info"].get("tts_engine") or "edge-offline"
                                )
                            _whisper_sec_fc = 0.0
                            if pipeline_timer is not None:
                                try:
                                    _whisper_sec_fc = float(
                                        (pipeline_timer._seconds or {}).get("whisper", 0)
                                        or 0
                                    )
                                except Exception:
                                    pass
                            _fc = try_run_full_conveyor(
                                task_id=task_id,
                                source_segments=source_segments_snapshot,
                                timing_map=timing_map_for_translate,
                                source_lang=translation_source_lang,
                                target_lang=target_lang,
                                voice=voice,
                                app_dir=APP_DIR,
                                tts_rate=tts_rate,
                                tts_pitch=tts_pitch,
                                tts_engine=_tts_eng_fc,
                                whisper_sec=_whisper_sec_fc,
                            )
                            if _fc:
                                segments = list(_fc.segments)
                                translate_method = "full_conveyor"
                                _conveyor_done = True
                                with STATE_LOCK:
                                    info_fc = task.setdefault("info", {})
                                    info_fc["segments_data"] = _fc.segments_data
                                    info_fc["conveyor_tts_done"] = True
                                    info_fc["tts_files"] = list(_fc.tts_files)
                                    info_fc["full_conveyor"] = _fc.report
                                    info_fc["translate_method"] = translate_method
                                    info_fc["pipeline_conveyor_timing"] = {
                                        "whisper_sec": _fc.whisper_sec,
                                        "marian_sec": _fc.marian_sec,
                                        "llm_sec": _fc.llm_sec,
                                        "tts_sec": _fc.tts_sec,
                                    }
                                profiler.set_meta(translate_cache="full_conveyor")
                                logger.info(
                                    "Task %s: full conveyor translate+TTS (%d segs, %d files)",
                                    task_id,
                                    len(segments),
                                    len(_fc.tts_files),
                                )
                        except Exception as _fc_exc:
                            logger.warning(
                                "Task %s: full conveyor skipped: %s", task_id, _fc_exc
                            )
                    if not _conveyor_done:
                        try:
                            segments = _prepare_translated_segments(
                                task_id,
                                source_segments_snapshot,
                                timing_map_for_translate,
                                translation_source_lang,
                                target_lang,
                                ui_lang,
                                translate_meta=translate_meta,
                            )
                            translate_method = "naturalizer_per_group"
                            with STATE_LOCK:
                                _audits = (
                                    AUTO_TASKS.get(task_id, {})
                                    .get("info", {})
                                    .get("translation_audits")
                                    or []
                                )
                            _route = str(_audits[0].get("route_label") or "") if _audits else ""
                            _engine = str(_audits[0].get("engine") or "") if _audits else ""
                            _avg_q = (
                                sum(float(a.get("quality_score") or 0) for a in _audits)
                                / max(len(_audits), 1)
                                if _audits
                                else 0.0
                            )
                            save_translate_cache(
                                APP_DIR,
                                source_segments_snapshot,
                                translation_source_lang,
                                target_lang,
                                segments,
                                route_label=_route,
                                engine=_engine,
                                quality_score=_avg_q,
                            )
                            profiler.set_meta(translate_cache="miss")
                        except Exception as tr_err:
                            from engines.model_manager.runtime import OfflineOnlyError
                            from engines.mt.translate_guard import TranslationTimeoutError
                            from engines.core.feature_flags import IS_DEBUG_LEARNING_MODE

                            _open_ddf.record_agent(
                                task_id, "Translation", called=True, success=False,
                                error=str(tr_err), fallback_used=True,
                                decision="fallback_to_source" if IS_DEBUG_LEARNING_MODE() else "pipeline_fail",
                            )

                            if IS_DEBUG_LEARNING_MODE():
                                logger.warning(
                                    "[DDF] Task %s: Translation FAILED (%s) — "
                                    "Debug mode: using source segments as fallback.",
                                    task_id, tr_err,
                                )
                                segments = list(source_segments_snapshot)
                                translate_method = "debug_fallback_source"
                            else:
                                logger.exception("Task %s: translation failed", task_id)
                                lp = LOCALIZATION.get(ui_lang, LOCALIZATION["ru"])
                                if isinstance(tr_err, OfflineOnlyError):
                                    msg = lp.get(
                                        "translate_not_prepared",
                                        "Модель перевода не подготовлена. Дождитесь завершения «Подготовка компонентов».",
                                    )
                                elif isinstance(tr_err, TranslationTimeoutError):
                                    logger.warning("Task %s: translation timeout (dev): %s", task_id, tr_err)
                                    msg = lp.get(
                                        "long_processing",
                                        "Выполняется длительная обработка. Пожалуйста, подождите.",
                                    )
                                else:
                                    msg = lp.get(
                                        "translate_failed",
                                        "Ошибка перевода. Проверьте интернет или выберите язык вручную.",
                                    )
                                from engines.dubbing_engine.pipeline_failure_diag import STAGE_TRANSLATION

                                _fail(
                                    task_id,
                                    [msg],
                                    stage=STAGE_TRANSLATION,
                                    exc=tr_err,
                                    error_code=(
                                        "TRANSLATION_MODEL_MISSING"
                                        if isinstance(tr_err, OfflineOnlyError)
                                        else (
                                            "TRANSLATION_TIMEOUT"
                                            if isinstance(tr_err, TranslationTimeoutError)
                                            else "TRANSLATION_FAILED"
                                        )
                                    ),
                                )
                                return

        if translate_meta:
            meta0 = translate_meta[0]
            profiler.add("translation", float(meta0.get("translation_sec") or meta0.get("marian_sec") or 0))
            profiler.add("naturalizer", float(meta0.get("naturalizer_sec") or 0))
            if pipeline_timer is not None:
                marian_s = float(meta0.get("marian_sec") or meta0.get("translation_sec") or 0)
                llm_s = float(meta0.get("llm_adaptation_sec") or 0)
                val_s = float(meta0.get("validation_sec") or 0)
                post_s = float(meta0.get("restore_sec") or 0) + float(
                    meta0.get("timing_aware_sec") or 0
                )
                pipeline_timer.add("translation", marian_s)
                pipeline_timer.add("natural", llm_s)
                pipeline_timer.add("validation", val_s)
                pipeline_timer.add("translation_post", post_s)
                br = meta0.get("translation_timing_breakdown") or {}
                if br and pipeline_timer is not None:
                    pipeline_timer.set_translation_breakdown(br)
                if br:
                    with STATE_LOCK:
                        t = AUTO_TASKS.get(task_id)
                        if t:
                            t.setdefault("info", {})["translation_timing"] = br
                            t["info"]["translation_timing_breakdown"] = br
            _ddf_agent = (
                "Translation/v1"
                if meta0.get("translation_agent") or translate_method == "translation_agent_v1"
                else "Translation"
            )
            _open_ddf.record_agent(
                task_id, _ddf_agent, called=True, success=True,
                decision=translate_method,
            )
            _nat_executed = bool(meta0.get("naturalizer_executed"))
            _nat_applied = bool(meta0.get("naturalizer_applied"))
            if _nat_executed and not _nat_applied:
                _open_ddf.record_agent(
                    task_id, "NaturalTranslation/LLM", called=True, success=False,
                    decision="LLM skipped - rule-based fallback used", fallback_used=True,
                )
            elif _nat_applied:
                _open_ddf.record_agent(
                    task_id, "NaturalTranslation/LLM", called=True, success=True,
                    decision="naturalizer_applied",
                )

        with STATE_LOCK:
            task["info"]["translate_meta"] = translate_meta[0] if translate_meta else {}
            task["info"]["target_lang"] = target_lang
            if translate_meta and translate_meta[0].get("pipeline"):
                task["info"]["translation_quality_log"] = str(
                    APP_DIR / "output" / "dev" / "translation_quality.log"
                )

        dev_diag.log_translation(
            source_lang=translation_source_lang,
            target_lang=target_lang,
            source_segments=source_segments_snapshot,
            translated_segments=segments,
            skip_translate=skip_translate_flag and bool(pre_translated),
            method=translate_method,
        )
        try:
            from engines.translation_memory import memory_summary
            from engines.translation_router import engine_rankings

            with STATE_LOCK:
                audits = list(task["info"].get("translation_audits") or [])
                tmeta = (translate_meta[0] if translate_meta else {}) or {}
            dev_diag.log_translation_pipeline_report(
                source_lang=translation_source_lang,
                target_lang=target_lang,
                audits=audits,
                translate_meta=tmeta,
                router_summary={
                    "rankings": [
                        {"engine": e, "score": s, "reason": r}
                        for e, s, r in engine_rankings(
                            APP_DIR, translation_source_lang, target_lang
                        )
                    ],
                    "memory": memory_summary(
                        APP_DIR, translation_source_lang, target_lang
                    ),
                },
            )
        except Exception as rep_err:
            logger.debug("translation_pipeline report skipped: %s", rep_err)
        with STATE_LOCK:
            task["info"]["dev_diagnostics"] = dev_diag.paths()
        logger.debug(
            "Task %s: translation done, segments=%d",
            task_id,
            len(segments),
        )
        _runtime_stage_record(
            task_id,
            runtime_diag,
            3,
            STAGE_TRANSLATION,
            segments_ok=len(segments),
        )
        _wtm_record_checkpoint(wtm_cp_log, task_id, "post_translate")

        # ── Stage 3: enforce 1 source = 1 translation (anti-bleed) ──
        try:
            from engines.translation_segment_parity import (
                detect_translation_bleed,
                enforce_one_to_one_translations,
                stamp_segment_translation_audit,
            )

            with STATE_LOCK:
                _src_par = list(
                    task["info"].get("source_segments") or source_segments_snapshot or []
                )
                _tm_par = list(
                    task["info"].get("timing_map_backup")
                    or timing_map_for_translate
                    or []
                )
                _sd_par = list(task["info"].get("segments_data") or [])
            segments, _parity_audits = enforce_one_to_one_translations(
                _src_par,
                list(segments),
                timing_map=_tm_par,
            )
            _bleed_flags = detect_translation_bleed(_src_par, segments)
            with STATE_LOCK:
                task["info"]["translation_parity"] = {
                    "applied": True,
                    "segments": len(segments),
                    "bleed_count": sum(1 for b in _bleed_flags if b),
                    "actions": [a for a in _parity_audits if a.get("action")][:40],
                }
                for _i_p, _txt_p in enumerate(segments):
                    if _i_p < len(_sd_par) and isinstance(_sd_par[_i_p], dict):
                        try:
                            from engines.translation_validation import (
                                stamp_authoritative_final_text,
                            )

                            stamp_authoritative_final_text(_sd_par[_i_p], _txt_p)
                        except Exception:
                            _sd_par[_i_p]["text"] = _txt_p
                            _sd_par[_i_p]["final_text"] = _txt_p
                            _sd_par[_i_p]["tts_text"] = _txt_p
                        stamp_segment_translation_audit(
                            _sd_par[_i_p],
                            original=_src_par[_i_p] if _i_p < len(_src_par) else "",
                            translated=_txt_p,
                            tts_text=_txt_p,
                            translation_bleed=bool(
                                _bleed_flags[_i_p] if _i_p < len(_bleed_flags) else False
                            ),
                        )
                if _sd_par:
                    task["info"]["segments_data"] = _sd_par
                _aud_par = task["info"].get("translation_audits") or []
                for _ar in _aud_par:
                    if not isinstance(_ar, dict):
                        continue
                    _ai = int(_ar.get("index", -1))
                    if 0 <= _ai < len(segments):
                        _ar["translation_bleed"] = bool(
                            _bleed_flags[_ai] if _ai < len(_bleed_flags) else False
                        )
                        if str(_ar.get("tts_text") or "") != segments[_ai]:
                            _ar["tts_text"] = segments[_ai]
                            _ar["final_text"] = segments[_ai]
                logger.info(
                    "Task %s: translation_parity n=%d bleed=%d",
                    task_id,
                    len(segments),
                    sum(1 for b in _bleed_flags if b),
                )
        except Exception as _par_exc:
            logger.warning("Task %s: translation_parity skipped: %s", task_id, _par_exc)

        # ── Happy Path text-fit BEFORE review/TTS (natural rate > atempo) ──
        try:
            from engines.happy_path import skip_advanced_text_shorteners as _hp_fit
            from engines.text_slot_fit import fit_segments_to_slots

            with STATE_LOCK:
                _info_fit = dict(task.get("info") or {})
                _tm_fit = list(
                    task["info"].get("timing_map_backup")
                    or timing_map_for_translate
                    or []
                )
                _src_fit = list(
                    task["info"].get("source_segments") or source_segments_snapshot or []
                )
            if _hp_fit(_info_fit) and segments:
                segments, _fit_audits = fit_segments_to_slots(
                    list(segments),
                    _tm_fit,
                    lang=str(target_lang or "uk"),
                    source_hints=_src_fit,
                )
                # Stage 15: never lock Final shorter than Raw MT by >15% words.
                try:
                    from engines.text_slot_fit import prefer_full_meaning_text

                    _aud_raw = list(
                        (task.get("info") or {}).get("translation_audits") or []
                    )
                    _sd_raw = list(
                        (task.get("info") or {}).get("segments_data") or []
                    )
                    for _i_ret, _txt_ret in enumerate(list(segments)):
                        _raw_ret = ""
                        if _i_ret < len(_sd_raw) and isinstance(_sd_raw[_i_ret], dict):
                            _raw_ret = str(
                                _sd_raw[_i_ret].get("raw_translation") or ""
                            )
                        if not _raw_ret and _i_ret < len(_aud_raw):
                            _ar = _aud_raw[_i_ret]
                            if isinstance(_ar, dict):
                                _raw_ret = str(_ar.get("raw_translation") or "")
                        _restored, _did = prefer_full_meaning_text(
                            str(_txt_ret or ""), _raw_ret
                        )
                        if _did and _restored:
                            segments[_i_ret] = _restored
                            if _i_ret < len(_fit_audits) and isinstance(
                                _fit_audits[_i_ret], dict
                            ):
                                _fit_audits[_i_ret]["action"] = "atempo_prefer"
                                _fit_audits[_i_ret]["meaning_preserved"] = True
                                _fit_audits[_i_ret]["changed"] = False
                                _fit_audits[_i_ret]["text"] = _restored
                                _rs = list(_fit_audits[_i_ret].get("reasons") or [])
                                _rs.append("restore_raw_mt_retention")
                                _fit_audits[_i_ret]["reasons"] = _rs
                except Exception as _ret_exc:
                    logger.debug(
                        "Task %s: raw retention restore skipped: %s",
                        task_id,
                        _ret_exc,
                    )
                _fit_changed = sum(1 for a in _fit_audits if a.get("changed"))
                with STATE_LOCK:
                    task["info"]["text_slot_fit"] = {
                        "applied": True,
                        "changed": _fit_changed,
                        "segments": len(_fit_audits),
                        "rows": _fit_audits[:80],
                    }
                    _sd_fit = list(task["info"].get("segments_data") or [])
                    try:
                        from engines.translation_segment_parity import (
                            detect_translation_bleed as _det_bleed,
                        )

                        _bleed_fit = _det_bleed(_src_fit, segments)
                    except Exception:
                        _bleed_fit = [False] * len(segments)
                    for _i_f, _a in enumerate(_fit_audits):
                        if _i_f >= len(segments):
                            break
                        if _i_f < len(_sd_fit) and isinstance(_sd_fit[_i_f], dict):
                            try:
                                from engines.translation_validation import (
                                    stamp_authoritative_final_text,
                                )

                                stamp_authoritative_final_text(
                                    _sd_fit[_i_f], segments[_i_f]
                                )
                            except Exception:
                                _sd_fit[_i_f]["text"] = segments[_i_f]
                                _sd_fit[_i_f]["final_text"] = segments[_i_f]
                            _sd_fit[_i_f]["text_slot_fit"] = {
                                "action": _a.get("action"),
                                "strategy": _a.get("strategy") or _a.get("action") or "ok",
                                "predicted_ms_before": _a.get("predicted_ms_before"),
                                "predicted_ms_after": _a.get("predicted_ms_after"),
                                "predicted_tts_ms": _a.get("predicted_tts_ms")
                                or _a.get("predicted_ms_after"),
                                "slot_ms": _a.get("slot_ms"),
                                "fill_ratio": _a.get("fill_ratio"),
                                "atempo": _a.get("atempo"),
                                "dead_air_risk_ms": _a.get("dead_air_risk_ms"),
                                "text_fit_applied": bool(_a.get("changed")),
                                "meaning_truncated": bool(_a.get("meaning_truncated")),
                                "translation_bleed": bool(
                                    _bleed_fit[_i_f] if _i_f < len(_bleed_fit) else False
                                ),
                                "original_len": len(
                                    str(_src_fit[_i_f] if _i_f < len(_src_fit) else "")
                                ),
                                "fitted_len": len(str(segments[_i_f] or "")),
                            }
                            # Stage 19 Review fields on the segment itself.
                            _sd_fit[_i_f]["slot_ms"] = int(_a.get("slot_ms") or 0)
                            _sd_fit[_i_f]["predicted_tts_ms"] = int(
                                _a.get("predicted_tts_ms")
                                or _a.get("predicted_ms_after")
                                or 0
                            )
                            _sd_fit[_i_f]["fill_ratio"] = _a.get("fill_ratio")
                            _sd_fit[_i_f]["atempo"] = _a.get("atempo")
                            _sd_fit[_i_f]["slot_strategy"] = (
                                _a.get("strategy") or _a.get("action") or "ok"
                            )
                            if _a.get("changed"):
                                logger.info(
                                    "text_fit seg#%d slot=%s pred %s→%s truncated=%s",
                                    _i_f,
                                    _a.get("slot_ms"),
                                    _a.get("predicted_ms_before"),
                                    _a.get("predicted_ms_after"),
                                    _a.get("meaning_truncated"),
                                )
                    # Stage 4: lock final_tts_text BEFORE Review — Review == TTS.
                    try:
                        from engines.tts_text_authority import lock_segments_final_tts

                        _aud_lock = list(task["info"].get("translation_audits") or [])
                        segments = lock_segments_final_tts(
                            _sd_fit,
                            list(segments),
                            audits=_aud_lock,
                            source="text_slot_fit",
                        )
                        task["info"]["translation_audits"] = _aud_lock
                        task["info"]["final_tts_locked"] = True
                        # Immutable snapshot — restore before TTS if anything rewrites Final.
                        task["info"]["fitted_tts_texts"] = list(segments)
                        task["info"]["fitted_tts_source"] = "text_slot_fit"
                    except Exception as _lock_exc:
                        logger.warning(
                            "Task %s: final_tts lock skipped: %s", task_id, _lock_exc
                        )
                    task["info"]["segments_data"] = _sd_fit
                logger.info(
                    "Task %s: text_slot_fit changed=%d/%d (atempo cap 1.15)",
                    task_id,
                    _fit_changed,
                    len(_fit_audits),
                )
        except Exception as _fit_exc:
            logger.warning("Task %s: text_slot_fit skipped: %s", task_id, _fit_exc)

        with STATE_LOCK:
            review_before_tts = bool(
                task["info"].get("translation_review_before_tts", True)
            )
        _update_progress_detail(
            task_id,
            total_segments=len(segments),
            segments_done=0,
            phase="translation_review" if review_before_tts else "translate",
        )

        if review_before_tts and segments:
            _populate_translation_review_data(task_id, segments)
            if _translation_review_requires_manual_hold():
                _enter_translation_review_pause(task_id)
                logger.info(
                    "Task %s: paused for manual pre-TTS translation review (%d segments)",
                    task_id,
                    len(segments),
                )
                if not _ensure_control(task_id, ui_lang):
                    return
            else:
                _resume_from_translation_review(task_id)
                logger.info(
                    "Task %s: translation review auto-approved (%d segments); "
                    "continuing pipeline (no manual hold)",
                    task_id,
                    len(segments),
                )
            with STATE_LOCK:
                segments_data = task["info"].get("segments_data") or []
                segments = [
                    str(seg.get("text") or "").strip() for seg in segments_data
                ]

        # ── TPS / Translation Fast Path v2 ───────────────────────────────
        # MT → Naturalizer(rule-first) → Fast QA → Retry(1) → Judge → Manual
        # Full Semantic/Timing/Grammar orchestrator only as legacy fallback.
        _tps_enabled = True
        try:
            import os as _os_tps

            _tps_enabled = _os_tps.getenv("TPS_ENABLED", "1").strip().lower() not in (
                "0",
                "false",
                "no",
                "off",
            )
        except Exception:
            _tps_enabled = True

        if _tps_enabled and segments:
            try:
                from engines.happy_path import skip_advanced_text_shorteners as _hp_tps

                with STATE_LOCK:
                    _info_tps_gate = dict(task.get("info") or {})
                if _hp_tps(_info_tps_gate):
                    _tps_enabled = False
                    with STATE_LOCK:
                        task["info"]["tps_skipped"] = "happy_path"
                        # Stage 7: do not fall into streaming_text orchestrator (~90–110s).
                        task["info"]["tps_skip_orchestrator"] = True
                    logger.info(
                        "Task %s: TPS skipped (happy_path — text_slot_fit only)",
                        task_id,
                    )
            except Exception:
                pass
        if _tps_enabled and segments:
            try:
                from engines.tps import run_tps_pipeline

                with STATE_LOCK:
                    _tps_info = task["info"]
                    _tps_src = list(_tps_info.get("source_segments") or source_segments_snapshot)
                    _tps_session = _tps_info.get("session_dir")
                    _tps_src_lang = str(
                        _tps_info.get("source_lang")
                        or translation_source_lang
                        or "en"
                    )
                    _tps_tgt_lang = str(_tps_info.get("target_lang") or target_lang or "uk")
                _tps_result = run_tps_pipeline(
                    task_id=task_id,
                    originals=_tps_src,
                    translations=list(segments),
                    src_lang=_tps_src_lang,
                    tgt_lang=_tps_tgt_lang,
                    app_dir=str(APP_DIR),
                    session_dir=_tps_session,
                    info=_tps_info,
                    persist_metrics=True,
                )
                segments = list(_tps_result.texts)
                with STATE_LOCK:
                    task["info"]["tps"] = True
                    task["info"]["tps_result"] = _tps_result.to_dict()
                    task["info"]["tps_metrics"] = _tps_result.metrics.to_dict()
                    # Hard-clear TTS-bound fields on blocked/manual-fail segments
                    _sd_tps = task["info"].get("segments_data") or []
                    for _i_b, _seg_b in enumerate(_sd_tps):
                        if not isinstance(_seg_b, dict):
                            continue
                        if _seg_b.get("tts_blocked") or _seg_b.get("skip_tts"):
                            for _k in (
                                "text",
                                "plain_text",
                                "translation_text",
                                "final_text",
                                "text_for_tts",
                                "semantic_text",
                                "semantic_engine_text",
                            ):
                                _seg_b[_k] = ""
                            if _i_b < len(segments):
                                segments[_i_b] = ""
                    # Single timing owner: skip DSAL text rewrite; stamp duration only
                    task["info"]["skip_dsal_pre_lock"] = True
                    task["info"]["skip_text_adaptation"] = True
                    task["info"]["tps_skip_orchestrator"] = True
                    task["info"]["quality_agent_path"] = True  # mark quality done via TQE
                    # TRH: Naturalizer DID run inside TPS — correct agent-path meta lie
                    task["info"]["naturalizer_executed"] = True
                    task["info"]["naturalizer_applied"] = bool(
                        (_tps_result.metrics.to_dict() or {})
                        # fallback: any segment changed
                    )
                    _nat_applied = False
                    for _s in task["info"].get("segments_data") or []:
                        if isinstance(_s, dict) and (
                            (_s.get("trh") or {}).get("naturalizer_applied")
                            or (
                                str(_s.get("naturalized_text") or "")
                                != str(_s.get("raw_mt") or "")
                                and str(_s.get("naturalized_text") or "").strip()
                            )
                        ):
                            _nat_applied = True
                            break
                    task["info"]["naturalizer_applied"] = _nat_applied
                    task["info"]["dsal_mode"] = "duration_only"
                    task["info"]["dsal_skip_reason"] = "tps_duration_only_no_text_rewrite"
                try:
                    from engines.tps import stamp_duration_after_approved

                    with STATE_LOCK:
                        _tps_info_stamp = task["info"]
                    stamp_duration_after_approved(_tps_info_stamp, task_id=task_id)
                except Exception as _stamp_exc:
                    logger.debug("[TPS] duration stamp skipped: %s", _stamp_exc)
                logger.info(
                    "[TPS] task %s: fast=%d retry=%d judge=%d manual=%d gate=%s",
                    task_id,
                    _tps_result.metrics.fast_path_count,
                    _tps_result.metrics.retry_path_count,
                    _tps_result.metrics.llm_judge_count,
                    _tps_result.metrics.manual_review_count,
                    _tps_result.gate_passed,
                )
                if _tps_result.manual_indices:
                    # Force manual Translation Review for failed segments
                    _populate_translation_review_data(task_id, segments)
                    _enter_translation_review_pause(task_id)
                    logger.info(
                        "[TPS] task %s: Manual Review required for segments %s",
                        task_id,
                        _tps_result.manual_indices[:20],
                    )
                    if not _ensure_control(task_id, ui_lang):
                        return
                    with STATE_LOCK:
                        segments_data = task["info"].get("segments_data") or []
                        segments = [
                            str(
                                seg.get("approved_text")
                                or seg.get("text")
                                or ""
                            ).strip()
                            for seg in segments_data
                        ]
            except Exception as _tps_exc:
                logger.warning("[TPS] pipeline failed, falling back to orchestrator: %s", _tps_exc)
                with STATE_LOCK:
                    task["info"]["tps_error"] = str(_tps_exc)
                    task["info"]["tps_skip_orchestrator"] = False

        # Semantic → Quality agents via orchestrator (legacy / TPS fallback only)
        with STATE_LOCK:
            _manifest_path = (task.get("info") or {}).get("manifest_path")
            _translation_status = (task.get("info") or {}).get("translation_agent_status")
            _segments_data_snap = list(task["info"].get("segments_data") or [])
            _quality_done = bool(task["info"].get("quality_agent_path"))
            _tps_skip_orch = bool(task["info"].get("tps_skip_orchestrator"))
        if (
            not _tps_skip_orch
            and not _quality_done
            and _manifest_path
            and Path(_manifest_path).is_file()
            and _segments_data_snap
            and any(
                str(s.get("translated_text") or s.get("text") or "").strip()
                for s in _segments_data_snap
            )
            and _translation_status in ("success", "warning", None)
        ):
            try:
                from engines.ai_core.llm_bootstrap import prepare_llm_for_pipeline

                with STATE_LOCK:
                    _llm_info = dict(task.get("info") or {})
                _llm_boot = prepare_llm_for_pipeline(
                    task_id, _llm_info, app_dir=APP_DIR, phase="AI_CORE"
                )
                with STATE_LOCK:
                    task["info"]["llm_bootstrap"] = _llm_boot
            except Exception as _llm_boot_exc:
                logger.debug("LLM bootstrap before text agents: %s", _llm_boot_exc)
            _orch_segments = _run_agent_safe(
                task_id,
                "AI-Core/Orchestrator",
                lambda: _run_orchestrator_text_agents(
                    task_id,
                    video_path,
                    _manifest_path,
                    _segments_data_snap,
                ),
                record_success=False,
            )
            if _orch_segments is not None:
                segments = _orch_segments
                logger.info(
                    "Task %s: AI Core orchestrator text agents completed (%d segments)",
                    task_id,
                    len(segments),
                )
            # Peer Validation Pipeline — semantic/grammar/timing agents own their fields;
            # post-hoc polish duplicated entity/grammar work (removed per AI Core Simplification TZ).

        # Legacy per-agent path removed — orchestrator handles semantic→quality.

        # ══ ШАГ 4: TTS или только субтитры ═══════════════════════════════
        timed_audio_path = None
        tts_files: list = []
        timing_warnings: list = []
        overlap_report: dict = {"ok": True}
        sso_active = False

        with STATE_LOCK:
            current_timing_map_snapshot = copy.deepcopy(
                task["info"]["timing_map_backup"]
            )
            from engines.cleaner import align_segments_to_timing_map
            from engines.pipeline_integrity.tts_segment_fields import resolve_segment_text_for_tts
            from engines.tts_text_path import final_texts_from_info

            info_snapshot = copy.deepcopy(task.get("info") or {})
            segments = final_texts_from_info(info_snapshot)
            raw_mt_segments = _raw_mt_texts_from_info(info_snapshot)
            if not any(segments):
                segments = [
                    resolve_segment_text_for_tts(seg)
                    for seg in (info_snapshot.get("segments_data") or [])
                ]
            if not any(raw_mt_segments):
                raw_mt_segments = list(segments)
            # Happy Path: never blind timing-redistribute (Stage 3 anti-bleed).
            _src_align = list(
                info_snapshot.get("source_segments") or source_segments_snapshot or []
            )
            _use_source_align = False
            try:
                from engines.happy_path import skip_advanced_text_shorteners
                _use_source_align = bool(skip_advanced_text_shorteners(info_snapshot))
            except Exception:
                _use_source_align = False
            if _use_source_align and _src_align:
                from engines.translation_segment_parity import (
                    enforce_one_to_one_translations,
                )
                segments, _ = enforce_one_to_one_translations(
                    _src_align, segments, timing_map=current_timing_map_snapshot
                )
                raw_mt_segments, _ = enforce_one_to_one_translations(
                    _src_align, raw_mt_segments, timing_map=current_timing_map_snapshot
                )
                _tm_n = len(current_timing_map_snapshot or [])
                if _tm_n and len(segments) < _tm_n:
                    segments = list(segments) + [""] * (_tm_n - len(segments))
                    raw_mt_segments = list(raw_mt_segments) + [""] * (
                        _tm_n - len(raw_mt_segments)
                    )
                elif _tm_n and len(segments) > _tm_n:
                    segments = list(segments)[:_tm_n]
                    raw_mt_segments = list(raw_mt_segments)[:_tm_n]
            else:
                segments = align_segments_to_timing_map(
                    segments, current_timing_map_snapshot
                )
                raw_mt_segments = align_segments_to_timing_map(
                    raw_mt_segments, current_timing_map_snapshot
                )
            tts_engine_id = task["info"].get("tts_engine") or "edge-offline"
            try:
                from engines.tts_backends import bind_pipeline_tts_from_info

                tts_engine_id = bind_pipeline_tts_from_info(task["info"])
            except Exception:
                pass
            _skip_timing_adapt = bool(task["info"].get("translation_agent_path"))
            _skip_semantic_shorten = bool(task["info"].get("semantic_agent_path"))
            _skip_timing_shorten = bool(task["info"].get("timing_agent_path"))
            _skip_grammar_shorten = bool(task["info"].get("grammar_agent_path"))

        _tat_records: list = []

        # Happy Path: skip Timing-Aware LLM / multi-shortener adaptation.
        try:
            from engines.happy_path import advanced_adaptation_enabled as _adv_tat

            with STATE_LOCK:
                _info_tat_gate = dict(task.get("info") or {})
            if not _adv_tat(_info_tat_gate):
                _skip_timing_adapt = True
                with STATE_LOCK:
                    task["info"]["timing_aware_skipped"] = "happy_path"
                    task["info"]["timing_aware_executed"] = False
                    task["info"]["timing_aware_applied"] = False
                logger.info(
                    "Task %s: Timing-Aware Translation skipped (happy_path)",
                    task_id,
                )
        except Exception:
            pass

        if current_timing_map_snapshot and not _skip_timing_adapt:
            from engines.ai_manager.installer import warmup_ai_for_dub
            from engines.timing_aware_translation import (
                adapt_segments_to_timing,
                apply_records_to_audits,
            )

            runtime_diag.stage_begin("Timing-Aware Translation")
            try:
                from engines.ai_core.llm_bootstrap import prepare_llm_for_pipeline

                with STATE_LOCK:
                    _llm_info_tat = dict(task.get("info") or {})
                _llm_tat = prepare_llm_for_pipeline(
                    task_id, _llm_info_tat, app_dir=APP_DIR, phase="ADAPTATION"
                )
                with STATE_LOCK:
                    task["info"]["llm_bootstrap_tat"] = _llm_tat
                    if not _llm_tat.get("available"):
                        _warns = task["info"].setdefault("user_warnings", [])
                        _warns.append(
                            {
                                "stage": "ADAPTATION",
                                "code": "LLM_MODEL_UNAVAILABLE",
                                "message": (
                                    "AI-модель недоступна (не установлена или не отвечает). "
                                    "Адаптация текста выполняется в упрощённом режиме."
                                ),
                            }
                        )
            except Exception:
                pass
            warmup_ai_for_dub(APP_DIR)

            with STATE_LOCK:
                _adapt_speed_mode = (
                    task["info"].get("adaptation_speed_mode")
                    or task["info"].get("dub_speed_mode")
                )
                _adapt_seg_budget = task["info"].get("adaptation_segment_budget_s")
                _adapt_proj_budget = task["info"].get("adaptation_project_budget_s")
                _content_mode_hint = task["info"].get("content_mode")

            def _adaptation_progress(done: int, total: int, strategy: str | None = None,
                                     eta_s: float | None = None) -> None:
                # "Адаптация текста... Сегмент N/M" + current strategy + ETA
                # (Task 8): keep the UI alive and informative during adaptation.
                shown = min(max(done, 0), total)
                status = f"Адаптация текста... Сегмент {shown} / {total}"
                if strategy:
                    status += f" · {strategy}"
                if eta_s is not None and eta_s > 0:
                    status += f" · ~{int(eta_s)}s"
                _update_progress_detail(
                    task_id,
                    phase="adaptation",
                    timing_substep="adapt",
                    total_segments=total,
                    segments_done=done,
                    adaptation_strategy=strategy,
                    adaptation_eta_s=(int(eta_s) if eta_s else None),
                    status_text=status,
                )

            # ── AI Core: single decision-making brain (ТЗ P0) ──────────────
            # AI Core analyses the whole project, decides the strategy (variant
            # counts, speed/quality mode, per-segment budget, voice delivery)
            # and then drives the adaptive dubbing pipeline. Every LLM call
            # flows through it. On any failure we fall back to the plain
            # per-segment adaptation path so a dub is never blocked.
            try:
                from engines.ai_core import get_ai_core

                _core = get_ai_core(task_id)
                _profile = _core.analyze(
                    source_segments=source_segments_snapshot,
                    translated_segments=segments,
                    timing_map=current_timing_map_snapshot,
                    src_lang=translation_source_lang,
                    tgt_lang=target_lang,
                    content_mode_hint=_content_mode_hint,
                )
                _strategy = _core.plan(
                    requested_mode=_adapt_speed_mode,
                    per_segment_budget_s=_adapt_seg_budget,
                    project_budget_s=_adapt_proj_budget,
                )
                _core_dict = _core.to_dict()
                with STATE_LOCK:
                    task["info"]["ai_core"] = _core_dict
                segments, _tat_records = _core.adapt_segments(
                    segments,
                    current_timing_map_snapshot,
                    source_segments_snapshot,
                    src_lang=translation_source_lang,
                    tgt_lang=target_lang,
                    raw_mt_segments=raw_mt_segments,
                    progress_cb=_adaptation_progress,
                )
            except Exception:
                logger.exception(
                    "[AICore] planning/adaptation failed — falling back to "
                    "direct per-segment adaptation"
                )
                try:
                    from engines.ai_adaptation_engine import (
                        set_adaptation_profile_override,
                    )

                    set_adaptation_profile_override(None)
                except Exception:
                    pass
                segments, _tat_records = adapt_segments_to_timing(
                    segments,
                    current_timing_map_snapshot,
                    source_segments_snapshot,
                    src_lang=translation_source_lang,
                    tgt_lang=target_lang,
                    task_id=task_id,
                    raw_mt_segments=raw_mt_segments,
                    speed_mode=_adapt_speed_mode,
                    per_segment_budget_s=_adapt_seg_budget,
                    project_budget_s=_adapt_proj_budget,
                    progress_cb=_adaptation_progress,
                )
            with STATE_LOCK:
                _audits_tat = task["info"].get("translation_audits") or []
                apply_records_to_audits(_audits_tat, _tat_records)
                task["info"]["translation_audits"] = _audits_tat
                _segments_data_now = task["info"].get("segments_data") or []
                _voice_by_idx: dict[int, dict] = {}
                for _rec in (_tat_records or []):
                    try:
                        _idx = int(getattr(_rec, "index", -1))
                    except Exception:
                        _idx = -1
                    _trace = dict(getattr(_rec, "ai_adaptation_trace", None) or {})
                    _voice = dict(_trace.get("voice") or {})
                    if _idx >= 0 and _voice:
                        _voice_by_idx[_idx] = _voice
                for _idx, _voice in _voice_by_idx.items():
                    if 0 <= _idx < len(_segments_data_now):
                        _segments_data_now[_idx]["ai_voice"] = _voice
                        _segments_data_now[_idx].setdefault(
                            "tts_emotion",
                            {
                                "emotion": _voice.get("emotion"),
                                "intonation": _voice.get("intonation"),
                                "rate": _voice.get("rate"),
                                "pitch": _voice.get("pitch"),
                            },
                        )
                        _segments_data_now[_idx].setdefault(
                            "intonation",
                            {"style": _voice.get("intonation"), "pause_scale": _voice.get("pause_scale")},
                        )
                task["info"]["segments_data"] = _segments_data_now
                if _voice_by_idx:
                    task["info"]["ai_voice_directions"] = _voice_by_idx
                tat_applied = any(r.adapted for r in _tat_records)
                task["info"]["timing_aware_applied"] = tat_applied
                task["info"]["timing_aware_executed"] = True
                task["info"]["timing_aware_records"] = [
                    r.to_dict() for r in _tat_records
                ]
                stages = dict(task["info"].get("pipeline_stages") or {})
                tat_stage = dict(stages.get("timing_aware_translation") or {})
                tat_stage.update(
                    {
                        "enabled": True,
                        "executed": True,
                        "applied": tat_applied,
                        "segments_adapted": sum(1 for r in _tat_records if r.adapted),
                        "segments_total": len(_tat_records),
                        "skip_reason": "fits_without_change" if not tat_applied else None,
                    }
                )
                stages["timing_aware_translation"] = tat_stage
                task["info"]["pipeline_stages"] = stages
            try:
                from engines.translation_adapt import get_llm_calls, get_llm_status

                with STATE_LOCK:
                    task["info"]["llm_calls"] = get_llm_calls()
                    task["info"]["llm_status"] = get_llm_status()
            except Exception:
                pass
            try:
                from engines.pipeline_integrity.passive_openddf import observe_stage_success

                observe_stage_success(task_id, "Timing-Aware Translation")
            except ImportError:
                pass

        # ══════════════════════════════════════════════════════════════════════
        # UNIFIED DUBBING ENGINE — Stress & Pronunciation (+ entity/punct gates)
        _natural_pauses_for_timing: list[int] = []
        _dub_engine_results: list | None = None
        _segments_before_engine = list(segments)
        _preserve_timing_text = any(
            bool((getattr(r, "ai_adaptation_trace", None) or {}).get("agent_timeline"))
            for r in (_tat_records or [])
        )
        with STATE_LOCK:
            _tps_skip_adapt = bool(
                (task.get("info") or {}).get("skip_text_adaptation")
                or (task.get("info") or {}).get("tps")
            )
            _info_hp = dict(task.get("info") or {})
        try:
            from engines.happy_path import skip_advanced_text_shorteners as _skip_adv

            _happy_skip_adapt = _skip_adv(_info_hp)
        except Exception:
            _happy_skip_adapt = True
        _preserve_timing_text = _preserve_timing_text or _tps_skip_adapt or _happy_skip_adapt
        if _happy_skip_adapt:
            logger.info(
                "Task %s: DubbingEngine text adaptation OFF (happy_path)",
                task_id,
            )

        try:
            from engines.dubbing_engine import DubbingEngine as _DubbingEngine

            _engine = _DubbingEngine(
                lang=target_lang,
                app_dir=APP_DIR,
                task_id=task_id,
                content_mode=content_mode,
                skip_text_adaptation=_preserve_timing_text,
            )
            with STATE_LOCK:
                _sd_engine = list(task["info"].get("segments_data") or [])
            _stamp_segment_identity(_sd_engine)
            _engine_sids = [
                str(s.get("segment_id") or "") if isinstance(s, dict) else ""
                for s in _sd_engine
            ]
            while len(_engine_sids) < len(segments):
                _engine_sids.append("")
            _dub_engine_results = _engine.process_all(
                segments,
                current_timing_map_snapshot,
                source_hints=source_segments_snapshot,
                natural_pauses_out=_natural_pauses_for_timing,
                segment_ids=_engine_sids[: len(segments)],
            )
            with STATE_LOCK:
                _review_freeze_engine = bool(
                    task["info"].get("translation_review_before_tts", True)
                    or task["info"].get("translation_review_approved")
                )
            # Final texts — skip_tts segments become empty (TTS skips them).
            # After Translation Review: keep operator-approved Final — engine may
            # only add pronunciation/SSML metadata, never rewrite spoken meaning.
            if _review_freeze_engine:
                segments = list(_segments_before_engine)
            else:
                segments = [
                    r.output_text if r.passed_validation and str(r.output_text or "").strip()
                    else (
                        _segments_before_engine[r.index]
                        if r.index < len(_segments_before_engine)
                        else ""
                    )
                    for r in _dub_engine_results
                ]
            # Happy Path: do NOT re-fit after Review (Stage 4 — Review == TTS).
            # Re-lock spoken buffer from final_tts_text only.
            try:
                from engines.happy_path import skip_advanced_text_shorteners as _hp_sc
                from engines.tts_text_authority import (
                    lock_segments_final_tts,
                    resolve_final_tts_text,
                )

                with STATE_LOCK:
                    _info_sc = dict(task.get("info") or {})
                    _sd_sc = list(task["info"].get("segments_data") or [])
                    _aud_sc = list(task["info"].get("translation_audits") or [])
                if _hp_sc(_info_sc):
                    _locked_texts = []
                    for _i_sc, _seg_sc in enumerate(_sd_sc):
                        if isinstance(_seg_sc, dict):
                            _lt = resolve_final_tts_text(_seg_sc)
                        else:
                            _lt = ""
                        if not _lt and _i_sc < len(segments):
                            _lt = str(segments[_i_sc] or "")
                        _locked_texts.append(_lt)
                    if len(_locked_texts) < len(segments):
                        _locked_texts.extend(
                            str(segments[i] or "")
                            for i in range(len(_locked_texts), len(segments))
                        )
                    segments = lock_segments_final_tts(
                        _sd_sc,
                        _locked_texts[: len(segments)]
                        if len(_locked_texts) >= len(segments)
                        else _locked_texts
                        + [""] * (len(segments) - len(_locked_texts)),
                        audits=_aud_sc,
                        source="pre_tts_lock",
                    )
                    with STATE_LOCK:
                        task["info"]["segments_data"] = _sd_sc
                        task["info"]["translation_audits"] = _aud_sc
                        task["info"]["text_slot_fit_pre_tts"] = {
                            "skipped": "happy_path_review_sync",
                            "locked": len(segments),
                        }
                    logger.info(
                        "Task %s: final_tts_text re-locked pre-TTS (no post-Review fit)",
                        task_id,
                    )
                else:
                    # Pro/advanced may still reinforce fit (legacy path).
                    from engines.text_slot_fit import fit_text_to_slot

                    _tm_sc = list(current_timing_map_snapshot or [])
                    _sc_rows = []
                    for _i_sc, _txt_sc in enumerate(list(segments)):
                        if not str(_txt_sc or "").strip():
                            continue
                        _slot_sc = 0
                        if _i_sc < len(_tm_sc):
                            _it = _tm_sc[_i_sc]
                            if isinstance(_it, dict):
                                _slot_sc = max(
                                    0, int(_it.get("end", 0)) - int(_it.get("start", 0))
                                )
                            elif isinstance(_it, (list, tuple)) and len(_it) >= 2:
                                _slot_sc = max(0, int(_it[1]) - int(_it[0]))
                        if _slot_sc <= 0:
                            continue
                        _hint = (
                            source_segments_snapshot[_i_sc]
                            if _i_sc < len(source_segments_snapshot or [])
                            else ""
                        )
                        _fit = fit_text_to_slot(
                            str(_txt_sc),
                            _slot_sc,
                            str(target_lang or "uk"),
                            source_hint=str(_hint or ""),
                        )
                        if _fit.changed and _fit.text:
                            segments[_i_sc] = _fit.text
                            _sc_rows.append(_fit.to_dict())
                            if _i_sc < len(_sd_sc) and isinstance(_sd_sc[_i_sc], dict):
                                try:
                                    from engines.tts_text_authority import (
                                        stamp_final_tts_text,
                                    )

                                    stamp_final_tts_text(
                                        _sd_sc[_i_sc],
                                        _fit.text,
                                        source="text_slot_fit_pre_tts",
                                    )
                                except Exception:
                                    _sd_sc[_i_sc]["text"] = _fit.text
                                    _sd_sc[_i_sc]["final_tts_text"] = _fit.text
                    if _sc_rows:
                        with STATE_LOCK:
                            task["info"]["text_slot_fit_pre_tts"] = {
                                "changed": len(_sc_rows),
                                "rows": _sc_rows[:40],
                            }
                            task["info"]["segments_data"] = _sd_sc
            except Exception as _sc_exc:
                logger.debug("text_slot_fit pre-TTS skipped: %s", _sc_exc)
            _engine_meta = {
                "segments": len(_dub_engine_results),
                "adapted": sum(
                    1 for r in _dub_engine_results
                    if r.recommended_strategy not in ("direct", "skip_tts")
                ),
                "skipped": sum(1 for r in _dub_engine_results if not r.passed_validation),
                "strategies": {
                    s: sum(1 for r in _dub_engine_results
                           if r.recommended_strategy == s)
                    for s in ("direct", "adapted", "video_adapt",
                               "merge_next", "skip_tts", "delay_start")
                },
                "video_adapt_segments": [
                    r.index for r in _dub_engine_results
                    if r.recommended_strategy == "video_adapt"
                ],
            }
            with STATE_LOCK:
                task["info"]["dubbing_engine"] = _engine_meta
                task["info"]["adaptive_dubbing_adapter"] = _engine_meta  # compat alias
                # Sync engine output → translation_audits so UI matches TTS
                # (skipped when Review froze Final — do not overwrite approved text)
                _audits_now = task["info"].get("translation_audits") or []
                _audit_by_idx_e = {int(a.get("index", -1)): a for a in _audits_now}
                if not _review_freeze_engine:
                    from engines.pipeline_integrity.identity_guard import (
                        resolve_row_by_identity,
                        run_identity_guard,
                    )
                    try:
                        from engines.pipeline_integrity.revision_manager import (
                            note_text_change,
                        )
                    except Exception:
                        note_text_change = None  # type: ignore[assignment]
                    _sd_now = task["info"].get("segments_data") or []
                    for r in _dub_engine_results:
                        _orig_e = (_segments_before_engine[r.index]
                                   if r.index < len(_segments_before_engine) else "")
                        _out_e = (
                            str(r.output_text or "").strip()
                            if r.passed_validation and str(r.output_text or "").strip()
                            else _orig_e
                        )
                        if _out_e and _out_e != _orig_e:
                            _row_e = _audit_by_idx_e.get(r.index)
                            if _row_e is not None:
                                try:
                                    from engines.stress_marks import strip_stress_marks

                                    _out_clean = strip_stress_marks(_out_e)
                                except Exception:
                                    _out_clean = _out_e
                                _row_e["final_text"] = _out_clean
                                _row_e["tts_text"] = _out_clean
                                _row_e["dubbing_engine_strategy"] = r.recommended_strategy
                                _row_e["semantic_adapted"] = True
                        if _out_e:
                            try:
                                from engines.stress_marks import strip_stress_marks

                                _out_clean = strip_stress_marks(_out_e)
                            except Exception:
                                _out_clean = _out_e
                            _sid_r = str(getattr(r, "segment_id", "") or "").strip()
                            _tgt, _how = resolve_row_by_identity(
                                _sd_now,
                                segment_id=_sid_r,
                                index=None if _sid_r else r.index,
                            )
                            if _tgt is None and not _sid_r:
                                _tgt, _how = resolve_row_by_identity(
                                    _sd_now, segment_id="", index=r.index
                                )
                            if _tgt is None:
                                logger.warning(
                                    "IdentityGuard: dub engine result not bound "
                                    "sid=%s index=%s",
                                    _sid_r,
                                    r.index,
                                )
                            else:
                                if _how == "index":
                                    logger.warning(
                                        "IdentityGuard: engine result missing "
                                        "segment_id; index fallback index=%s",
                                        r.index,
                                    )
                                _old_txt = str(
                                    _tgt.get("plain_text") or _tgt.get("text") or ""
                                ).strip()
                                if note_text_change is not None:
                                    try:
                                        note_text_change(
                                            _tgt, _out_clean, kind="adaptation"
                                        )
                                    except Exception:
                                        pass
                                _tgt["text"] = _out_clean
                                _tgt["plain_text"] = _out_clean
                                _tgt["translation_text"] = _out_clean
                                _tgt["final_text"] = _out_clean
                                _tgt["tts_text"] = _out_clean
                                if _out_clean.strip() != _old_txt:
                                    try:
                                        from engines.pipeline_integrity.uuid_chain import (
                                            ensure_tts_uuid,
                                        )

                                        ensure_tts_uuid(_tgt, force_new=True)
                                    except Exception:
                                        pass
                    try:
                        run_identity_guard(
                            _sd_now,
                            stage="post_adapt",
                            task_info=task["info"],
                            require_wav=False,
                        )
                    except Exception as _ig_ad_exc:
                        logger.warning(
                            "IdentityGuard post_adapt skipped: %s", _ig_ad_exc
                        )
                else:
                    for r in _dub_engine_results:
                        _row_e = _audit_by_idx_e.get(r.index)
                        if _row_e is not None:
                            _row_e["dubbing_engine_strategy"] = r.recommended_strategy
                task["info"]["translation_audits"] = _audits_now
                from engines.translation_stage_log import log_translation_stage_batch

                log_translation_stage_batch(
                    task_id,
                    stage="post_length_control",
                    texts=[
                        str(_dub_engine_results[i].output_text or "")
                        for i in range(len(_dub_engine_results))
                    ],
                    target_lang=target_lang,
                    detail="dubbing_engine",
                )
            _update_progress_detail(
                task_id,
                phase="dubbing_engine",
                total_segments=len(segments),
                segments_done=_engine_meta["adapted"],
            )
            logger.info(
                "[DubEngine] Task %s: %d segs | %d adapted | %d skip_tts | %d video_adapt",
                task_id, _engine_meta["segments"], _engine_meta["adapted"],
                _engine_meta["skipped"], len(_engine_meta["video_adapt_segments"]),
            )
        except Exception as _engine_exc:
            logger.warning(
                "[DubEngine] engine error — falling back to raw segments: %s", _engine_exc,
            )
            _natural_pauses_for_timing = [160] * len(segments)

        # ── Legacy SSO path (kept for compatibility when DubEngine is disabled) ──
        # Happy Path: never run standalone SSO (advanced shortener).
        from engines.smart_segment_optimizer import is_enabled as sso_enabled
        from engines.smart_segment_optimizer import optimize_segments

        sso_meta: dict = {}
        sso_reports: list = []
        try:
            from engines.happy_path import advanced_adaptation_enabled as _adv_sso

            with STATE_LOCK:
                _info_sso = dict(task.get("info") or {})
            _sso_advanced = _adv_sso(_info_sso)
        except Exception:
            _sso_advanced = False
        # DubbingEngine already includes SSO internally; run standalone only if engine failed
        sso_active = (
            _sso_advanced
            and sso_enabled()
            and not skip_tts
            and _dub_engine_results is None
        )
        if sso_active:
            segments, sso_reports, sso_meta = optimize_segments(
                segments,
                current_timing_map_snapshot,
                source_segments=source_segments_snapshot,
                tgt_lang=target_lang,
                src_lang=translation_source_lang,
                app_dir=APP_DIR,
                task_id=task_id,
            )
            _update_progress_detail(
                task_id,
                phase="smart_segment_optimizer",
                total_segments=len(segments),
                segments_done=sso_meta.get("changed", 0),
            )

        # ── Adaptive Dubbing Adapter ──────────────────────────────────────────
        # Sits between Natural Translation / SSO and TTS synthesis.
        # Applies the 5-step decision tree (predict → reframe → synonyms →
        # word order → remove secondary) to prepare text BEFORE TTS is called.
        # No audio cutting / stretching happens here — only text adjustment.
        #
        # CRITICAL: after adaptation, translation_audits[i]["final_text"] MUST
        # be updated to the adapted text so the TTS trace shows "OK" (no MISMATCH)
        # and the UI reflects exactly what TTS will receive.
        # ── ADA legacy path (only when DubbingEngine failed + advanced ON) ────
        if _dub_engine_results is None and _sso_advanced:
            try:
                from engines.adaptive_dubbing_adapter import adapt_segments_for_tts as _ada_adapt
                _segments_before_ada = list(segments)
                segments, ada_meta = _ada_adapt(
                    segments,
                    timing_map=current_timing_map_snapshot,
                    lang=target_lang,
                    source_hints=source_segments_snapshot,
                    app_dir=APP_DIR,
                    task_id=task_id,
                )
                with STATE_LOCK:
                    task["info"]["adaptive_dubbing_adapter"] = ada_meta
                    _audits_fb = task["info"].get("translation_audits") or []
                    _audit_fb_idx = {int(a.get("index", -1)): a for a in _audits_fb}
                    for _i_fb, _adapted_fb in enumerate(segments):
                        _orig_fb = _segments_before_ada[_i_fb] if _i_fb < len(_segments_before_ada) else ""
                        if _adapted_fb and _adapted_fb != _orig_fb:
                            _row_fb = _audit_fb_idx.get(_i_fb)
                            if _row_fb is not None:
                                _row_fb["final_text"] = _adapted_fb
                                _row_fb["tts_text"] = _adapted_fb
                    task["info"]["translation_audits"] = _audits_fb
            except Exception as _ada_exc:
                logger.warning("[ADA] legacy adapter skipped: %s", _ada_exc)

        if _dub_engine_results is None:
            try:
                from engines.text_preparation import prepare_segments_for_tts

                segments, prep_meta = prepare_segments_for_tts(
                    segments,
                    lang=target_lang,
                    tts_engine_id=tts_engine_id,
                    app_dir=APP_DIR,
                    task_id=task_id,
                )
                with STATE_LOCK:
                    task["info"]["text_preparation"] = prep_meta
                    if sso_active:
                        task["info"]["smart_segment_optimizer"] = sso_meta
                        audits = task["info"].get("translation_audits") or []
                        audit_by_idx = {int(a.get("index", -1)): a for a in audits}
                        for rep in sso_reports:
                            row = audit_by_idx.get(rep.index)
                            if not row:
                                continue
                            if rep.changed:
                                row["sso_optimized"] = rep.optimized
                                row["sso_level"] = rep.level_used
                            row["sso_skip_reason"] = rep.skip_reason
                        task["info"]["translation_audits"] = audits
            except Exception as _prep_exc:
                logger.debug("[DubEngine] text_preparation skipped: %s", _prep_exc)

        _wtm_record_checkpoint(wtm_cp_log, task_id, "post_dubbing_engine")

        # Sentence & word integrity gate (TЗ §3/§4/§6): the LAST guard before
        # TTS. No segment may be empty, NULL, space-only, cut mid-word, or an
        # unfinished sentence. Broken text is reverted to the fullest COMPLETE
        # translation instead (never clipped, never empty).
        if not skip_tts:
            try:
                from engines.sentence_integrity import enforce_pre_tts_integrity

                _integrity_audits = task["info"].get("translation_audits") or []
                _integrity_src = task["info"].get("source_segments") or []
                _integrity_tgt = str(task["info"].get("target_lang") or "ru")
                segments, _integrity_report = enforce_pre_tts_integrity(
                    segments,
                    audits=_integrity_audits,
                    source_segments=_integrity_src,
                    target_lang=_integrity_tgt,
                )
                # Stamp healed text into segments_data + audits (avoid text_tts_mismatch)
                _fixed_idx = set(_integrity_report.get("fixed_indices") or [])
                if _fixed_idx:
                    try:
                        from engines.translation_validation import (
                            stamp_authoritative_final_text,
                        )

                        with STATE_LOCK:
                            _sd_int = list(task["info"].get("segments_data") or [])
                            _aud_int = list(task["info"].get("translation_audits") or [])
                        _aud_by = {
                            int(a.get("index", -1)): a
                            for a in _aud_int
                            if isinstance(a, dict)
                        }
                        for _fi in _fixed_idx:
                            if not (0 <= _fi < len(segments)):
                                continue
                            _ftxt = str(segments[_fi] or "").strip()
                            if not _ftxt:
                                continue
                            if 0 <= _fi < len(_sd_int) and isinstance(
                                _sd_int[_fi], dict
                            ):
                                stamp_authoritative_final_text(
                                    _sd_int[_fi],
                                    _ftxt,
                                    audit=_aud_by.get(_fi),
                                    preserve_semantic_engine=True,
                                )
                        with STATE_LOCK:
                            task["info"]["segments_data"] = _sd_int
                            task["info"]["translation_audits"] = _aud_int
                    except Exception as _stamp_exc:
                        logger.debug(
                            "[Integrity] stamp after heal skipped: %s", _stamp_exc
                        )
                with STATE_LOCK:
                    task["info"]["pre_tts_integrity"] = _integrity_report
                if _integrity_report.get("fixed"):
                    logger.warning(
                        "[Integrity] task %s: repaired %d broken pre-TTS segment(s): %s",
                        task_id,
                        _integrity_report["fixed"],
                        _integrity_report["fixed_indices"][:20],
                    )
            except Exception as _integ_exc:
                logger.warning("[Integrity] pre-TTS integrity gate skipped: %s", _integ_exc)

        # AI Adaptation hard gate (P0 §1/§2): NEVER send to TTS when a segment
        # requires LLM adaptation but LLM was not called. This is a critical
        # error — the pipeline MUST stop, not continue silently.
        if not skip_tts:
            try:
                from engines.ai_adaptation_engine import enforce_adaptation_gate
                from engines.translation_adapt import get_llm_calls, get_llm_status

                _gate_records = task["info"].get("timing_aware_records") or []
                _gate_segs_data = task["info"].get("segments_data") or []
                _gate_result = enforce_adaptation_gate(
                    segments,
                    timing_records=_gate_records,
                    llm_status=get_llm_status(),
                    segments_data=_gate_segs_data,
                    llm_calls=get_llm_calls(),
                )
                with STATE_LOCK:
                    task["info"]["adaptation_gate"] = _gate_result.to_dict()
                if not _gate_result.passed:
                    from engines.core.feature_flags import IS_DEBUG_LEARNING_MODE

                    _violations = _gate_result.violations
                    _vi_summary = "; ".join(
                        f"#{v['index'] + 1}:{v.get('reason', '')}" for v in _violations[:10]
                    )
                    if IS_DEBUG_LEARNING_MODE():
                        logger.warning(
                            "[AdaptGate] task %s: %d LLM skip(s) — debug mode, continuing: %s",
                            task_id,
                            len(_violations),
                            _vi_summary,
                        )
                    else:
                        from engines.dubbing_engine.pipeline_failure_diag import fail_pipeline

                        logger.error(
                            "[AdaptGate] task %s BLOCKED: %d segment(s) require LLM but LLM not called: %s",
                            task_id,
                            len(_violations),
                            _vi_summary,
                        )
                        fail_pipeline(
                            task_id,
                            f"AI Adaptation Engine: {len(_violations)} сегмент(ов) требуют "
                            f"интеллектуальной адаптации, но LLM не была вызвана ({_vi_summary})",
                            stage="AI Adaptation Gate",
                            error_code="LLM_NOT_CALLED",
                            ui_lang=ui_lang,
                        )
                        return
            except Exception as _gate_exc:
                if "LLM_NOT_CALLED" in str(_gate_exc):
                    raise
                logger.warning("[AdaptGate] pre-TTS adaptation gate skipped: %s", _gate_exc)

        _update_progress_detail(
            task_id,
            total_segments=len(segments),
            phase="text_preparation",
        )

        if skip_tts:
            if not _ensure_control(task_id, ui_lang):
                return
            from engines.dub_style_presets import build_subtitle_segments
            from engines.subtitle_formats import export_srt

            video_stem = Path(video_path).stem
            srt_name = f"{video_stem}_SUBS_{base_id}.srt"
            sub_segs = build_subtitle_segments(segments, current_timing_map_snapshot)
            (OUTPUT_DIR / srt_name).write_text(
                export_srt(sub_segs), encoding="utf-8"
            )
            with STATE_LOCK:
                task["info"]["subtitle_file"] = srt_name
                task["info"]["segments_data"] = _segments_data_entries(
                    segments, task["info"]
                )
                task["info"]["total_segments"] = len(segments)
            _wtm_record_checkpoint(wtm_cp_log, task_id, "pre_tts")
            logger.info(
                "Task %s: subtitles_only — SRT %s (no TTS)",
                task_id,
                srt_name,
            )
        else:
            if not _ensure_control(task_id, ui_lang):
                return
            from engines.pipeline_stage_flow import (
                log_stage_begin,
                log_stage_end,
                log_stage_transition,
            )

            log_stage_transition(task_id, "TRANSLATION", "TTS_PREP")
            log_stage_begin(task_id, "TTS_PREP")

            with STATE_LOCK:
                segments_data = _segments_data_entries(segments, task["info"])
                _pi = _integrity_coordinator(task_id)
                _pi.assign_segment_ids(segments_data)
                if project_session:
                    project_session.set_segments(segments_data)
                    _pi.initialize_guard_context(
                        project_session=project_session,
                        segments_data=segments_data,
                    )
                task["info"]["segments_data"] = segments_data
                task["info"]["total_segments"] = len(segments)

            with STATE_LOCK:
                _vp_manifest = (task.get("info") or {}).get("manifest_path")
                _vp_segments = list(task["info"].get("segments_data") or [])
            if _vp_manifest and Path(_vp_manifest).is_file() and _vp_segments:
                _vp_orch = _run_ai_core_orchestrator(
                    task_id,
                    video_path,
                    _vp_manifest,
                    {
                        "segments": _build_orchestrator_agent_segments(
                            task_id, _vp_segments
                        ),
                    },
                    agents=["voice_preparation"],
                )
                if _vp_orch.warnings:
                    logger.debug(
                        "Task %s: voice_preparation warnings: %s",
                        task_id,
                        _vp_orch.warnings,
                    )

            from engines.pipeline_language_gate import (
                log_segment_pipeline_trace,
                validate_segments_target_language,
            )

            pre_tts_rows = [
                {"text": s, "plain_text": s}
                if isinstance(s, str)
                else {
                    "text": str(s.get("text") or s.get("plain_text") or ""),
                    "plain_text": str(s.get("plain_text") or s.get("text") or ""),
                }
                for s in segments
            ]
            log_segment_pipeline_trace(
                task_id,
                pre_tts_rows,
                source_segments=source_segments_snapshot,
                target_lang=target_lang,
                audits=task.get("info", {}).get("translation_audits"),
            )
            pre_tts_lang_issues = validate_segments_target_language(
                pre_tts_rows,
                source_segments=source_segments_snapshot,
                target_lang=target_lang,
                source_lang=translation_source_lang,
                stage="PRE_TTS",
            )
            if pre_tts_lang_issues:
                # Unified recovery (deflate / naturalizer / salvage) before legacy path.
                try:
                    from engines.language_validation.recovery import (
                        apply_recovery_and_revalidate,
                    )
                    from engines.language_validation.diagnostics import (
                        write_language_validation_diagnostics,
                    )

                    with STATE_LOCK:
                        _sd_pre = list(task["info"].get("segments_data") or [])
                    if _sd_pre:
                        _pre_rec = apply_recovery_and_revalidate(
                            _sd_pre,
                            source_segments=source_segments_snapshot,
                            target_lang=target_lang,
                            source_lang=translation_source_lang,
                            stage="PRE_TTS",
                        )
                        write_language_validation_diagnostics(
                            task_id=task_id,
                            app_dir=APP_DIR,
                            stage="PRE_TTS",
                            decisions=_pre_rec.get("decisions") or [],
                            recovery=_pre_rec,
                        )
                        with STATE_LOCK:
                            task["info"]["segments_data"] = _sd_pre
                            task["info"]["language_recovery_pre_tts"] = {
                                "healed": _pre_rec.get("healed_indices"),
                                "recovered": _pre_rec.get("recovered"),
                                "failed_hard": _pre_rec.get("failed_hard"),
                            }
                        segments = [
                            str(s.get("text") or s.get("plain_text") or "").strip()
                            if isinstance(s, dict)
                            else str(s or "")
                            for s in _sd_pre
                        ]
                        pre_tts_rows = [
                            {
                                "text": str(
                                    s.get("text") or s.get("plain_text") or ""
                                ),
                                "plain_text": str(
                                    s.get("plain_text") or s.get("text") or ""
                                ),
                                "segment_id": s.get("segment_id"),
                            }
                            for s in _sd_pre
                            if isinstance(s, dict)
                        ]
                        pre_tts_lang_issues = validate_segments_target_language(
                            pre_tts_rows,
                            source_segments=source_segments_snapshot,
                            target_lang=target_lang,
                            source_lang=translation_source_lang,
                            stage="PRE_TTS",
                        )
                        # Drop soft issues that match target language (TZ: no false LM).
                        pre_tts_lang_issues = [
                            i
                            for i in pre_tts_lang_issues
                            if i.get("hard_fail")
                            or i.get("category") == "language_mismatch"
                            or (
                                i.get("category") == "meaning_collapse"
                                and any(
                                    "critical_cue" in str(r) or "pregnancy" in str(r)
                                    for r in (i.get("reasons") or [])
                                )
                            )
                        ]
                except Exception as _pre_rec_exc:
                    logger.warning(
                        "Task %s: unified pre-TTS language recovery skipped: %s",
                        task_id,
                        _pre_rec_exc,
                    )

            if pre_tts_lang_issues:
                from engines.core.feature_flags import IS_DEBUG_LEARNING_MODE
                from engines.translation_validation import (
                    build_validation_rows_from_info,
                    recover_mismatched_segments,
                    sync_final_text_to_task_info,
                    write_translation_validation_json,
                )

                with STATE_LOCK:
                    _info_rec = task["info"]
                    recovered, still_bad = recover_mismatched_segments(
                        _info_rec,
                        pre_tts_lang_issues,
                        task_id=task_id,
                        source_lang=translation_source_lang,
                        target_lang=target_lang,
                    )
                    sync_final_text_to_task_info(_info_rec)
                    try:
                        validation_rows = build_validation_rows_from_info(_info_rec)
                        write_translation_validation_json(
                            task_id,
                            validation_rows,
                            project_uuid=str(_info_rec.get("project_uuid") or ""),
                            app_dir=APP_DIR,
                        )
                    except Exception:
                        pass
                    if recovered:
                        segments = [
                            str(s.get("text") or s.get("plain_text") or "").strip()
                            for s in (_info_rec.get("segments_data") or [])
                        ]
                        pre_tts_rows = [
                            {"text": s, "plain_text": s} for s in segments if s
                        ] or pre_tts_rows
                        pre_tts_lang_issues = validate_segments_target_language(
                            pre_tts_rows,
                            source_segments=source_segments_snapshot,
                            target_lang=target_lang,
                            source_lang=translation_source_lang,
                        )
                        still_bad = pre_tts_lang_issues

                if still_bad:
                    first = still_bad[0]
                    from engines.dubbing_engine.pipeline_failure_diag import fail_pipeline
                    from engines.pipeline_language_gate import (
                        build_language_mismatch_report,
                    )

                    # TZ §4/§8: build a full per-stage RCA report so the failure is
                    # never just "LANGUAGE_MISMATCH" without an explanation.
                    _audits = task.get("info", {}).get("translation_audits") or []
                    _audit_by_idx = {int(a.get("index", -1)): a for a in _audits}
                    mismatch_reports = []
                    for _issue in still_bad:
                        _idx = int(_issue.get("index", -1))
                        _seg = (
                            segments[_idx]
                            if 0 <= _idx < len(segments) and isinstance(segments[_idx], dict)
                            else {}
                        )
                        if not _seg and 0 <= _idx < len(pre_tts_rows):
                            _seg = pre_tts_rows[_idx]
                        _orig = (
                            source_segments_snapshot[_idx]
                            if 0 <= _idx < len(source_segments_snapshot)
                            else ""
                        )
                        mismatch_reports.append(
                            build_language_mismatch_report(
                                index=_idx,
                                segment=_seg,
                                audit=_audit_by_idx.get(_idx),
                                original=_orig,
                                target_lang=target_lang,
                            )
                        )
                    with STATE_LOCK:
                        task["info"]["language_mismatch_reports"] = mismatch_reports
                    first_report = mismatch_reports[0] if mismatch_reports else {}
                    diagnosis = str(first_report.get("diagnosis") or "")
                    seg_msg = str(first.get("message") or first.get("code") or "")

                    reason = (
                        f"Критическая ошибка языка до TTS: сегмент #{first.get('index')} "
                        f"({first.get('code')}) — ожидался {target_lang}, "
                        f"обнаружен {first.get('detected_lang', 'en')}. "
                        f"Превью: {str(first.get('final_preview') or '')[:120]}. "
                        f"{seg_msg}. RCA: {diagnosis}"
                    )
                    if IS_DEBUG_LEARNING_MODE():
                        _collapse_codes = {
                            "meaning_collapse",
                            "cjk_meaning_collapse",
                            "source_script_leak",
                            "cjk_in_uk_track",
                            "cjk_in_ru_track",
                            "cjk_residue_in_uk_track",
                            "cjk_residue_in_ru_track",
                            "cjk_residue_in_be_track",
                            "phrase_loop",
                        }
                        _hard = [
                            x
                            for x in still_bad
                            if str(x.get("code") or "") in _collapse_codes
                            or str(x.get("code") or "").startswith("source_script_leak_")
                            or str(x.get("code") or "").startswith("cjk_residue_")
                        ]
                        if _hard:
                            from engines.pipeline_language_gate import (
                                salvage_collapsed_segment_text,
                            )

                            _salvaged_idx: set[int] = set()
                            with STATE_LOCK:
                                _sd_sal = task["info"].get("segments_data") or []
                                _aud_sal = {
                                    int(a.get("index", -1)): a
                                    for a in (
                                        task["info"].get("translation_audits") or []
                                    )
                                }
                            for _iss in list(_hard):
                                _si = int(_iss.get("index", -1))
                                if _si < 0:
                                    continue
                                _orig_s = (
                                    source_segments_snapshot[_si]
                                    if 0 <= _si < len(source_segments_snapshot)
                                    else ""
                                )
                                _cur = ""
                                if 0 <= _si < len(segments):
                                    _cur = (
                                        segments[_si]
                                        if isinstance(segments[_si], str)
                                        else str(
                                            (segments[_si] or {}).get("text") or ""
                                        )
                                    )
                                _approved = ""
                                if 0 <= _si < len(_sd_sal) and isinstance(
                                    _sd_sal[_si], dict
                                ):
                                    _approved = str(
                                        _sd_sal[_si].get("approved_text") or ""
                                    )
                                    if not _cur:
                                        _cur = str(
                                            _sd_sal[_si].get("text")
                                            or _sd_sal[_si].get(
                                                "rejected_translation"
                                            )
                                            or ""
                                        )
                                _aud_row = _aud_sal.get(_si) or {}
                                if not _approved:
                                    _approved = str(
                                        _aud_row.get("approved_text")
                                        or _aud_row.get("final_text")
                                        or ""
                                    )
                                _fix, _method = salvage_collapsed_segment_text(
                                    text=_cur,
                                    original=_orig_s,
                                    approved=_approved,
                                    target_lang=target_lang,
                                    source_lang=translation_source_lang,
                                )
                                if not _fix:
                                    continue
                                _salvaged_idx.add(_si)
                                if 0 <= _si < len(segments):
                                    segments[_si] = _fix
                                with STATE_LOCK:
                                    _sdh = task["info"].get("segments_data") or []
                                    if 0 <= _si < len(_sdh) and isinstance(
                                        _sdh[_si], dict
                                    ):
                                        for _kh in (
                                            "text",
                                            "plain_text",
                                            "translation_text",
                                            "final_text",
                                            "text_for_tts",
                                            "approved_text",
                                            "voice_input",
                                        ):
                                            _sdh[_si][_kh] = _fix
                                        _sdh[_si]["tts_blocked"] = False
                                        _sdh[_si]["skip_tts"] = False
                                        _sdh[_si]["salvage_method"] = _method
                                        _sdh[_si].pop("needs_manual_review", None)
                                logger.info(
                                    "Task %s: salvaged collapse seg #%s via %s",
                                    task_id,
                                    _si,
                                    _method,
                                )
                            _hard = [
                                x
                                for x in _hard
                                if int(x.get("index", -1)) not in _salvaged_idx
                            ]
                            if not _hard:
                                still_bad = [
                                    x
                                    for x in still_bad
                                    if int(x.get("index", -1)) not in _salvaged_idx
                                ]
                                if not still_bad:
                                    logger.info(
                                        "Task %s: all LANGUAGE_MISMATCH segments "
                                        "salvaged — continuing",
                                        task_id,
                                    )
                            if _hard:
                                logger.warning(
                                    "Task %s: LANGUAGE_MISMATCH collapse in debug — "
                                    "blanking %d seg(s), no TTS",
                                    task_id,
                                    len(_hard),
                                )
                                _hard_idx = {int(x.get("index", -1)) for x in _hard}
                                segments = [
                                    "" if i in _hard_idx else s
                                    for i, s in enumerate(segments)
                                ]
                                with STATE_LOCK:
                                    for _hi in _hard_idx:
                                        _sdh = task["info"].get("segments_data") or []
                                        if 0 <= _hi < len(_sdh) and isinstance(
                                            _sdh[_hi], dict
                                        ):
                                            for _kh in (
                                                "text",
                                                "plain_text",
                                                "translation_text",
                                                "final_text",
                                                "text_for_tts",
                                                "approved_text",
                                                "voice_input",
                                                "semantic_text",
                                                "semantic_engine_text",
                                                "grammar_text",
                                                "timing_text",
                                            ):
                                                _sdh[_hi][_kh] = ""
                                            _sdh[_hi]["tts_blocked"] = True
                                            _sdh[_hi]["skip_tts"] = True
                                            _sdh[_hi]["needs_manual_review"] = True
                                            if "FAIL" not in str(
                                                _sdh[_hi].get("tqe_status") or ""
                                            ).upper():
                                                _sdh[_hi][
                                                    "tqe_status"
                                                ] = "FAIL_MANUAL_REVIEW"
                                    task["info"]["language_mismatch_blanked"] = sorted(
                                        _hard_idx
                                    )
                                _left = [s for s in segments if str(s or "").strip()]
                                if not _left:
                                    # All blocked after salvage — never brick the job:
                                    # fall back to subtitles_only (SRT + original audio).
                                    _export_subtitles_only_fallback(
                                        task_id,
                                        video_path=video_path,
                                        base_id=base_id,
                                        segments=segments,
                                        timing_map=current_timing_map_snapshot,
                                        reason=(
                                            "LANGUAGE_MISMATCH (meaning_collapse) — "
                                            "все сегменты заблокированы после salvage; "
                                            "subtitles_only fallback."
                                        ),
                                    )
                                    skip_tts = True
                                    logger.error(
                                        "Task %s: all segments blocked after salvage — "
                                        "subtitles_only fallback (no nonsense TTS)",
                                        task_id,
                                    )
                        else:
                            logger.warning(
                                "Task %s: LANGUAGE_MISMATCH after recovery — debug mode continues: %s",
                                task_id,
                                reason,
                            )
                    else:
                        from engines.pipeline_language_gate import (
                            salvage_collapsed_segment_text,
                        )

                        _prod_salvaged = 0
                        with STATE_LOCK:
                            _sd_sal = task["info"].get("segments_data") or []
                        for _iss in list(still_bad):
                            _si = int(_iss.get("index", -1))
                            if _si < 0:
                                continue
                            _orig_s = (
                                source_segments_snapshot[_si]
                                if 0 <= _si < len(source_segments_snapshot)
                                else ""
                            )
                            _cur = ""
                            if 0 <= _si < len(segments):
                                _cur = (
                                    segments[_si]
                                    if isinstance(segments[_si], str)
                                    else str((segments[_si] or {}).get("text") or "")
                                )
                            _approved = ""
                            if 0 <= _si < len(_sd_sal) and isinstance(
                                _sd_sal[_si], dict
                            ):
                                _approved = str(_sd_sal[_si].get("approved_text") or "")
                            _fix, _method = salvage_collapsed_segment_text(
                                text=_cur,
                                original=_orig_s,
                                approved=_approved,
                                target_lang=target_lang,
                                source_lang=translation_source_lang,
                            )
                            if not _fix:
                                continue
                            _prod_salvaged += 1
                            if 0 <= _si < len(segments):
                                segments[_si] = _fix
                            with STATE_LOCK:
                                _sdh = task["info"].get("segments_data") or []
                                if 0 <= _si < len(_sdh) and isinstance(
                                    _sdh[_si], dict
                                ):
                                    for _kh in (
                                        "text",
                                        "plain_text",
                                        "translation_text",
                                        "final_text",
                                        "text_for_tts",
                                        "approved_text",
                                        "voice_input",
                                    ):
                                        _sdh[_si][_kh] = _fix
                                    _sdh[_si]["tts_blocked"] = False
                                    _sdh[_si]["skip_tts"] = False
                                    _sdh[_si]["salvage_method"] = _method
                        if _prod_salvaged:
                            pre_tts_rows = [
                                {"text": s, "plain_text": s}
                                if isinstance(s, str)
                                else {
                                    "text": str(
                                        s.get("text") or s.get("plain_text") or ""
                                    ),
                                    "plain_text": str(
                                        s.get("plain_text") or s.get("text") or ""
                                    ),
                                }
                                for s in segments
                            ]
                            still_bad = validate_segments_target_language(
                                pre_tts_rows,
                                source_segments=source_segments_snapshot,
                                target_lang=target_lang,
                                source_lang=translation_source_lang,
                            )
                            logger.info(
                                "Task %s: production salvage fixed %d seg(s), remaining=%d",
                                task_id,
                                _prod_salvaged,
                                len(still_bad),
                            )
                        if still_bad:
                            logger.error(
                                "Task %s: pre-TTS language gate — %d issues after recovery, idx=%s code=%s | RCA: %s",
                                task_id,
                                len(still_bad),
                                first.get("index"),
                                first.get("code"),
                                diagnosis,
                            )
                            for _rep in mismatch_reports:
                                logger.error(
                                    "Task %s: LANGUAGE_MISMATCH chain idx=%s → %s",
                                    task_id,
                                    _rep.get("index"),
                                    _rep.get("transformation_chain"),
                                )
                            # Partial salvage: blank blocked, TTS clean rows.
                            # If nothing voiceable remains → subtitles_only (never hard-fail).
                            _block_idx = {
                                int(x.get("index", -1))
                                for x in still_bad
                                if int(x.get("index", -1)) >= 0
                            }
                            segments, _voiceable = _blank_language_gate_segments(
                                task_id,
                                segments,
                                _block_idx,
                                preserve_for_subtitles=True,
                            )
                            if _voiceable > 0:
                                logger.warning(
                                    "Task %s: LANGUAGE_MISMATCH partial salvage — "
                                    "blanked %d blocked, continuing TTS for %d clean",
                                    task_id,
                                    len(_block_idx),
                                    _voiceable,
                                )
                            else:
                                _export_subtitles_only_fallback(
                                    task_id,
                                    video_path=video_path,
                                    base_id=base_id,
                                    segments=segments,
                                    timing_map=current_timing_map_snapshot,
                                    reason=reason,
                                )
                                skip_tts = True
                                logger.error(
                                    "Task %s: LANGUAGE_MISMATCH — all blocked after "
                                    "salvage; subtitles_only fallback (no hard-fail)",
                                    task_id,
                                )
                        else:
                            logger.info(
                                "Task %s: LANGUAGE_MISMATCH fully salvaged in production — continuing",
                                task_id,
                            )
                elif recovered:
                    logger.info(
                        "Task %s: pre-TTS language recovery fixed %d segment(s)",
                        task_id,
                        recovered,
                    )

            _wtm_record_checkpoint(wtm_cp_log, task_id, "pre_tts")

            # ── Translation Quality Engine (TQE) — hard gate before TTS ──
            # Skip only when TPS actually PASSed segments (approved_text set).
            # FAIL_MANUAL / tts_blocked must NOT skip — that was shipping flower MT.
            if not skip_tts:
                with STATE_LOCK:
                    _info_tps = task.get("info") or {}
                    _tps_already = bool(_info_tps.get("tps"))
                    _sd_gate = list(_info_tps.get("segments_data") or [])
                    _manual_or_blocked = any(
                        isinstance(s, dict)
                        and (
                            s.get("tts_blocked")
                            or s.get("skip_tts")
                            or s.get("needs_manual_review")
                            or "FAIL" in str(s.get("tqe_status") or "").upper()
                            or (
                                not str(s.get("approved_text") or "").strip()
                                and str(s.get("tps_path") or "") == "manual"
                            )
                        )
                        for s in _sd_gate
                    )
                if _tps_already and not _manual_or_blocked:
                    logger.info(
                        "[TQE] task %s: skipped hard gate — TPS approved",
                        task_id,
                    )
                    with STATE_LOCK:
                        task["info"]["tqe"] = {
                            "skipped": "tps",
                            "gate_passed": True,
                        }
                elif _tps_already and _manual_or_blocked:
                    # Blank blocked segments; do not synthesize hallucinations
                    logger.warning(
                        "[TQE] task %s: TPS had manual/blocked segments — blanking TTS texts",
                        task_id,
                    )
                    with STATE_LOCK:
                        _sd_b = task["info"].get("segments_data") or []
                        for _ib, _sb in enumerate(_sd_b):
                            if not isinstance(_sb, dict):
                                continue
                            if (
                                _sb.get("tts_blocked")
                                or _sb.get("skip_tts")
                                or _sb.get("needs_manual_review")
                                or "FAIL" in str(_sb.get("tqe_status") or "").upper()
                            ):
                                for _kb in (
                                    "text",
                                    "plain_text",
                                    "translation_text",
                                    "final_text",
                                    "text_for_tts",
                                    "semantic_text",
                                    "semantic_engine_text",
                                    "approved_text",
                                    "voice_input",
                                    "grammar_text",
                                    "timing_text",
                                ):
                                    _sb[_kb] = ""
                                _sb["tts_blocked"] = True
                                _sb["skip_tts"] = True
                                _sb["needs_manual_review"] = True
                                if "FAIL" not in str(_sb.get("tqe_status") or "").upper():
                                    _sb["tqe_status"] = "FAIL_MANUAL_REVIEW"
                                if _ib < len(segments):
                                    segments[_ib] = ""
                        task["info"]["tqe"] = {
                            "skipped": "tps_manual_blanked",
                            "gate_passed": False,
                            "blocked_blanked": True,
                        }
                    # All voiceable text gone → stop before TTS/Studio (clear QC fail)
                    _voiceable_left = [
                        s
                        for s in segments
                        if str(s or "").strip()
                    ]
                    with STATE_LOCK:
                        _sd_left = task["info"].get("segments_data") or []
                        _any_ok = any(
                            isinstance(s, dict)
                            and not (
                                s.get("tts_blocked")
                                or s.get("skip_tts")
                                or (
                                    "FAIL" in str(s.get("tqe_status") or "").upper()
                                    and not str(s.get("approved_text") or "").strip()
                                )
                            )
                            and str(
                                s.get("approved_text")
                                or s.get("text")
                                or s.get("final_text")
                                or ""
                            ).strip()
                            for s in _sd_left
                        )
                    if not _voiceable_left and not _any_ok:
                        # Never brick: export subtitles and continue with original audio.
                        _export_subtitles_only_fallback(
                            task_id,
                            video_path=video_path,
                            base_id=base_id,
                            segments=segments,
                            timing_map=current_timing_map_snapshot,
                            reason=(
                                "Перевод отклонён (meaning_collapse / manual review) — "
                                "озвучка запрещена; subtitles_only fallback."
                            ),
                        )
                        skip_tts = True
                        logger.error(
                            "[TQE] task %s: all voiceable blocked — subtitles_only fallback",
                            task_id,
                        )
                else:
                    try:
                        from engines.tqe import filter_tts_texts, run_tqe_gate

                        with STATE_LOCK:
                            _tqe_src = list(task["info"].get("source_segments") or [])
                            _tqe_timing = copy.deepcopy(
                                task["info"].get("timing_map_backup") or []
                            )
                            _tqe_audits = list(task["info"].get("translation_audits") or [])
                        # Prefer audit final_text when present
                        _tqe_texts = list(segments)
                        for _a in _tqe_audits:
                            try:
                                _ai = int(_a.get("index", -1))
                            except Exception:
                                continue
                            _ft = str(
                                _a.get("final_text")
                                or _a.get("semantic_text")
                                or _a.get("tts_text")
                                or ""
                            ).strip()
                            if 0 <= _ai < len(_tqe_texts) and _ft and not _ft.lstrip().startswith(
                                "<speak"
                            ):
                                _tqe_texts[_ai] = _ft

                        _tqe_result = run_tqe_gate(
                            task_id=task_id,
                            originals=_tqe_src or source_segments_snapshot,
                            translations=_tqe_texts,
                            timing_map=_tqe_timing or current_timing_map_snapshot,
                            app_dir=str(APP_DIR),
                            persist=True,
                            allow_retry=True,
                        )
                        with STATE_LOCK:
                            task["info"]["tqe"] = _tqe_result.to_dict()
                            task["info"]["tqe_explanations"] = list(_tqe_result.explanations)

                        if not _tqe_result.gate_passed:
                            from engines.core.feature_flags import IS_DEBUG_LEARNING_MODE
                            from engines.dubbing_engine.pipeline_failure_diag import fail_pipeline

                            _blocked = _tqe_result.blocked_indices[:12]
                            _why = "; ".join(
                                (_tqe_result.explanations[i].replace("\n", " | ")
                                 if i < len(_tqe_result.explanations) else f"#{i+1}")
                                for i in _blocked
                            )
                            _msg = (
                                f"TQE отклонил {_tqe_result.rejected} сегмент(ов) — "
                                f"TTS запрещён. {_why}"
                            )
                            if IS_DEBUG_LEARNING_MODE():
                                logger.warning(
                                    "[TQE] task %s: gate failed in debug mode — blanking rejected: %s",
                                    task_id,
                                    _msg[:500],
                                )
                                segments = filter_tts_texts(segments, _tqe_result, blank_rejected=True)
                                with STATE_LOCK:
                                    task["info"]["tqe_debug_continued"] = True
                            else:
                                logger.error("[TQE] task %s: %s", task_id, _msg[:800])
                                fail_pipeline(
                                    task_id,
                                    _msg[:1200],
                                    stage="TQE",
                                    error_code="TQE_REJECT",
                                )
                                return
                        else:
                            # Apply any retry-improved texts
                            segments = [
                                d.translation if d.allowed_for_tts else segments[d.index]
                                for d in _tqe_result.decisions
                            ] if _tqe_result.decisions else segments
                            while len(segments) < len(_tqe_texts):
                                segments.append(_tqe_texts[len(segments)])
                            logger.info(
                                "[TQE] task %s: PASS %d/%d avg_conf=%.3f",
                                task_id,
                                _tqe_result.passed,
                                _tqe_result.passed + _tqe_result.rejected,
                                _tqe_result.overall_confidence,
                            )
                    except Exception as _tqe_exc:
                        logger.warning("[TQE] gate skipped due to error: %s", _tqe_exc)
                        with STATE_LOCK:
                            task["info"]["tqe_error"] = str(_tqe_exc)

            try:
                from engines.core.feature_flags import is_enabled as _ff_enabled
                from engines.emotion_tagger import classify_segments, is_emotion_tts_enabled

                if _ff_enabled("emotion_tts", developer_session=True) or is_emotion_tts_enabled():
                    with STATE_LOCK:
                        _seg_rows = copy.deepcopy(task["info"].get("segments_data") or [])
                    tgt_texts = [str(s.get("text") or "") for s in _seg_rows]
                    with STATE_LOCK:
                        _src_audio_em = task["info"].get("original_audio_path") or audio_path
                    emotion_tags = classify_segments(
                        tgt_texts,
                        originals=source_segments_snapshot,
                        audio_paths=[_src_audio_em] * len(tgt_texts),
                        timing_map=current_timing_map_snapshot,
                    )
                    with STATE_LOCK:
                        task["info"]["emotion_tags"] = [t.to_dict() for t in emotion_tags]
                    for i, tag in enumerate(emotion_tags):
                        if i < len(segments_data):
                            segments_data[i]["emotion"] = tag.emotion
                            segments_data[i]["tts_emotion"] = tag.to_dict()
                            segments_data[i]["intonation"] = tag.intonation or {}
            except Exception:
                pass

            # Pre-TTS text hygiene (44.zip RCA): phrase-loop deflate, neighbor bleed
            # restore, sync tts_text↔text BEFORE first synthesis.
            try:
                from engines.pipeline_language_gate import heal_phrase_loops_in_segments
                from engines.tts_text_guard import repair_neighbor_bleed

                with STATE_LOCK:
                    _sd_pre_tts = list(task["info"].get("segments_data") or segments_data)
                _loop_pre = heal_phrase_loops_in_segments(
                    _sd_pre_tts,
                    source_segments=source_segments_snapshot,
                    target_lang=target_lang,
                    source_lang=translation_source_lang,
                )
                _bleed = repair_neighbor_bleed(
                    _sd_pre_tts,
                    source_segments=source_segments_snapshot,
                )
                if _loop_pre or _bleed.get("healed"):
                    logger.info(
                        "Task %s: pre-TTS text guard — loops=%s bleed=%s",
                        task_id,
                        _loop_pre,
                        _bleed.get("healed_indices"),
                    )
                    segments_data = _sd_pre_tts
                    segments = [
                        str(s.get("text") or s.get("plain_text") or "").strip()
                        if isinstance(s, dict)
                        else str(s or "")
                        for s in _sd_pre_tts
                    ]
                    with STATE_LOCK:
                        _aud_pre = task["info"].get("translation_audits") or []
                        _aby_pre = {
                            int(a.get("index", -1)): a
                            for a in _aud_pre
                            if isinstance(a, dict)
                        }
                        for _hi in list(_loop_pre or []):
                            if not (0 <= _hi < len(_sd_pre_tts)):
                                continue
                            _hs = _sd_pre_tts[_hi]
                            if not isinstance(_hs, dict):
                                continue
                            _ht = str(
                                _hs.get("text") or _hs.get("plain_text") or ""
                            ).strip()
                            _ar = _aby_pre.get(_hi)
                            if _ar is not None and _ht:
                                _ar["tts_text"] = _ht
                                _ar["final_text"] = _ht
                                _ar["naturalized_text"] = _ht
                                _ar["phrase_loop_healed"] = True
                        task["info"]["segments_data"] = _sd_pre_tts
                        task["info"]["pre_tts_text_guard"] = {
                            "phrase_loops": list(_loop_pre or []),
                            "bleed": _bleed,
                        }
            except Exception as _pre_tts_guard_exc:
                logger.warning(
                    "Task %s: pre-TTS text guard skipped: %s",
                    task_id,
                    _pre_tts_guard_exc,
                )

            from engines.translation_naturalizer import build_tts_groups
            from engines.tts import generate_audio, generate_tts_groups_parallel
            from engines.semantic_adaptation import prepare_tts_groups_semantic
            from engines.tts_text_path import (
                build_tts_trace_rows,
                find_mismatches,
                log_tts_trace,
            )

            with STATE_LOCK:
                review_before_tts = bool(
                    task["info"].get("translation_review_before_tts", True)
                )
                review_approved = bool(task["info"].get("translation_review_approved"))
                timing_aware_applied = bool(task["info"].get("timing_aware_applied"))
                translate_method_snap = task["info"].get("translate_method") or translate_method

            # Hard freeze: spoken TTS must equal Review Final after operator approval.
            if review_before_tts or review_approved:
                try:
                    from engines.tts_review_align import (
                        align_info_for_translation_review,
                        freeze_spoken_to_review_final,
                    )

                    with STATE_LOCK:
                        align_info_for_translation_review(task["info"])
                        _sd_fr = list(task["info"].get("segments_data") or segments_data)
                        _aud_fr = list(task["info"].get("translation_audits") or [])
                        segments = freeze_spoken_to_review_final(
                            segments,
                            _sd_fr,
                            _aud_fr,
                            source_segments=source_segments_snapshot,
                        )
                        task["info"]["segments_data"] = _sd_fr
                        task["info"]["translation_audits"] = _aud_fr
                        task["info"]["tts_frozen_to_review_final"] = True
                    segments_data = _sd_fr
                    logger.info(
                        "Task %s: TTS frozen to Review Final (%d segments)",
                        task_id,
                        len(segments),
                    )
                except Exception as _freeze_exc:
                    logger.warning(
                        "Task %s: TTS↔Review freeze skipped: %s",
                        task_id,
                        _freeze_exc,
                    )

            # Stage 4: after freeze/guards, force spoken buffers from fitted snapshot.
            try:
                from engines.happy_path import skip_advanced_text_shorteners as _hp_relock
                from engines.tts_text_authority import (
                    lock_segments_final_tts,
                    resolve_final_tts_text,
                )

                with STATE_LOCK:
                    _info_rl = dict(task.get("info") or {})
                    _sd_rl = list(task["info"].get("segments_data") or segments_data)
                    _aud_rl = list(task["info"].get("translation_audits") or [])
                    _fitted_rl = list(task["info"].get("fitted_tts_texts") or [])
                if _hp_relock(_info_rl) and (
                    _info_rl.get("final_tts_locked") or _fitted_rl
                ):
                    _texts_rl = []
                    for _i_rl, _seg_rl in enumerate(_sd_rl):
                        _t_rl = ""
                        if _i_rl < len(_fitted_rl):
                            _t_rl = str(_fitted_rl[_i_rl] or "").strip()
                        if not _t_rl and isinstance(_seg_rl, dict):
                            _t_rl = resolve_final_tts_text(_seg_rl)
                        if not _t_rl and _i_rl < len(segments):
                            _t_rl = str(segments[_i_rl] or "")
                        _texts_rl.append(_t_rl)
                    segments = lock_segments_final_tts(
                        _sd_rl,
                        _texts_rl,
                        audits=_aud_rl,
                        source="pre_tts_fitted_snapshot",
                    )
                    segments_data = _sd_rl
                    with STATE_LOCK:
                        task["info"]["segments_data"] = _sd_rl
                        task["info"]["translation_audits"] = _aud_rl
                        task["info"]["final_tts_relocked_pre_groups"] = True
                        task["info"]["fitted_tts_texts"] = list(_texts_rl)
                    logger.info(
                        "Task %s: fitted_tts_texts restored before TTS groups (%d)",
                        task_id,
                        len(_texts_rl),
                    )
            except Exception as _relock_exc:
                logger.debug(
                    "Task %s: pre-group final_tts relock skipped: %s",
                    task_id,
                    _relock_exc,
                )

            # After review / timing-aware pass, TTS uses approved Final without extra rewrite.
            adapt_tts_text = not (
                review_before_tts
                or review_approved
                or sso_active
                or timing_aware_applied
                or _skip_timing_shorten
                or _skip_grammar_shorten
            )

            # ── PSA4: SegmentNormalizer + SlotBudgetFirst BEFORE TTS groups ──
            _slot_budget_ok = True
            try:
                from engines.pipeline_integrity.slot_budget import (
                    prepare_slot_budget_before_tts,
                    segment_tts_allowed,
                )

                with STATE_LOCK:
                    _sb_info = task["info"]
                    _sb_tm = list(
                        _sb_info.get("timing_map_backup")
                        or current_timing_map_snapshot
                        or []
                    )
                    _src_lang_sb = str(
                        _sb_info.get("detected_lang")
                        or _sb_info.get("source_lang")
                        or "en"
                    )
                segments_data, _sb_tm, _sb_report = prepare_slot_budget_before_tts(
                    segments_data,
                    _sb_tm,
                    src_lang=_src_lang_sb,
                    tgt_lang=target_lang or "uk",
                    task_info=_sb_info,
                )
                _slot_budget_ok = bool(_sb_report.tts_allowed)
                current_timing_map_snapshot = list(_sb_tm)
                # Keep parallel text list index-aligned with segments_data /
                # timing_map. Filtering merged rows here caused TTS groups to
                # map text onto the wrong timing slots.
                segments = []
                for s in segments_data:
                    if not isinstance(s, dict):
                        segments.append("")
                        continue
                    if s.get("merged_into") is not None or s.get("archived"):
                        segments.append("")
                        continue
                    try:
                        from engines.tts_text_authority import resolve_final_tts_text

                        _spoken = resolve_final_tts_text(s)
                    except Exception:
                        _spoken = ""
                    segments.append(
                        _spoken
                        or str(
                            s.get("plain_text")
                            or s.get("translation_text")
                            or s.get("text")
                            or ""
                        ).strip()
                    )
                with STATE_LOCK:
                    task["info"]["segments_data"] = segments_data
                    task["info"]["timing_map_backup"] = current_timing_map_snapshot
                    task["info"]["slot_budget_report"] = _sb_report.to_dict()
                    task["info"]["slot_budget_ok"] = _slot_budget_ok
                if not _slot_budget_ok:
                    logger.warning(
                        "Task %s: SlotBudgetFirst blocked %d segment(s) — "
                        "TTS will NOT be called for blocked rows "
                        "(normalize/merge first)",
                        task_id,
                        len(_sb_report.blocked),
                    )
            except Exception as _sb_exc:
                logger.warning(
                    "Task %s: PSA4 SlotBudget/Normalizer pre-TTS skipped: %s",
                    task_id,
                    _sb_exc,
                )

            # Re-freeze after SlotBudget — PSA4 must not diverge spoken text from Review.
            if review_before_tts or review_approved:
                try:
                    from engines.tts_review_align import freeze_spoken_to_review_final

                    with STATE_LOCK:
                        _sd_fr2 = list(task["info"].get("segments_data") or segments_data)
                        _aud_fr2 = list(task["info"].get("translation_audits") or [])
                        segments = freeze_spoken_to_review_final(
                            segments,
                            _sd_fr2,
                            _aud_fr2,
                            source_segments=source_segments_snapshot,
                        )
                        task["info"]["segments_data"] = _sd_fr2
                    segments_data = _sd_fr2
                    # Keep text list index-aligned with live rows (2.zip RCA).
                    if len(segments) > len(segments_data):
                        segments = list(segments[: len(segments_data)])
                    elif len(segments) < len(segments_data):
                        while len(segments) < len(segments_data):
                            segments.append("")
                except Exception as _fr2_exc:
                    logger.debug("post-SlotBudget review freeze skipped: %s", _fr2_exc)

            # Keep 1:1 TTS groups when review-before-TTS is on — merged groups
            # stamped combined text onto the head segment (Review bleed N→N+1).
            # Happy Path (TZ Stage 2): still merge ultra-short slots (<3s) for
            # natural delivery when review freeze is off; with review on, rely on
            # STT happy-path merge (≥5s) and keep 1:1 to protect Final ownership.
            _tts_min_ms = 1 if review_before_tts else 2000
            try:
                from engines.happy_path import skip_advanced_text_shorteners as _hp_tts
                from engines.translation_naturalizer import HAPPY_PATH_PRE_TTS_MIN_MS

                with STATE_LOCK:
                    _info_tts = dict(task.get("info") or {})
                if _hp_tts(_info_tts) and not review_before_tts:
                    _tts_min_ms = int(HAPPY_PATH_PRE_TTS_MIN_MS)
                    task["info"]["pre_tts_merge_ms"] = _tts_min_ms
            except Exception:
                pass
            try:
                from engines.segment_merger import ensure_timing_map_for_segments

                current_timing_map_snapshot = ensure_timing_map_for_segments(
                    segments,
                    current_timing_map_snapshot,
                    duration_ms=int(target_duration_ms or 0) or None,
                )
                with STATE_LOCK:
                    task["info"]["timing_map_backup"] = list(current_timing_map_snapshot)
            except Exception as _tm_align_exc:
                logger.debug("pre-TTS timing_map align skipped: %s", _tm_align_exc)
            try:
                tts_groups = build_tts_groups(
                    segments,
                    current_timing_map_snapshot,
                    min_duration_ms=_tts_min_ms,
                )
            except TypeError:
                tts_groups = build_tts_groups(segments, current_timing_map_snapshot)

            # Drop groups whose head index no longer maps into segments_data
            # (freeze/audits can briefly diverge from live rows — 2.zip RCA).
            _n_sd = len(segments_data)
            _kept_groups = []
            for _g in tts_groups:
                _idxs = [int(i) for i in (_g.get("indices") or [])]
                if not _idxs or not (0 <= _idxs[0] < _n_sd):
                    logger.warning(
                        "Task %s: drop TTS group indices=%s (segments_data=%d)",
                        task_id,
                        _idxs,
                        _n_sd,
                    )
                    continue
                _g["indices"] = [i for i in _idxs if 0 <= i < _n_sd] or _idxs[:1]
                _kept_groups.append(_g)
            tts_groups = _kept_groups

            # Stage 4: stamp group.final_tts_text from locked segment authority.
            try:
                from engines.tts_text_authority import (
                    assert_tts_matches_final,
                    resolve_final_tts_text,
                    text_hash,
                )

                for _g in tts_groups:
                    _idxs = list(_g.get("indices") or [])
                    if not _idxs:
                        continue
                    _hi = int(_idxs[0])
                    _exp = ""
                    if 0 <= _hi < len(segments_data) and isinstance(
                        segments_data[_hi], dict
                    ):
                        _exp = resolve_final_tts_text(segments_data[_hi])
                    if not _exp and 0 <= _hi < len(segments):
                        _exp = str(segments[_hi] or "")
                    if _exp:
                        _g["final_tts_text"] = _exp
                        _g["plain_text"] = _exp
                        # Keep SSML in text only if already set; else plain.
                        if not str(_g.get("text") or "").lstrip().startswith("<speak"):
                            _g["text"] = _exp
                        _g["tts_text_hash"] = text_hash(_exp)
                        _g["spoken_text_source"] = "final_tts_text"
                        assert_tts_matches_final(
                            str(_g.get("text") or ""),
                            _exp,
                            index=_hi,
                            task_id=task_id,
                        )
            except Exception as _auth_exc:
                logger.debug("Task %s: TTS authority stamp skipped: %s", task_id, _auth_exc)

            tts_groups, semantic_log = prepare_tts_groups_semantic(
                tts_groups,
                source_segments=source_segments_snapshot,
                src_lang=translation_source_lang,
                tgt_lang=target_lang,
                task_id=task_id,
                app_dir=APP_DIR,
                adapt_text=adapt_tts_text,
                segments_data=segments_data,
            )
            with STATE_LOCK:
                style_cfg = _style_params_from_info(task.get("info"))
                _voice_hints = {
                    i: dict(seg.get("ai_voice") or {})
                    for i, seg in enumerate(task.get("info", {}).get("segments_data") or [])
                    if seg.get("ai_voice")
                }

            from engines.dub_style_presets import get_dub_style
            from engines.professional_dubbing import prepare_tts_groups_prosody

            _style_id = dub_style or "modern"
            try:
                _style_delivery = get_dub_style(_style_id).delivery
            except Exception:
                _style_delivery = ""
            with STATE_LOCK:
                _src_audio = task["info"].get("original_audio_path") or audio_path
            tts_groups, produb_meta = prepare_tts_groups_prosody(
                tts_groups,
                lang=target_lang,
                style_id=_style_id,
                delivery=_style_delivery,
                base_rate=tts_rate,
                base_pitch=tts_pitch,
                segment_voice_hints=_voice_hints,
                app_dir=APP_DIR,
                task_id=task_id,
                source_audio_path=_src_audio,
            )

            # Voice Platform: lock speaker→voice before synthesis (soft-fail).
            # Stage 9 Simple: NEVER multi-voice — pin pipeline_voice on all segs.
            try:
                from engines.simple_voice_lock import (
                    lock_simple_pipeline_voice,
                    should_lock_simple_voice,
                )

                with STATE_LOCK:
                    _vinfo = dict(task.get("info") or {})
                    _vinfo.setdefault("target_lang", target_lang)
                if should_lock_simple_voice(_vinfo):
                    voice = lock_simple_pipeline_voice(
                        segments_data,
                        pipeline_voice=voice,
                        task_info=_vinfo,
                    ).get("pipeline_voice") or voice
                    with STATE_LOCK:
                        task["info"].update(
                            {
                                k: _vinfo[k]
                                for k in (
                                    "simple_voice_locked",
                                    "pipeline_voice",
                                    "tts_voice",
                                    "unique_voices_used",
                                    "unique_voices",
                                    "voice_lock_pinned_segments",
                                    "voice_platform_skipped",
                                    "voice",
                                )
                                if k in _vinfo
                            }
                        )
                        task["info"]["segments_data"] = segments_data
                    logger.info(
                        "Task %s: Simple voice lock tts_voice=%s unique=1",
                        task_id,
                        voice,
                    )
                    # Stage 12: reject non-uk TTS text; remt once or skip.
                    try:
                        from engines.tts_lang_lock import (
                            assert_voice_matches_target,
                            enforce_segments_lang_lock,
                        )

                        assert_voice_matches_target(voice, target_lang, raise_error=True)
                        with STATE_LOCK:
                            _src_l = str(
                                task["info"].get("source_lang")
                                or task["info"].get("detected_lang")
                                or "en"
                            )
                        try:
                            _src_snap = list(source_segments_snapshot or [])
                        except NameError:
                            _src_snap = []
                        for _i, _ls in enumerate(segments_data or []):
                            if not isinstance(_ls, dict):
                                continue
                            if str(
                                _ls.get("original")
                                or _ls.get("original_text")
                                or _ls.get("whisper_text")
                                or _ls.get("source_text")
                                or ""
                            ).strip():
                                continue
                            if _i < len(_src_snap) and str(_src_snap[_i] or "").strip():
                                _ls["original"] = str(_src_snap[_i]).strip()
                        _lang_stats = enforce_segments_lang_lock(
                            segments_data,
                            target_lang=target_lang,
                            source_lang=_src_l,
                            app_dir=APP_DIR,
                            simple_mode=True,
                            fail_loud=True,
                        )
                        with STATE_LOCK:
                            task["info"]["tts_lang_lock"] = dict(_lang_stats)
                        # Sync tts_groups plain_text after remt/skip.
                        for _g in tts_groups:
                            _idxs = [int(x) for x in (_g.get("indices") or [])]
                            if not _idxs:
                                continue
                            _head = _idxs[0]
                            if 0 <= _head < len(segments_data) and isinstance(
                                segments_data[_head], dict
                            ):
                                _nt = str(
                                    segments_data[_head].get("final_tts_text")
                                    or segments_data[_head].get("plain_text")
                                    or ""
                                ).strip()
                                if segments_data[_head].get("skip_tts"):
                                    _g["plain_text"] = ""
                                    _g["text"] = ""
                                    _g["final_tts_text"] = ""
                                    _g["skip_tts"] = True
                                elif _nt:
                                    _g["plain_text"] = _nt
                                    _g["final_tts_text"] = _nt
                                    _g["text"] = _nt
                        logger.info(
                            "Task %s: TTS lang lock checked=%s remt_ok=%s skipped=%s",
                            task_id,
                            _lang_stats.get("checked"),
                            _lang_stats.get("remt_ok"),
                            _lang_stats.get("skipped"),
                        )
                    except RuntimeError:
                        raise
                    except Exception as _ll_exc:
                        logger.warning(
                            "Task %s: TTS lang lock soft-fail: %s", task_id, _ll_exc
                        )
                else:
                    voice = _apply_voice_platform_assignments(
                        task_id,
                        segments_data,
                        default_voice=voice,
                        target_lang=target_lang,
                        style_id=str(_style_id or "Movie"),
                    )
            except RuntimeError:
                raise
            except Exception as _vp_exc:
                logger.warning(
                    "Task %s: voice platform assignment soft-fail: %s",
                    task_id,
                    _vp_exc,
                )
                try:
                    from engines.simple_voice_lock import (
                        lock_simple_pipeline_voice,
                        should_lock_simple_voice,
                    )

                    with STATE_LOCK:
                        _vinfo2 = dict(task.get("info") or {})
                        _vinfo2.setdefault("target_lang", target_lang)
                    if should_lock_simple_voice(_vinfo2):
                        lock_simple_pipeline_voice(
                            segments_data,
                            pipeline_voice=voice,
                            task_info=_vinfo2,
                        )
                        with STATE_LOCK:
                            task["info"].update(_vinfo2)
                except Exception:
                    pass

            # Build per-segment TTS input trace using plain_text (same as actual TTS input).
            # group["text"] may be SSML; plain_text is what actually goes to edge_tts.
            # Never stamp a merged group blob onto every member index.
            tts_inputs_by_seg: list[str] = list(segments)
            for group in tts_groups:
                plain = str(group.get("plain_text") or "").strip()
                gtext_ssml = str(group.get("text") or "").strip()
                gtext = plain if plain else gtext_ssml
                if gtext.lstrip().startswith("<speak"):
                    import re as _re
                    gtext = _re.sub(r"<[^>]+>", " ", gtext).strip()
                indices = [int(i) for i in (group.get("indices") or [])]
                if len(indices) <= 1:
                    idx = indices[0] if indices else -1
                    if 0 <= idx < len(tts_inputs_by_seg) and gtext:
                        tts_inputs_by_seg[idx] = gtext
                    continue
                for idx in indices:
                    if not (0 <= idx < len(tts_inputs_by_seg)):
                        continue
                    seg = (
                        segments_data[idx]
                        if idx < len(segments_data) and isinstance(segments_data[idx], dict)
                        else {}
                    )
                    member = str(
                        seg.get("plain_text")
                        or seg.get("tts_text")
                        or seg.get("text")
                        or tts_inputs_by_seg[idx]
                        or ""
                    ).strip()
                    if member.lstrip().startswith("<speak"):
                        import re as _re
                        member = _re.sub(r"<[^>]+>", " ", member).strip()
                    # Head may fall back to group text; members keep own text.
                    if idx == indices[0]:
                        tts_inputs_by_seg[idx] = member or gtext
                    elif member:
                        tts_inputs_by_seg[idx] = member

            with STATE_LOCK:
                info_for_trace = copy.deepcopy(task.get("info") or {})
            trace_rows = build_tts_trace_rows(info_for_trace, tts_inputs_by_seg)
            tts_trace_path = log_tts_trace(
                APP_DIR, trace_rows, task_id=task_id, phase="pre_synthesis"
            )
            mismatches = find_mismatches(trace_rows)
            if mismatches:
                logger.warning(
                    "Task %s: TTS input != Final for %d segments (see %s)",
                    task_id,
                    len(mismatches),
                    tts_trace_path,
                )
            with STATE_LOCK:
                task["info"]["tts_text_trace_log"] = tts_trace_path
                task["info"]["tts_text_mismatches"] = len(mismatches)
                task["info"]["translate_method"] = translate_method_snap
                task["info"]["tts_semantic_adapt"] = adapt_tts_text
                task["info"]["professional_dubbing"] = produb_meta
            with STATE_LOCK:
                task["info"]["semantic_adaptation_log"] = semantic_log.path
                audits = task["info"].get("translation_audits") or []
                from engines.translation_trace import TranslationTraceLog

                trace = TranslationTraceLog(APP_DIR, task_id=task_id)
                _sync_tts_audits_from_groups(
                    audits,
                    segments_data,
                    tts_groups,
                    trace=trace,
                    prosody_only=not adapt_tts_text,
                )
                task["info"]["translation_audits"] = audits
                trace.flush(phase="post_semantic", extra={"tgt": target_lang})
                task["info"]["translation_trace_log"] = trace.path

            from engines.pipeline_integrity.tts_segment_fields import (
                apply_tts_group_merge_links,
                resolve_tts_input_text,
            )

            with STATE_LOCK:
                tts_engine_id = task["info"].get("tts_engine") or "edge-offline"
                _tts_info_snap = dict(task.get("info") or {})
            try:
                from engines.tts_backends import bind_pipeline_tts_from_info

                tts_engine_id = bind_pipeline_tts_from_info(_tts_info_snap)
            except Exception:
                pass

            apply_tts_group_merge_links(segments_data, tts_groups)
            _log_tts_synthesis_requests(
                task_id,
                tts_groups=tts_groups,
                segments_data=segments_data,
                voice=voice,
                target_lang=target_lang,
                provider=str(tts_engine_id or "edge-offline"),
            )

            log_stage_end(task_id, "TTS_PREP")
            log_stage_transition(task_id, "TTS_PREP", "TTS")
            _set_step(task_id, "tts", 65.0)
            runtime_diag.stage_begin(STAGE_TTS)
            log_stage_begin(task_id, "TTS")

            # PSA4 SlotBudget already applied before TTS groups; re-stamp gate.
            try:
                from engines.pipeline_integrity.slot_budget import segment_tts_allowed

                _blocked_n = sum(
                    1
                    for s in segments_data
                    if isinstance(s, dict)
                    and s.get("merged_into") is None
                    and not segment_tts_allowed(s)
                )
                _slot_budget_ok = _blocked_n == 0
                with STATE_LOCK:
                    task["info"]["slot_budget_ok"] = _slot_budget_ok
            except Exception:
                pass

            # ── Identity Guard before TTS (PSA2) — bind + assert ──
            try:
                from engines.pipeline_integrity.identity_guard import (
                    bind,
                    run_identity_guard,
                )

                for _seg in segments_data:
                    if isinstance(_seg, dict) and _seg.get("merged_into") is None:
                        bind(_seg, stage="pre_tts")
                with STATE_LOCK:
                    run_identity_guard(
                        segments_data,
                        stage="pre_tts",
                        task_info=task["info"],
                        require_wav=False,
                    )
            except Exception as _ig_exc:
                from engines.pipeline_integrity.exceptions import (
                    IdentityMismatchError,
                )

                if isinstance(_ig_exc, IdentityMismatchError):
                    raise
                logger.warning(
                    "Task %s: IdentityGuard pre_tts soft-fail: %s",
                    task_id,
                    _ig_exc,
                )

            # MF-HOTFIX safety net: MF before LOCK if not already done post-Naturalizer.
            # Skip when review-before-TTS already approved Final (do not re-shorten).
            with STATE_LOCK:
                _mf_info = task["info"]
                segments_data = list(
                    _mf_info.get("segments_data") or segments_data
                )
                _review_freeze_mf = bool(
                    _mf_info.get("translation_review_before_tts", True)
                )
            try:
                from engines.meaning_fit import apply_meaning_fit_before_lock
                from engines.meaning_fit.flags import ensure_meaning_fit_enabled_for_dubbing
                from engines.happy_path import advanced_adaptation_enabled as _adv_mf2

                if not _adv_mf2(_mf_info):
                    logger.info(
                        "Task %s: Meaning Fit safety-net skipped (happy_path)",
                        task_id,
                    )
                else:
                    ensure_meaning_fit_enabled_for_dubbing()
                    if _review_freeze_mf:
                        logger.info(
                            "Task %s: Meaning Fit safety-net skipped (review-before-TTS freeze)",
                            task_id,
                        )
                    elif not _mf_info.get("meaning_fit_done"):
                        _mf_report = apply_meaning_fit_before_lock(
                            segments_data,
                            task_info=_mf_info,
                            call_site=(
                                "api/auto_dub_api.py:pre_TTS_safety_net"
                                ":BEFORE_lock_block"
                            ),
                        )
                        with STATE_LOCK:
                            task["info"] = _mf_info
                            if _mf_info.get("segments_data"):
                                segments_data = list(_mf_info["segments_data"])
                        logger.info(
                            "Task %s: Meaning Fit safety-net enabled=%s applied=%s "
                            "processed=%s call_site=%s",
                            task_id,
                            _mf_report.get("enabled"),
                            _mf_report.get("applied"),
                            _mf_report.get("processed"),
                            _mf_report.get("call_site"),
                        )
            except Exception as _mf_exc:
                logger.warning(
                    "Task %s: Meaning Fit safety-net soft-fail: %s",
                    task_id,
                    _mf_exc,
                )

            # Pipeline Integrity v2.0 BUG F: do NOT lock before TTS.
            # Legacy lock retained only when Overflow Inspector is disabled.
            with STATE_LOCK:
                _lock_info = task["info"]
                segments_data = list(
                    _lock_info.get("segments_data") or segments_data
                )
                from engines.pipeline_integrity.v2_gates import (
                    overflow_inspector_enabled,
                )

                if not overflow_inspector_enabled():
                    if not _lock_info.get("translation_locked"):
                        from engines.translation_validation import (
                            apply_translation_lock_after_validation,
                        )

                        apply_translation_lock_after_validation(_lock_info)
                        segments_data = list(
                            _lock_info.get("segments_data") or segments_data
                        )
                else:
                    _lock_info["translation_lock_deferred"] = "awaiting_post_fit"
                    logger.info(
                        "Task %s: Translation Lock deferred until after "
                        "TTS + Slot Fit (Overflow Inspector)",
                        task_id,
                    )
                from engines.pipeline_integrity.pipeline_state import (
                    PipelineState,
                    advance_pipeline_state,
                    get_pipeline_state,
                )

                if get_pipeline_state(_lock_info) == PipelineState.LOCKED:
                    advance_pipeline_state(_lock_info, PipelineState.TTS_READY)

            _integrity_coordinator(task_id).begin_stage("tts", segments_data)

            total_groups = max(len(tts_groups), 1)
            tts_files = []
            _update_progress_detail(
                task_id,
                total_segments=len(segments),
                segments_done=0,
                phase="tts",
                tts_groups_total=total_groups,
                tts_groups_done=0,
            )

            profiler.start("tts")
            if pipeline_timer is not None:
                pipeline_timer.start("tts")
            with STATE_LOCK:
                use_parallel_tts = not control.get("stop_after_segment")
                tts_engine_id = task["info"].get("tts_engine") or "edge-offline"
                _tts_info_snap = dict(task.get("info") or {})
            try:
                from engines.tts_backends import bind_pipeline_tts_from_info

                tts_engine_id = bind_pipeline_tts_from_info(_tts_info_snap)
            except Exception:
                pass
            _pipeline_mode = str(task["info"].get("pipeline_mode") or "")
            _streaming_voice_done = bool(task["info"].get("streaming_voice_done"))
            _manifest_vp = str(task["info"].get("manifest_path") or "")
            _simple_batch_tts = bool(
                task["info"].get("simple_pipeline")
                or task["info"].get("happy_path")
            )

            # Stage 6: Simple/Happy Path always owns TTS via parallel+cache batch.
            # Conveyor/streaming may have run earlier — ignore their skip flags here.
            _skip_batch_tts = False
            if not _simple_batch_tts:
                _skip_batch_tts = _streaming_voice_done
                with STATE_LOCK:
                    _conveyor_tts_done = bool(task["info"].get("conveyor_tts_done"))
                if _conveyor_tts_done:
                    _skip_batch_tts = True
                    for seg in segments_data:
                        f = seg.get("file")
                        if f and f not in tts_files:
                            tts_files.append(f)
                    logger.info(
                        "Task %s: batch TTS skipped — full conveyor (%d files)",
                        task_id,
                        len(tts_files),
                    )
                if _pipeline_mode == "streaming" and not _streaming_voice_done:
                    _skip_batch_tts = _run_streaming_voice_for_task(
                        task_id,
                        segments_data,
                        voice=voice,
                        target_lang=target_lang,
                        tts_rate=tts_rate,
                        tts_pitch=tts_pitch,
                        manifest_path=_manifest_vp,
                    )
            else:
                with STATE_LOCK:
                    task["info"]["tts_batch_forced"] = "simple_stage6"
                    # Keep any prior conveyor files as skip-existing candidates only.
                logger.info(
                    "Task %s: Simple Stage6 batch TTS (ignore conveyor/streaming skip)",
                    task_id,
                )

            def _parallel_tts_progress(g_idx: int, groups_total: int, groups_done: int) -> None:
                head_idx = 0
                text = ""
                if 0 <= g_idx < len(tts_groups):
                    indices = tts_groups[g_idx].get("indices") or []
                    head_idx = indices[0] if indices else 0
                    text = resolve_tts_input_text(tts_groups[g_idx]) or ""
                with STATE_LOCK:
                    task["progress"] = round(
                        65.0 + (groups_done / max(groups_total, 1)) * 15.0, 1
                    )
                _update_progress_detail(
                    task_id,
                    current_segment=head_idx + 1,
                    total_segments=len(segments),
                    segments_done=groups_done,
                    phase="tts",
                    operation="speech_generation",
                    tts_groups_done=groups_done,
                    tts_groups_total=groups_total,
                    eta_sec=_estimate_eta_sec(
                        task_id, groups_done / max(groups_total, 1)
                    ),
                    **_segment_tts_progress_meta(
                        task_id,
                        head_idx,
                        segments_data,
                        voice=voice,
                        tts_engine_id=str(tts_engine_id or "edge-offline"),
                        text=text,
                    ),
                )

            if _skip_batch_tts:
                for seg in segments_data:
                    if seg.get("merged_into") is not None:
                        continue
                    fn = seg.get("file") or seg.get("tts_file_path")
                    if fn and fn not in tts_files:
                        tts_files.append(fn)
                with STATE_LOCK:
                    task["progress"] = round(80.0, 1)
                _update_progress_detail(
                    task_id,
                    total_segments=len(segments),
                    segments_done=len(tts_files),
                    phase="tts",
                    tts_groups_done=total_groups,
                    tts_groups_total=total_groups,
                )
                logger.info(
                    "Task %s: batch TTS skipped — streaming voice (%d files)",
                    task_id,
                    len(tts_files),
                )
            elif use_parallel_tts and len(tts_groups) > 1:
                # Stage 9: re-pin one voice immediately before synth (Simple).
                try:
                    from engines.simple_voice_lock import (
                        lock_simple_pipeline_voice,
                        should_lock_simple_voice,
                    )

                    with STATE_LOCK:
                        _vpin = dict(task.get("info") or {})
                    if should_lock_simple_voice(_vpin):
                        lock_simple_pipeline_voice(
                            segments_data,
                            pipeline_voice=voice,
                            task_info=_vpin,
                        )
                        voice = str(_vpin.get("pipeline_voice") or voice)
                        with STATE_LOCK:
                            task["info"]["simple_voice_locked"] = True
                            task["info"]["pipeline_voice"] = voice
                            task["info"]["tts_voice"] = voice
                            task["info"]["unique_voices_used"] = 1
                except Exception as _vpin_exc:
                    logger.debug("Simple voice re-pin skipped: %s", _vpin_exc)

                work_items = []
                from engines.pipeline_integrity.slot_budget import segment_tts_allowed

                for g_idx, group in enumerate(tts_groups):
                    indices = group["indices"]
                    text = resolve_tts_input_text(group)
                    try:
                        from engines.text_slot_fit import strip_slot_pad_fillers

                        text = strip_slot_pad_fillers(text)
                    except Exception:
                        pass
                    try:
                        from engines.tts_text_authority import prefer_locked_uk_spoken_text

                        _hi_lock = int(indices[0]) if indices else -1
                        _seg_lock = (
                            segments_data[_hi_lock]
                            if 0 <= _hi_lock < len(segments_data)
                            else None
                        )
                        text = prefer_locked_uk_spoken_text(
                            text, group=group, seg=_seg_lock
                        )
                    except Exception:
                        pass
                    try:
                        from engines.tts_text_authority import (
                            assert_tts_matches_final,
                            text_hash,
                        )

                        _exp_g = str(group.get("final_tts_text") or text)
                        try:
                            from engines.text_slot_fit import strip_slot_pad_fillers as _spf

                            _exp_g = _spf(_exp_g)
                        except Exception:
                            pass
                        assert_tts_matches_final(
                            text, _exp_g, index=indices[0] if indices else -1, task_id=task_id
                        )
                        group["tts_text_hash"] = text_hash(text)
                        group["spoken_text_source"] = "final_tts_text"
                    except Exception:
                        pass
                    if not text:
                        head_idx = int(indices[0]) if indices else -1
                        logger.warning(
                            "Task %s: skip TTS group %s — empty text (idx=%s)",
                            task_id,
                            g_idx,
                            head_idx,
                        )
                        if 0 <= head_idx < len(segments_data) and isinstance(
                            segments_data[head_idx], dict
                        ):
                            segments_data[head_idx]["skip_tts"] = True
                            segments_data[head_idx]["tts_empty_skipped"] = True
                            segments_data[head_idx]["status"] = "empty"
                        with STATE_LOCK:
                            info_e = task.setdefault("info", {})
                            skips = list(info_e.get("tts_empty_skipped_indices") or [])
                            if head_idx >= 0 and head_idx not in skips:
                                skips.append(head_idx)
                            info_e["tts_empty_skipped_indices"] = skips
                        continue
                    if text:
                        head_idx = int(indices[0]) if indices else 0
                        if not (0 <= head_idx < len(segments_data)):
                            logger.warning(
                                "Task %s: skip TTS group %s — head_idx=%s out of "
                                "range (segments_data=%d)",
                                task_id,
                                g_idx,
                                head_idx,
                                len(segments_data),
                            )
                            continue
                        if not segment_tts_allowed(segments_data[head_idx]):
                            logger.info(
                                "Task %s: skip TTS group %s — SlotBudgetFirst blocked",
                                task_id,
                                g_idx,
                            )
                            continue
                        seg_id = str(segments_data[head_idx].get("segment_id") or "")
                        expected_path = str(
                            _artifacts_dir(task.get("info")) / f"{base_id}_g{g_idx:04d}.mp3"
                        )
                        with STATE_LOCK:
                            _simple_v = bool(
                                (task.get("info") or {}).get("simple_voice_locked")
                                or (task.get("info") or {}).get("simple_pipeline")
                                or (task.get("info") or {}).get("happy_path")
                            )
                        group_voice = (
                            voice
                            if _simple_v
                            else _segment_tts_voice(segments_data[head_idx], voice)
                        )
                        work_items.append(
                            {
                                "g_idx": g_idx,
                                "indices": indices,
                                "text": text,
                                "voice": group_voice,
                                "timing": group.get("timing"),
                                "rate": group.get("prosody_rate") or tts_rate,
                                "pitch": group.get("prosody_pitch") or tts_pitch,
                                "tts_context": _tts_context_for_segment(
                                    task_id=task_id,
                                    segment_id=seg_id,
                                    segment_index=head_idx,
                                    current=head_idx + 1,
                                    total=len(segments_data),
                                    original_text=(
                                        source_segments_snapshot[head_idx]
                                        if head_idx < len(source_segments_snapshot)
                                        else ""
                                    ),
                                    tts_text=text,
                                    voice=group_voice,
                                    target_lang=target_lang,
                                    tts_file_path=expected_path,
                                    engine_id=tts_engine_id,
                                ),
                            }
                        )
                with _blocking_progress_heartbeat(
                    task_id,
                    "tts",
                    interval=25.0,
                    messages=[
                        "Генерация речи (TTS)…",
                        "Синтез озвучки сегментов…",
                        "Edge TTS обрабатывает группу…",
                    ],
                ):
                    # Stage 6: parallel Edge + disk cache + skip existing (Simple default).
                    _tts_stats: dict = {}
                    try:
                        from engines.tts_cache import default_cache_dir
                        from engines.tts_parallel import (
                            resolve_edge_tts_concurrency,
                            synthesize_segments_parallel,
                        )

                        try:
                            (APP_DIR / "output" / f"tts_speedup_{task_id}.json").write_text(
                                '{"path":"stage6_entering"}',
                                encoding="utf-8",
                            )
                        except Exception:
                            pass

                        _conc = resolve_edge_tts_concurrency(None)
                        _art = _artifacts_dir(task.get("info"))
                        try:
                            from engines.oss_production import (
                                resolve_oss_segs_dir,
                                resolve_tts_out_path,
                            )

                            _segs = resolve_oss_segs_dir(_art)
                        except Exception:
                            _segs = Path(_art) / "segs"
                            _segs.mkdir(parents=True, exist_ok=True)
                        with STATE_LOCK:
                            (task.get("info") or {})["oss_segs_dir"] = str(_segs)
                        _parallel_items = []
                        for wi in work_items:
                            _g = int(wi.get("g_idx", 0))
                            _ctx_p = str(
                                (wi.get("tts_context") or {}).get("tts_file_path") or ""
                            )
                            try:
                                _out = resolve_tts_out_path(_segs, _g, _ctx_p, ext=".mp3")
                            except Exception:
                                _out = _segs / f"{_g:04d}.mp3"
                            _parallel_items.append(
                                {
                                    **wi,
                                    "index": _g,
                                    "g_idx": _g,
                                    "out_path": str(_out),
                                    "engine_id": tts_engine_id,
                                }
                            )

                        # Progress only from main thread — avoid STATE_LOCK in workers.
                        _done_box = {"n": 0}

                        def _on_fast_done(_idx: int, _done: int) -> None:
                            _done_box["n"] = int(_done)

                        parallel_results, _tts_stats = synthesize_segments_parallel(
                            _parallel_items,
                            concurrency=_conc,
                            cache_dir=default_cache_dir(),
                            warmup=min(2, max(0, len(_parallel_items) - 1)),
                            on_done=_on_fast_done,
                        )
                        _parallel_tts_progress(
                            0, total_groups, int(_done_box.get("n") or len(parallel_results))
                        )
                        with STATE_LOCK:
                            task["info"]["tts_speedup"] = dict(_tts_stats)
                            task["info"]["tts_wall_sec"] = _tts_stats.get("tts_wall_sec")
                            task["info"]["tts_segments_total"] = _tts_stats.get(
                                "tts_segments_total"
                            )
                            task["info"]["tts_cache_hits"] = _tts_stats.get(
                                "tts_cache_hits"
                            )
                            task["info"]["tts_cache_misses"] = _tts_stats.get(
                                "tts_cache_misses"
                            )
                            task["info"]["tts_concurrency_used"] = _tts_stats.get(
                                "tts_concurrency_used"
                            )
                            task["info"]["tts_retries"] = _tts_stats.get("tts_retries")
                            task["info"]["tts_skips_existing"] = _tts_stats.get(
                                "tts_skips_existing"
                            )
                            task["info"]["tts_engine_path"] = "stage6_parallel_cache"
                        # Persist speedup metrics to disk (acceptance / STAGE6 report).
                        try:
                            import json as _json_s6

                            _s6_path = APP_DIR / "output" / f"tts_speedup_{task_id}.json"
                            _s6_path.write_text(
                                _json_s6.dumps(
                                    {
                                        "task_id": task_id,
                                        "path": "stage6_parallel_cache",
                                        **dict(_tts_stats),
                                    },
                                    ensure_ascii=False,
                                    indent=2,
                                ),
                                encoding="utf-8",
                            )
                        except Exception:
                            pass
                        logger.info(
                            "Task %s: Stage6 TTS parallel wall=%.2fs conc=%s hits=%s misses=%s skips=%s",
                            task_id,
                            float(_tts_stats.get("tts_wall_sec") or 0),
                            _tts_stats.get("tts_concurrency_used"),
                            _tts_stats.get("tts_cache_hits"),
                            _tts_stats.get("tts_cache_misses"),
                            _tts_stats.get("tts_skips_existing"),
                        )
                    except Exception as _s6_exc:
                        logger.warning(
                            "Task %s: Stage6 TTS parallel failed (%s) — fallback generate_tts_groups_parallel",
                            task_id,
                            _s6_exc,
                        )
                        try:
                            from engines.tts_parallel import (
                                resolve_edge_tts_concurrency as _rconc,
                            )

                            _fb_conc = _rconc(None)
                        except Exception:
                            _fb_conc = 6
                        parallel_results = generate_tts_groups_parallel(
                            work_items,
                            voice,
                            rate=tts_rate,
                            pitch=tts_pitch,
                            engine_id=tts_engine_id,
                            on_group_done=_parallel_tts_progress,
                            output_dir=_artifacts_dir(task.get("info")),
                            task_id=base_id,
                            max_concurrent=_fb_conc,
                        )
                        with STATE_LOCK:
                            task["info"]["tts_engine_path"] = "legacy_parallel_fallback"
                            task["info"]["tts_stage6_error"] = str(_s6_exc)
                            task["info"]["tts_concurrency_used"] = _fb_conc
                        try:
                            import json as _json_s6e

                            (APP_DIR / "output" / f"tts_speedup_{task_id}.json").write_text(
                                _json_s6e.dumps(
                                    {
                                        "task_id": task_id,
                                        "path": "legacy_parallel_fallback",
                                        "error": str(_s6_exc),
                                        "tts_concurrency_used": _fb_conc,
                                    },
                                    ensure_ascii=False,
                                    indent=2,
                                ),
                                encoding="utf-8",
                            )
                        except Exception:
                            pass

                for res in parallel_results:
                    try:
                        g_idx = int(res.get("g_idx", 0))
                        indices = res.get("indices") or []
                        text = str(res.get("text") or "").strip()
                        one_files = [res["file"]] if res.get("file") else []
                        head_idx = indices[0] if indices else 0
                        if res.get("tts_failure"):
                            for _seg_idx in (indices or []):
                                _open_ddf.record_agent(
                                    task_id, "TTS", called=True, success=False,
                                    error=str(res["tts_failure"]),
                                    fallback_used=True,
                                    segment_idx=_seg_idx,
                                    decision="silence_gap",
                                )
                                _open_ddf.mark_segment_attention(task_id, _seg_idx, "tts_failed")
                            _mark_tts_segment_skipped(
                                task_id,
                                segments_data,
                                indices,
                                res["tts_failure"],
                                reason="tts_failure",
                            )
                            _commit_tts_group_result(
                                segments_data,
                                indices,
                                tts_text=text,
                                audio_filename=None,
                                task_info=task.get("info") if task else None,
                            )
                            continue
                        if one_files:
                            tts_files.extend(one_files)
                            with STATE_LOCK:
                                _tts_info_local = dict(task.get("info") or {})
                            _commit_tts_group_result(
                                segments_data,
                                indices,
                                tts_text=text,
                                audio_filename=one_files[0],
                                task_info=_tts_info_local,
                            )
                            if head_idx < len(segments_data) and isinstance(
                                segments_data[head_idx], dict
                            ):
                                if res.get("rate") is not None:
                                    segments_data[head_idx]["tts_synth_rate"] = res.get("rate")
                                if res.get("pitch") is not None:
                                    segments_data[head_idx]["tts_synth_pitch"] = res.get("pitch")
                                segments_data[head_idx]["tts_cache_hit"] = bool(
                                    res.get("cache_hit")
                                )
                        else:
                            _commit_tts_group_result(
                                segments_data,
                                indices,
                                tts_text=text,
                                audio_filename=None,
                                task_info=task.get("info") if task else None,
                            )
                    except Exception as _par_apply_exc:
                        logger.error(
                            "Task %s: parallel TTS result apply failed g=%s: %s — continuing",
                            task_id,
                            res.get("g_idx") if isinstance(res, dict) else "?",
                            _par_apply_exc,
                        )

                with STATE_LOCK:
                    task["progress"] = round(65.0 + 15.0, 1)
            else:
                from engines.pipeline_segment_watchdog import run_segment_bounded

                from engines.pipeline_integrity.slot_budget import segment_tts_allowed

                for g_idx, group in enumerate(tts_groups):
                    if not _ensure_control(task_id, ui_lang):
                        profiler.stop("tts")
                        return

                    indices = group["indices"]
                    text = resolve_tts_input_text(group)
                    try:
                        from engines.text_slot_fit import strip_slot_pad_fillers

                        text = strip_slot_pad_fillers(text)
                    except Exception:
                        pass
                    try:
                        from engines.tts_text_authority import prefer_locked_uk_spoken_text

                        _hi_s = int(indices[0]) if indices else -1
                        _seg_s = (
                            segments_data[_hi_s]
                            if 0 <= _hi_s < len(segments_data)
                            else None
                        )
                        text = prefer_locked_uk_spoken_text(
                            text, group=group, seg=_seg_s
                        )
                    except Exception:
                        pass
                    head_idx = int(indices[0]) if indices else 0
                    if not text:
                        logger.warning(
                            "Task %s: skip TTS group %s — empty text (idx=%s)",
                            task_id,
                            g_idx,
                            head_idx,
                        )
                        if 0 <= head_idx < len(segments_data) and isinstance(
                            segments_data[head_idx], dict
                        ):
                            segments_data[head_idx]["skip_tts"] = True
                            segments_data[head_idx]["tts_empty_skipped"] = True
                            segments_data[head_idx]["status"] = "empty"
                        continue
                    if not (0 <= head_idx < len(segments_data)):
                        logger.warning(
                            "Task %s: skip TTS group %s — head_idx=%s out of "
                            "range (segments_data=%d)",
                            task_id,
                            g_idx,
                            head_idx,
                            len(segments_data),
                        )
                        continue
                    if not segment_tts_allowed(segments_data[head_idx]):
                        logger.info(
                            "Task %s: skip TTS group %s — SlotBudgetFirst blocked",
                            task_id,
                            g_idx,
                        )
                        continue

                    with STATE_LOCK:
                        control["current_segment"] = head_idx
                        task["progress"] = round(
                            65.0 + (g_idx / total_groups) * 15.0, 1
                        )
                    try:
                        from engines.pipeline_progress_tracker import record_segment_start

                        record_segment_start(
                            task_id,
                            "tts",
                            head_idx + 1,
                            total_segments=len(segments),
                            **_segment_tts_progress_meta(
                                task_id,
                                head_idx,
                                segments_data,
                                voice=voice,
                                tts_engine_id=str(tts_engine_id or "edge-offline"),
                                text=text,
                            ),
                        )
                    except Exception:
                        pass
                    _update_progress_detail(
                        task_id,
                        current_segment=head_idx + 1,
                        total_segments=len(segments),
                        segments_done=g_idx,
                        phase="tts",
                        operation="speech_generation",
                        tts_groups_done=g_idx,
                        tts_groups_total=total_groups,
                        eta_sec=_estimate_eta_sec(task_id, (g_idx + 1) / total_groups),
                        **_segment_tts_progress_meta(
                            task_id,
                            head_idx,
                            segments_data,
                            voice=voice,
                            tts_engine_id=str(tts_engine_id or "edge-offline"),
                            text=text,
                        ),
                    )

                    if not text:
                        _commit_tts_group_result(
                            segments_data,
                            indices,
                            tts_text="",
                            audio_filename=None,
                            task_info=task.get("info") if task else None,
                        )
                        continue

                    logger.info(
                        "Task %s: TTS group %d/%d indices=%s len=%d segment_id=%s",
                        task_id,
                        g_idx + 1,
                        total_groups,
                        indices,
                        len(text),
                        segments_data[head_idx].get("segment_id"),
                    )
                    expected_path = str(
                        _artifacts_dir(task.get("info")) / f"{base_id}_seg0000.mp3"
                    )
                    tts_ctx = _tts_context_for_segment(
                        task_id=task_id,
                        segment_id=str(segments_data[head_idx].get("segment_id") or ""),
                        segment_index=head_idx,
                        current=head_idx + 1,
                        total=len(segments_data),
                        original_text=(
                            source_segments_snapshot[head_idx]
                            if head_idx < len(source_segments_snapshot)
                            else ""
                        ),
                        tts_text=text,
                        voice=voice,
                        target_lang=target_lang,
                        tts_file_path=expected_path,
                        engine_id=tts_engine_id,
                    )
                    try:
                        with STATE_LOCK:
                            _simple_v2 = bool(
                                (task.get("info") or {}).get("simple_voice_locked")
                                or (task.get("info") or {}).get("simple_pipeline")
                                or (task.get("info") or {}).get("happy_path")
                            )
                        group_voice = (
                            voice
                            if _simple_v2
                            else _segment_tts_voice(
                                segments_data[head_idx]
                                if head_idx < len(segments_data)
                                else None,
                                voice,
                            )
                        )
                        tts_ctx["voice"] = group_voice

                        def _run_tts_group() -> list:
                            raw = generate_audio(
                                text=text,
                                voice=group_voice,
                                segments=[text],
                                rate=group.get("prosody_rate") or tts_rate,
                                pitch=group.get("prosody_pitch") or tts_pitch,
                                engine_id=tts_engine_id,
                                output_dir=_artifacts_dir(task.get("info")),
                                task_id=base_id,
                                context=tts_ctx,
                            )
                            return _normalize_tts_result(raw)

                        with _blocking_progress_heartbeat(
                            task_id,
                            "tts",
                            interval=25.0,
                            messages=[
                                f"TTS сегмент {head_idx + 1}/{len(segments)}…",
                                "Синтез речи…",
                            ],
                        ):
                            watch = run_segment_bounded(
                                task_id=task_id,
                                phase="tts",
                                segment_index=head_idx,
                                stage="tts_synthesis",
                                fn=_run_tts_group,
                                fallback=lambda: [],
                            )
                        try:
                            from engines.pipeline_progress_tracker import record_segment_end

                            record_segment_end(
                                task_id,
                                "tts",
                                head_idx + 1,
                                cause="tts_timeout" if watch.timed_out else "",
                                error=str(watch.error or ""),
                            )
                        except Exception:
                            pass
                        one_files = watch.value
                        if watch.timed_out or watch.error:
                            from engines.dubbing_engine.tts_failure_diag import (
                                build_failure_report,
                            )

                            report = build_failure_report(
                                RuntimeError(
                                    watch.error or f"timeout_{watch.elapsed_sec:.0f}s"
                                ),
                                segment_id=str(
                                    segments_data[head_idx].get("segment_id") or ""
                                ),
                                segment_index=head_idx,
                                current=head_idx + 1,
                                total=len(segments_data),
                                original_text=(
                                    source_segments_snapshot[head_idx]
                                    if head_idx < len(source_segments_snapshot)
                                    else ""
                                ),
                                tts_text=text,
                                voice=voice,
                                language=target_lang,
                                tts_file_path=expected_path,
                                duration_ms=watch.elapsed_sec * 1000.0,
                                task_id=task_id,
                                engine_id=tts_engine_id or "edge-offline",
                            )
                            _mark_tts_segment_skipped(
                                task_id,
                                segments_data,
                                indices,
                                report,
                                reason=watch.error or "timeout",
                            )
                            one_files = []
                    except Exception as te:
                        from engines.dubbing_engine.tts_failure_diag import (
                            VoiceGenerationError,
                            build_failure_report,
                        )

                        if isinstance(te, VoiceGenerationError) and te.report:
                            report = te.report
                        else:
                            report = build_failure_report(
                                te,
                                segment_id=str(segments_data[head_idx].get("segment_id") or ""),
                                segment_index=head_idx,
                                current=head_idx + 1,
                                total=len(segments_data),
                                original_text=(
                                    source_segments_snapshot[head_idx]
                                    if head_idx < len(source_segments_snapshot)
                                    else ""
                                ),
                                tts_text=text,
                                voice=voice,
                                language=target_lang,
                                tts_file_path=expected_path,
                                duration_ms=0.0,
                                task_id=task_id,
                                engine_id=tts_engine_id or "edge-offline",
                            )
                        _mark_tts_segment_skipped(
                            task_id,
                            segments_data,
                            indices,
                            report,
                            reason="tts_exception",
                        )
                        _commit_tts_group_result(
                            segments_data,
                            indices,
                            tts_text=text,
                            audio_filename=None,
                            task_info=task.get("info") if task else None,
                        )
                        for _ddf_idx in (indices or []):
                            _open_ddf.record_agent(
                                task_id, "TTS", called=True, success=False,
                                error=str(te), fallback_used=True,
                                segment_idx=_ddf_idx, decision="silence_gap",
                            )
                            _open_ddf.mark_segment_attention(task_id, _ddf_idx, "tts_failed")
                        continue

                    with STATE_LOCK:
                        if one_files:
                            tts_files.extend(one_files)
                            _tts_info_local = dict(task.get("info") or {})
                            _commit_tts_group_result(
                                segments_data,
                                indices,
                                tts_text=text,
                                audio_filename=one_files[0],
                                task_info=_tts_info_local,
                            )
                            if not _tts_info_local.get("oss_locked_voice"):
                                try:
                                    from engines.oss_production import (
                                        lock_voice_after_first_success,
                                    )

                                    _lv = lock_voice_after_first_success(
                                        [
                                            s
                                            for s in segments_data
                                            if isinstance(s, dict)
                                        ],
                                        voice=str(group_voice or voice or ""),
                                        engine_id=str(
                                            tts_engine_id or "edge-offline"
                                        ),
                                    )
                                    voice = str(
                                        _lv.get("oss_locked_voice")
                                        or group_voice
                                        or voice
                                    )
                                    task.setdefault("info", {}).update(_lv)
                                    task["info"]["voice"] = voice
                                    task["info"]["pipeline_voice"] = voice
                                except Exception:
                                    pass
                        else:
                            logger.warning(
                                "Task %s: TTS returned no file for group %d",
                                task_id,
                                g_idx + 1,
                            )
                            _commit_tts_group_result(
                                segments_data,
                                indices,
                                tts_text=text,
                                audio_filename=None,
                                task_info=task.get("info") if task else None,
                            )

                    with STATE_LOCK:
                        is_stop_triggered = control["stop_after_segment"]

                    if is_stop_triggered:
                        with STATE_LOCK:
                            control.update(
                                {
                                    "editing": True,
                                    "state": "paused",
                                    "stop_after_segment": False,
                                }
                            )
                        if not _ensure_control(task_id, ui_lang):
                            profiler.stop("tts")
                            return

            profiler.stop("tts")
            if pipeline_timer is not None:
                pipeline_timer.stop("tts")
            log_stage_end(task_id, "TTS")
            log_stage_transition(task_id, "TTS", "POST_TTS_QA")
            log_stage_begin(task_id, "POST_TTS_QA")
            try:
                from engines.ai_core.llm_bootstrap import prepare_llm_for_pipeline

                with STATE_LOCK:
                    _llm_post_info = dict(task.get("info") or {})
                _llm_post = prepare_llm_for_pipeline(
                    task_id,
                    _llm_post_info,
                    app_dir=APP_DIR,
                    phase="POST_TTS_QA",
                )
                with STATE_LOCK:
                    task["info"]["llm_bootstrap_post_tts"] = _llm_post
            except Exception:
                logger.debug("POST_TTS_QA LLM bootstrap skipped", exc_info=True)
            _pi_tts = _integrity_coordinator(task_id)
            _pi_tts.end_stage(
                "tts",
                segments_data,
                timing_map=current_timing_map_snapshot,
            )
            from engines.pipeline_integrity.tts_segment_fields import sync_tts_legacy_fields

            sync_tts_legacy_fields(segments_data)
            with STATE_LOCK:
                tts_ok = sum(
                    1
                    for s in (AUTO_TASKS.get(task_id, {}).get("info", {}).get("segments_data") or [])
                    if (s.get("tts_file_path") or s.get("file"))
                    and s.get("merged_into") is None
                    and s.get("status") not in ("merged", "failed", "empty")
                )
                _eligible = sum(
                    1
                    for s in (AUTO_TASKS.get(task_id, {}).get("info", {}).get("segments_data") or [])
                    if isinstance(s, dict)
                    and s.get("merged_into") is None
                    and not s.get("archived")
                    and not s.get("skip_tts")
                    and not s.get("tts_blocked")
                    and str(s.get("text") or "").strip()
                )
                _tts_failures_n = len(
                    (AUTO_TASKS.get(task_id, {}).get("info", {}) or {}).get("tts_failures") or []
                )
            if not skip_tts and _eligible > 0 and tts_ok == 0:
                return _fail(
                    task_id,
                    [
                        f"TTS handoff empty: 0/{_eligible} segments synthesized "
                        f"(failures={_tts_failures_n})"
                    ],
                    stage=STAGE_TTS,
                    error_code="TTS_HANDOFF_EMPTY",
                )
            if not skip_tts and _eligible >= 3 and tts_ok < max(1, int(_eligible * 0.3)):
                return _fail(
                    task_id,
                    [
                        f"TTS coverage too low: {tts_ok}/{_eligible} segments OK "
                        f"(failures={_tts_failures_n}) — refusing to mux partial silence"
                    ],
                    stage=STAGE_TTS,
                    error_code="TTS_FAILED",
                )
            _runtime_stage_record(
                task_id,
                runtime_diag,
                4,
                STAGE_TTS,
                segments_ok=tts_ok,
            )
            from engines.dubbing_engine.tts_handoff_diag import log_tts_generated

            with STATE_LOCK:
                _tts_info = dict(task.get("info") or {})
            log_tts_generated(
                task_id,
                tts_files=tts_files,
                segments_data=segments_data,
                artifacts_dir=_artifacts_dir(_tts_info),
            )
            from engines.voice_style_fx import apply_voice_fx_to_segment_files

            with STATE_LOCK:
                style_cfg = _style_params_from_info(task.get("info"))
            fx_count = apply_voice_fx_to_segment_files(
                segments_data, OUTPUT_DIR, style_cfg.get("voice_fx")
            )
            if fx_count:
                logger.info("Task %s: voice style FX applied to %d segments", task_id, fx_count)

            _pi_tts.register_tts_artifacts(
                segments_data,
                resolve_path=_resolve_segment_audio_path,
                task_info=_tts_info,
            )

            # PSA2 IdentityGuard — assert chain after TTS synthesis
            try:
                from engines.pipeline_integrity.identity_guard import (
                    run_identity_guard,
                )

                with STATE_LOCK:
                    run_identity_guard(
                        segments_data,
                        stage="post_tts",
                        task_info=task["info"],
                        require_wav=False,
                    )
            except Exception as _ig_post_exc:
                from engines.pipeline_integrity.exceptions import (
                    IdentityMismatchError,
                )

                if isinstance(_ig_post_exc, IdentityMismatchError):
                    raise
                logger.warning(
                    "Task %s: IdentityGuard post_tts soft-fail: %s",
                    task_id,
                    _ig_post_exc,
                )

            with STATE_LOCK:
                timing_map_for_fit = copy.deepcopy(
                    task["info"].get("timing_map_backup") or current_timing_map_snapshot
                )

            with STATE_LOCK:
                _qa_info = dict(task.get("info") or {})
            timing_map_for_fit, _post_tts_stats = _post_tts_timing_qa(
                task_id,
                segments_data,
                timing_map_for_fit,
                _qa_info,
                voice=voice,
                target_lang=target_lang,
                src_lang=str(_qa_info.get("detected_lang") or _qa_info.get("source_lang") or "en"),
                tts_rate=tts_rate,
                tts_pitch=tts_pitch,
            )
            with STATE_LOCK:
                task["info"]["timing_map_backup"] = timing_map_for_fit
                task["info"]["post_tts_qa"] = _post_tts_stats
                # _qa_info is a shallow copy of task["info"]; propagate the keys
                # that _post_tts_timing_qa writes back onto the real task info so
                # the OpenDDF report reflects the ACTUAL post-TTS LLM journal
                # (otherwise llm_calls/llm_status stay empty → false
                # LLM_NOT_CALLED / LLM_ADAPTATION_FAILED diagnostics).
                for _k in (
                    "llm_calls",
                    "llm_status",
                    "timing_joint_fixes",
                    "timing_report",
                    "timing_report_path",
                    "timeline_validation",
                    "adaptive_resegment_post_tts",
                    "source_segments",
                    "translation_audits",
                ):
                    if _k in _qa_info:
                        task["info"][_k] = _qa_info[_k]

            # PSA2 IdentityGuard — after regen / resegment closed-loop
            try:
                from engines.pipeline_integrity.identity_guard import (
                    run_identity_guard,
                )

                with STATE_LOCK:
                    run_identity_guard(
                        segments_data,
                        stage="post_regen_resegment",
                        task_info=task["info"],
                        require_wav=False,
                    )
            except Exception as _ig_rr_exc:
                from engines.pipeline_integrity.exceptions import (
                    IdentityMismatchError,
                )

                if isinstance(_ig_rr_exc, IdentityMismatchError):
                    raise
                logger.warning(
                    "Task %s: IdentityGuard post_regen_resegment soft-fail: %s",
                    task_id,
                    _ig_rr_exc,
                )

            # TZ §1-§4: two explicit branches, NO hidden fallback.
            #   • strict mode    → segments that still require LLM adaptation stop
            #                      the pipeline with an actionable diagnostic.
            #   • automatic mode → surface a user-visible quality warning and
            #                      continue (overflow handled by gap/video-adapt,
            #                      never silent truncation).
            from engines.llm_adaptation_mode import (
                MODE_STRICT,
                build_stop_diagnostics,
                detect_capabilities,
                resolve_adaptation_mode,
            )

            _llm_gate = (_post_tts_stats or {}).get("requires_llm_adaptation") or {}
            with STATE_LOCK:
                _adapt_mode = resolve_adaptation_mode(task.get("info") or {})
                task["info"]["adaptation_mode"] = _adapt_mode
            _simple_soft = False
            try:
                from engines.simple_dub_pipeline import is_simple_pipeline as _isp

                _simple_soft = bool(_isp(task.get("info") or {}))
            except Exception:
                _simple_soft = bool(
                    (task.get("info") or {}).get("simple_pipeline")
                    or (task.get("info") or {}).get("happy_path")
                )
            _soft_unresolved = bool(
                _llm_gate.get("soft_complete")
                or str(_llm_gate.get("reason") or "") == "closed_loop_unresolved"
                or (task.get("info") or {}).get("closed_loop_unresolved_soft")
            )
            if _simple_soft:
                _adapt_mode = "automatic"
            if _simple_soft or _soft_unresolved:
                with STATE_LOCK:
                    if _simple_soft:
                        task["info"]["adaptation_mode"] = _adapt_mode
                    task["info"]["closed_loop_unresolved_soft"] = True
            if _llm_gate.get("count"):
                _idxs = _llm_gate.get("segment_indices") or []
                _caps = detect_capabilities()
                _stop_diag = build_stop_diagnostics(
                    mode=_adapt_mode,
                    reason=str(_llm_gate.get("reason") or "requires_llm_adaptation"),
                    pending_indices=list(_idxs),
                    total_segments=len(segments_data),
                    capabilities=_caps,
                )
                with STATE_LOCK:
                    task["info"]["llm_gate_diagnostics"] = _stop_diag
                if _adapt_mode == MODE_STRICT and not _simple_soft and not _soft_unresolved:
                    from engines.dubbing_engine.pipeline_failure_diag import fail_pipeline

                    reason = (
                        f"Строгий режим: {_llm_gate.get('count')} сегмент(ов) требуют "
                        f"интеллектуальной адаптации, но она не выполнена ({_llm_gate.get('reason')}). "
                        f"Сегменты: {_idxs[:20]}. Передача неадаптированного текста в "
                        f"TTS/Slot Fit запрещена."
                    )
                    logger.error(
                        "Task %s: STRICT LLM gate blocked pipeline — %d segments "
                        "require adaptation: %s",
                        task_id,
                        _llm_gate.get("count"),
                        _idxs[:20],
                    )
                    fail_pipeline(
                        task_id,
                        reason,
                        stage="POST_TTS_QA",
                        error_code="REQUIRES_LLM_ADAPTATION",
                    )
                    return
                # Automatic mode: explicit, user-visible degradation warning.
                _warn = (
                    f"{_llm_gate.get('count')} сегмент(ов) не удалось адаптировать "
                    f"({_llm_gate.get('reason')}). Дубляж продолжен в упрощённом "
                    f"режиме. Для максимального качества установите AI-модуль TubeDub "
                    f"или включите строгий режим в настройках."
                )
                logger.warning("Task %s: AUTOMATIC mode degraded — %s", task_id, _warn)
                with STATE_LOCK:
                    _warns = task["info"].setdefault("user_warnings", [])
                    _warns.append({"stage": "POST_TTS_QA", "code": "LLM_ADAPTATION_DEGRADED", "message": _warn})

            log_stage_end(task_id, "POST_TTS_QA")
            log_stage_transition(task_id, "POST_TTS_QA", "SLOT_FIT")
            log_stage_begin(task_id, "SLOT_FIT")

            _update_progress_detail(
                task_id,
                phase="slot_fit",
                total_segments=len(segments_data),
                segments_done=0,
            )
            slot_fit_stats = {
                "total": 0,
                "already_fit": 0,
                "compressed": 0,
                "overflow": 0,
                "failed": 0,
                "enabled": False,
            }
            # slot_fit is a core dubbing step — always enabled regardless of soft_sync/word_timing flags
            slot_fit_enabled = True
            _pi_sf = _integrity_coordinator(task_id)
            _pi_sf.begin_stage("slot_fit", segments_data)

            if slot_fit_enabled:
                if pipeline_timer is not None:
                    pipeline_timer.start("slot_fit")
                with STATE_LOCK:
                    _timing_agent_slot_fit = bool(
                        (task.get("info") or {}).get("timing_agent_path")
                    )
                    _slot_fit_info = dict(task.get("info") or {})
                    _project_locked = bool(
                        (task.get("info") or {}).get("translation_locked")
                    ) or str(
                        (task.get("info") or {}).get("pipeline_state") or ""
                    ).upper() in {
                        "LOCKED",
                        "TTS_READY",
                        "OPTIMIZED",
                        "SCHEDULED",
                        "MERGED",
                        "HANDOFF",
                        "EXPORTED",
                    }
                # MASTER TZ: after LOCK never compress/rewrite text in slot_fit
                _skip_text = True if _project_locked else _timing_agent_slot_fit
                slot_fit_stats = _pipeline_slot_fit_segments(
                    segments_data,
                    timing_map_for_fit,
                    voice=voice,
                    target_lang=target_lang,
                    source_segments=source_segments_snapshot,
                    tts_files=tts_files,
                    tts_rate=tts_rate,
                    tts_pitch=tts_pitch,
                    max_attempts=3,
                    task_id=task_id,
                    task_info=_slot_fit_info,
                    skip_text_compression=_skip_text,
                )
                slot_fit_stats["enabled"] = True
                if pipeline_timer is not None:
                    pipeline_timer.stop("slot_fit")
            # Close slot_fit snapshot BEFORE AudioTimingOptimizer (P25 stage boundary)
            _pi_sf.end_stage(
                "slot_fit",
                segments_data,
                timing_map=timing_map_for_fit,
            )
            logger.info(
                "Task %s: slot_fit total=%d compressed=%d overflow=%d already_fit=%d",
                task_id,
                slot_fit_stats.get("total", 0),
                slot_fit_stats.get("compressed", 0),
                slot_fit_stats.get("overflow", 0),
                slot_fit_stats.get("already_fit", 0),
            )

            # ── PSA6: Overflow honesty + LOCK ordering (no DSAL/MF algo) ──
            try:
                from engines.pipeline_integrity.identity_guard import (
                    run_identity_guard,
                )
                from engines.pipeline_integrity.overflow_inspector import (
                    apply_psa6_lock_ordering,
                )

                with STATE_LOCK:
                    _ov_info = task["info"]
                    _sb_ok = bool(_ov_info.get("slot_budget_ok", True))
                _psa6 = apply_psa6_lock_ordering(
                    _ov_info,
                    segments_data,
                    slot_budget_ok=_sb_ok,
                )
                _ov_report = _psa6.get("overflow") or {}
                _lock_res = _psa6.get("late_lock") or {}
                run_identity_guard(
                    segments_data,
                    stage="post_slot_fit",
                    task_info=_ov_info,
                    require_wav=True,
                )
                # PSA7 — honest diagnostics / stability metrics (no algo change)
                try:
                    from engines.pipeline_integrity.honest_diagnostics import (
                        collect_stability_metrics,
                    )

                    _stab = collect_stability_metrics(
                        segments_data, task_info=_ov_info
                    )
                except Exception as _hd_exc:
                    logger.debug(
                        "Task %s: PSA7 stability metrics skipped: %s",
                        task_id,
                        _hd_exc,
                    )
                    _stab = {}

                with STATE_LOCK:
                    task["info"]["overflow_inspector"] = _ov_report
                    task["info"]["late_translation_lock"] = _lock_res
                    task["info"]["psa6_lock_ordering"] = {
                        "meaning_fit_call_point": _psa6.get(
                            "meaning_fit_call_point"
                        ),
                        "manual_review_call_point": _psa6.get(
                            "manual_review_call_point"
                        ),
                        "pipeline_success_allowed": _psa6.get(
                            "pipeline_success_allowed"
                        ),
                        "translation_locked": _psa6.get("translation_locked"),
                    }
                    if _stab:
                        task["info"]["stability_metrics"] = _stab
                    task["info"]["segments_data"] = segments_data
                logger.info(
                    "Task %s: PSA6 OverflowInspector critical=%s "
                    "late_lock=%s meaning_fit_cp=%s success_allowed=%s "
                    "PSA7 metrics id_mm=%s micro=%s resid_ov=%s place_ov=%s",
                    task_id,
                    _ov_report.get("critical"),
                    _lock_res.get("locked"),
                    _psa6.get("meaning_fit_call_point"),
                    _psa6.get("pipeline_success_allowed"),
                    (_stab or {}).get("identity_mismatch_count"),
                    (_stab or {}).get("micro_slot_count"),
                    (_stab or {}).get("residual_overflow_count"),
                    (_stab or {}).get("placement_overlap_count"),
                )
            except Exception as _ov_exc:
                from engines.pipeline_integrity.exceptions import (
                    IdentityMismatchError,
                )

                if isinstance(_ov_exc, IdentityMismatchError):
                    raise
                logger.warning(
                    "Task %s: OverflowInspector/late-lock skipped: %s",
                    task_id,
                    _ov_exc,
                )

            # Freeze P2/P3/P4: audio-only optimizer + UUID chain + metrics after slot_fit
            # Stamp revision/uuid_chain BEFORE snapshot begin so audio_timing
            # does not look like a forbidden first-write of adaptation_uuid.
            try:
                from engines.pipeline_integrity.uuid_chain import (
                    ensure_project_uuids,
                    ensure_tts_uuid,
                )

                ensure_project_uuids(segments_data)
                for _seg_pre in segments_data:
                    if isinstance(_seg_pre, dict) and not (
                        _seg_pre.get("merged_into") or _seg_pre.get("merged_into_id")
                    ):
                        ensure_tts_uuid(_seg_pre)
            except Exception as _uuid_pre_exc:
                logger.debug(
                    "Task %s: pre-audio_timing uuid stamp skipped: %s",
                    task_id,
                    _uuid_pre_exc,
                )

            _pi_sf.begin_stage("audio_timing", segments_data)
            try:
                from engines.audio_timing_optimizer import optimize_audio_timing
                from engines.pipeline_integrity.error_taxonomy import (
                    DubMetrics,
                    stamp_metrics,
                )
                from engines.pipeline_integrity.tts_artifact_lifecycle import (
                    TTSLifecycleState,
                    advance_tts_lifecycle,
                    get_tts_lifecycle,
                )
                from engines.pipeline_integrity.uuid_chain import (
                    ensure_project_uuids,
                    ensure_tts_uuid,
                )
                from engines.perf_budgets import measure_budget

                with STATE_LOCK:
                    _opt_info = task["info"]
                with measure_budget("scheduler", enforce=False) as _sched_sample:
                    _opt_result = optimize_audio_timing(
                        segments_data,
                        info=_opt_info,
                        settings={
                            "voice": voice,
                            "target_lang": target_lang,
                            "task_id": task_id,
                        },
                    )
                try:
                    from engines.pipeline_integrity.pipeline_state import (
                        PipelineState,
                        advance_pipeline_state,
                        get_pipeline_state,
                    )

                    if get_pipeline_state(_opt_info) == PipelineState.TTS_READY:
                        advance_pipeline_state(_opt_info, PipelineState.OPTIMIZED)
                        with STATE_LOCK:
                            task["info"]["pipeline_state"] = _opt_info.get(
                                "pipeline_state"
                            )
                except Exception as _fsm_exc:
                    logger.debug(
                        "Task %s: OPTIMIZED state advance skipped: %s",
                        task_id,
                        _fsm_exc,
                    )
                ensure_project_uuids(segments_data)
                for _seg in segments_data:
                    if not isinstance(_seg, dict):
                        continue
                    if _seg.get("merged_into") or _seg.get("merged_into_id"):
                        continue
                    ensure_tts_uuid(_seg)
                    st = get_tts_lifecycle(_seg)
                    # Best-effort advance toward Scheduled after slot_fit (P3.1 path)
                    try:
                        if st == TTSLifecycleState.CREATED:
                            advance_tts_lifecycle(
                                _seg, TTSLifecycleState.QUEUED, task_id=task_id
                            )
                            st = get_tts_lifecycle(_seg)
                        if st == TTSLifecycleState.QUEUED and (
                            _seg.get("file") or _seg.get("tts_file_path")
                        ):
                            advance_tts_lifecycle(
                                _seg, TTSLifecycleState.SYNTHESIZED, task_id=task_id
                            )
                            st = get_tts_lifecycle(_seg)
                        if st == TTSLifecycleState.SYNTHESIZED:
                            advance_tts_lifecycle(
                                _seg, TTSLifecycleState.VERIFIED, task_id=task_id
                            )
                            st = get_tts_lifecycle(_seg)
                        if st == TTSLifecycleState.VERIFIED:
                            advance_tts_lifecycle(
                                _seg, TTSLifecycleState.STORED, task_id=task_id
                            )
                            st = get_tts_lifecycle(_seg)
                        if st == TTSLifecycleState.STORED:
                            advance_tts_lifecycle(
                                _seg, TTSLifecycleState.SCHEDULED, task_id=task_id
                            )
                    except Exception:
                        pass
                stamp_metrics(_opt_info, DubMetrics.from_optimizer(_opt_result.metrics))
                _opt_info["scheduler_budget_sample"] = _sched_sample.to_dict()
                logger.info(
                    "Task %s: AudioTimingOptimizer levels=%s overflow=%s fingerprint=%s",
                    task_id,
                    _opt_result.levels_applied,
                    _opt_result.overflow,
                    (_opt_result.fingerprint or "")[:16],
                )
            except Exception as _opt_exc:
                logger.warning(
                    "Task %s: AudioTimingOptimizer skipped: %s", task_id, _opt_exc
                )
            _open_ddf.record_agent(
                task_id, "SlotFit", called=True,
                success=slot_fit_stats.get("failed", 0) == 0,
                decision=(
                    f"total={slot_fit_stats.get('total',0)} "
                    f"compressed={slot_fit_stats.get('compressed',0)} "
                    f"overflow={slot_fit_stats.get('overflow',0)}"
                ),
            )

            log_stage_end(task_id, "SLOT_FIT")
            log_stage_transition(task_id, "SLOT_FIT", "STUDIO")
            log_stage_begin(task_id, "STUDIO")

            # Collect video_stretch_segments for DubEngine (segments where video
            # should be slowed to match longer-than-slot TTS audio).
            video_stretch_segs: list[dict] = []
            for idx, seg in enumerate(segments_data):
                if seg.get("merged_into") is not None:
                    continue
                vsr = float(seg.get("video_stretch_ratio", 1.0))
                if vsr > 1.01 and seg.get("video_adapt_mode"):
                    s_ms = int(seg.get("start_ms", 0))
                    e_ms = int(seg.get("end_ms", s_ms))
                    video_stretch_segs.append({
                        "start_ms": s_ms,
                        "end_ms": e_ms,
                        "stretch_ratio": round(vsr, 4),
                    })

            # ── Speech speed equalization ─────────────────────────────────────
            # After slot_fit, normalize atempo across segments so no single
            # segment sounds rushed while its neighbours are slow.
            _equalize_speech_speeds(segments_data, timing_map_for_fit)

            # Audio identity preflight — MASTER TZ v3.0 hard-fail on duplicates.
            # Unique basenames must be allocated at TTS time; repair is not silent OK.
            from engines.pipeline_integrity.audio_identity import (
                ensure_unique_before_handoff,
            )

            with STATE_LOCK:
                _handoff_info = dict(task.get("info") or {})
            try:
                _identity = ensure_unique_before_handoff(
                    segments_data,
                    resolve_path=lambda p: _resolve_segment_audio_path(
                        p, _handoff_info
                    ),
                    dest_dir=_artifacts_dir(_handoff_info),
                    run_id=task_id,
                    app_dir=APP_DIR,
                    hard_fail=True,
                )
                with STATE_LOCK:
                    task["info"]["audio_registry"] = _identity.get("registry")
                    task["info"]["audio_identity_report"] = _identity.get("report")
                    task["info"]["audio_identity_repairs"] = _identity.get("repairs")
                    if _identity.get("paths"):
                        task["info"]["audio_identity_paths"] = _identity["paths"]
                if _identity.get("repairs"):
                    logger.warning(
                        "Task %s: repaired %d duplicate TTS filename(s) before handoff",
                        task_id,
                        len(_identity["repairs"]),
                    )
                if not _identity.get("ok"):
                    logger.error(
                        "Task %s: audio identity still invalid after repair: %s",
                        task_id,
                        _identity.get("validation"),
                    )
            except Exception as _id_exc:
                logger.warning("Task %s: audio identity preflight failed: %s", task_id, _id_exc)

            _pi_sf.end_stage(
                "audio_timing",
                segments_data,
                timing_map=timing_map_for_fit,
            )
            with STATE_LOCK:
                _handoff_info = dict(task.get("info") or {})
            # Last-chance repair: split children without WAV must not reach Studio.
            try:
                _handoff_repair = _repair_missing_tts_files(
                    segments_data,
                    voice=voice,
                    task_info=_handoff_info,
                    task_id=task_id,
                    tts_rate=tts_rate,
                    tts_pitch=tts_pitch,
                )
                with STATE_LOCK:
                    task["info"]["missing_tts_repair_handoff"] = _handoff_repair
            except Exception as _rep_exc:
                logger.warning(
                    "Task %s: pre-handoff missing-TTS repair failed: %s",
                    task_id,
                    _rep_exc,
                )
            _handoff_info["segments_data"] = segments_data
            _pi_sf.validate_pipeline(
                segments_data,
                timing_map_for_fit,
                stage="studio_handoff",
                task_info=_handoff_info,
                resolve_audio=_resolve_segment_audio_path,
            )
            # TZ v2 P3/P4: Studio forbidden if physical audio files missing.
            try:
                from engines.pipeline_integrity.runtime_validator import (
                    assert_studio_handoff_wavs,
                    enforce_runtime,
                )
                from engines.pipeline_integrity.exceptions import RuntimeIntegrityError
                from engines.pipeline_integrity.crash_recovery import save_checkpoint
                from engines.pipeline_integrity.pipeline_state import (
                    PipelineState,
                    advance_pipeline_state,
                    get_pipeline_state,
                )

                assert_studio_handoff_wavs(
                    _handoff_info,
                    resolve_audio=_resolve_segment_audio_path,
                )
                enforce_runtime(
                    _handoff_info,
                    stage="studio_handoff",
                    require_tts=True,
                    output_dir=APP_DIR / "output" / "diagnostics" / task_id,
                    resolve_audio=_resolve_segment_audio_path,
                )
                # P3.1: sync Runtime Registry + advance lifecycle to HandoffReady
                try:
                    from engines.pipeline_integrity.runtime_registry import (
                        get_or_create_registry,
                    )
                    from engines.pipeline_integrity.tts_artifact_lifecycle import (
                        TTSLifecycleState as _TLS,
                        advance_tts_lifecycle as _adv_lc,
                        get_tts_lifecycle as _get_lc,
                    )
                    from engines.pipeline_integrity.wav_ownership import stamp_wav_owner

                    _reg = get_or_create_registry(_handoff_info)
                    for _hs in segments_data:
                        if not isinstance(_hs, dict):
                            continue
                        if _hs.get("merged_into") or _hs.get("merged_into_id"):
                            continue
                        stamp_wav_owner(_hs)
                        _st = _get_lc(_hs)
                        if _st == _TLS.SCHEDULED:
                            _adv_lc(_hs, _TLS.MERGED, task_id=task_id)
                            _st = _get_lc(_hs)
                        if _st == _TLS.MERGED:
                            _adv_lc(_hs, _TLS.HANDOFF_READY, task_id=task_id)
                        _reg.upsert_from_segment(
                            _hs,
                            actor="studio_handoff",
                            compute_hash=False,
                        )
                    _reg_path = (
                        APP_DIR / "output" / "diagnostics" / task_id / "runtime_registry.json"
                    )
                    _reg.save(_reg_path)
                    _handoff_info["runtime_registry_path"] = str(_reg_path)
                except Exception as _reg_exc:
                    logger.warning(
                        "Task %s: runtime registry sync skipped: %s", task_id, _reg_exc
                    )
                if get_pipeline_state(_handoff_info) == PipelineState.MERGED:
                    advance_pipeline_state(_handoff_info, PipelineState.HANDOFF)
                elif get_pipeline_state(_handoff_info) == PipelineState.SCHEDULED:
                    advance_pipeline_state(_handoff_info, PipelineState.MERGED)
                    advance_pipeline_state(_handoff_info, PipelineState.HANDOFF)
                sess = _handoff_info.get("session_dir") or (
                    APP_DIR / "output" / "sessions" / task_id
                )
                save_checkpoint(sess, _handoff_info, stage="handoff")
            except Exception as _rt_exc:
                from engines.pipeline_integrity.exceptions import RuntimeIntegrityError as _RIE

                if isinstance(_rt_exc, _RIE):
                    raise
                logger.warning(
                    "Task %s: runtime handoff checks skipped: %s", task_id, _rt_exc
                )

            with STATE_LOCK:
                task["info"]["segments_data"] = segments_data
                task["info"]["slot_fit_stats"] = slot_fit_stats
                task["info"]["video_stretch_segments"] = video_stretch_segs
                task["info"]["tts_files"] = list(dict.fromkeys(tts_files))
                task["info"]["mux_base_id"] = base_id
                task["info"]["extracted_audio_path"] = audio_path
                task["info"]["pipeline_integrity"] = _pi_sf.to_dict()
                if _handoff_info.get("runtime_registry_path"):
                    task["info"]["runtime_registry_path"] = _handoff_info[
                        "runtime_registry_path"
                    ]
                if _handoff_info.get("pipeline_state"):
                    task["info"]["pipeline_state"] = _handoff_info["pipeline_state"]
                touch_task(task_id)

            # TZ Root Cause Audit — snapshot after slot_fit / before StudioReady (no mutation)
            try:
                from engines.pipeline_integrity.timing_lifecycle_audit import (
                    dump_pre_merge_timing_audit,
                )

                _audit = dump_pre_merge_timing_audit(
                    segments_data,
                    task_id=task_id,
                    timing_map=timing_map_for_fit,
                    source="post_slot_fit_pre_studio",
                )
                with STATE_LOCK:
                    task["info"]["timing_lifecycle_audit"] = _audit.get("summary") or {}
            except Exception:
                pass
            if project_session:
                project_session.store_pipeline_state(
                    segments=segments_data,
                    source_segments=source_segments_snapshot,
                    timing_map=timing_map_for_fit,
                    translations=segments,
                )

            from engines.dubbing_engine.tts_handoff_diag import log_pipeline_handoff

            with STATE_LOCK:
                _handoff_info = dict(task.get("info") or {})
            log_pipeline_handoff(
                task_id,
                project_session=project_session,
                task_info=_handoff_info,
                segments_data=segments_data,
            )

            from engines.pipeline_language_gate import (
                heal_phrase_loops_in_segments,
                log_segment_pipeline_trace,
                validate_segments_target_language,
            )

            # Closed-loop / Argos can leave «у той момент»×N in final text after TTS.
            # Deflate before STUDIO language gate; re-TTS healed rows so audio matches.
            _loop_healed = heal_phrase_loops_in_segments(
                segments_data,
                source_segments=source_segments_snapshot,
                target_lang=target_lang,
                source_lang=translation_source_lang,
            )
            if _loop_healed:
                logger.info(
                    "Task %s: phrase-loop heal before STUDIO gate — segs %s",
                    task_id,
                    _loop_healed,
                )
                with STATE_LOCK:
                    _aud_loop = task["info"].get("translation_audits") or []
                _aud_by_loop = {
                    int(a.get("index", -1)): a for a in _aud_loop if isinstance(a, dict)
                }
                for _hi in _loop_healed:
                    if not (0 <= _hi < len(segments_data)):
                        continue
                    _hseg = segments_data[_hi]
                    if not isinstance(_hseg, dict):
                        continue
                    _htext = str(
                        _hseg.get("text") or _hseg.get("plain_text") or ""
                    ).strip()
                    if not _htext:
                        continue
                    _arow = _aud_by_loop.get(_hi)
                    if _arow is not None:
                        _arow["tts_text"] = _htext
                        _arow["final_text"] = _htext
                        _arow["naturalized_text"] = _htext
                        _arow["phrase_loop_healed"] = True
                    _hvoice = str(
                        _hseg.get("assigned_voice")
                        or _hseg.get("voice")
                        or voice
                        or ""
                    )
                    if not _hvoice:
                        continue
                    try:
                        _hfile = _regen_segment_tts_simple(
                            _htext,
                            _hvoice,
                            tts_rate=tts_rate,
                            tts_pitch=tts_pitch,
                            segment_id=str(
                                _hseg.get("segment_id")
                                or _hseg.get("segment_uuid")
                                or ""
                            ),
                            task_id=task_id,
                        )
                    except Exception as _hexc:
                        logger.warning(
                            "Task %s: phrase-loop re-TTS seg #%s failed: %s",
                            task_id,
                            _hi,
                            _hexc,
                        )
                        _hfile = None
                    if _hfile:
                        _hseg["file"] = _hfile
                        _hseg["tts_file_path"] = _hfile
                        _hseg["tts_status"] = "generated"
                        _hseg["tts_text"] = _htext
                        _hseg.pop("fitted_file", None)
                        _identity_bind_after_regen(
                            _hseg,
                            _htext,
                            _hfile,
                            segments_data=segments_data,
                            stage="phrase_loop_regen",
                        )
                with STATE_LOCK:
                    task["info"]["segments_data"] = segments_data
                    task["info"]["phrase_loop_healed_indices"] = list(_loop_healed)

            # Unified Language Validation + Recovery (TZ P0): never hard-stop on
            # expected==detected / phrase_loop / recoverable meaning_collapse.
            def _studio_re_tts(idx: int, seg: dict) -> None:
                _htext = str(seg.get("text") or seg.get("plain_text") or "").strip()
                if not _htext:
                    return
                _hvoice = str(
                    seg.get("assigned_voice") or seg.get("voice") or voice or ""
                )
                if not _hvoice:
                    return
                try:
                    _hfile = _regen_segment_tts_simple(
                        _htext,
                        _hvoice,
                        tts_rate=tts_rate,
                        tts_pitch=tts_pitch,
                        segment_id=str(
                            seg.get("segment_id") or seg.get("segment_uuid") or ""
                        ),
                        task_id=task_id,
                    )
                except Exception as _hexc:
                    logger.warning(
                        "Task %s: recovery re-TTS seg #%s failed: %s",
                        task_id,
                        idx,
                        _hexc,
                    )
                    return
                if _hfile:
                    seg["file"] = _hfile
                    seg["tts_file_path"] = _hfile
                    seg["tts_status"] = "generated"
                    seg["tts_text"] = _htext
                    seg.pop("fitted_file", None)
                    _identity_bind_after_regen(
                        seg,
                        _htext,
                        _hfile,
                        segments_data=segments_data,
                        stage="recovery_regen",
                    )

            from engines.language_validation.recovery import (
                apply_recovery_and_revalidate,
            )
            from engines.language_validation.diagnostics import (
                write_language_validation_diagnostics,
            )

            _lang_recovery = apply_recovery_and_revalidate(
                segments_data,
                source_segments=source_segments_snapshot,
                target_lang=target_lang,
                source_lang=translation_source_lang,
                stage="STUDIO",
                on_healed=_studio_re_tts,
            )
            try:
                write_language_validation_diagnostics(
                    task_id=task_id,
                    app_dir=APP_DIR,
                    stage="STUDIO",
                    decisions=_lang_recovery.get("decisions") or [],
                    recovery=_lang_recovery,
                )
            except Exception as _diag_exc:
                logger.debug("language diagnostics skipped: %s", _diag_exc)
            with STATE_LOCK:
                task["info"]["segments_data"] = segments_data
                task["info"]["language_recovery"] = {
                    "healed": _lang_recovery.get("healed_indices"),
                    "failed_hard": _lang_recovery.get("failed_hard"),
                    "recovered": _lang_recovery.get("recovered"),
                }

            log_segment_pipeline_trace(
                task_id,
                segments_data,
                source_segments=source_segments_snapshot,
                target_lang=target_lang,
                audits=task.get("info", {}).get("translation_audits"),
            )
            lang_issues = validate_segments_target_language(
                segments_data,
                source_segments=source_segments_snapshot,
                target_lang=target_lang,
                source_lang=translation_source_lang,
                stage="STUDIO",
                hard_only=True,
            )
            # Also include any remaining hard fails from recovery result
            for _hf in _lang_recovery.get("still_hard") or []:
                if not any(
                    int(x.get("index", -1)) == int(_hf.get("index", -2))
                    for x in lang_issues
                ):
                    lang_issues.append(_hf)
            if lang_issues:
                first = lang_issues[0]
                from engines.dubbing_engine.pipeline_failure_diag import fail_pipeline
                from engines.language_validation.service import format_validation_message
                from engines.language_validation.service import LanguageValidationDecision

                _msg = str(first.get("message") or "")
                if not _msg:
                    try:
                        _msg = format_validation_message(
                            LanguageValidationDecision(
                                ok=False,
                                hard_fail=True,
                                category=str(first.get("category") or "language_mismatch"),
                                code=str(first.get("code") or "language_mismatch"),
                                expected_lang=str(
                                    first.get("target_lang") or target_lang
                                ),
                                detected_lang=str(first.get("detected_lang") or "?"),
                                confidence=float(first.get("confidence") or 0.0),
                                target_confidence=float(
                                    first.get("target_confidence") or 0.0
                                ),
                                scores=dict(first.get("scores") or {}),
                                reasons=list(first.get("reasons") or []),
                                text_checked=str(first.get("final_preview") or ""),
                                stage="STUDIO",
                                recovery_actions=list(
                                    first.get("recovery_actions") or ["recovery_exhausted"]
                                ),
                            )
                        )
                    except Exception:
                        _msg = (
                            f"Language Validation failed. Expected: {target_lang}. "
                            f"Detected: {first.get('detected_lang')} "
                            f"(confidence {first.get('confidence', '?')}). "
                            f"Code: {first.get('code')}. "
                            f"Preview: {str(first.get('final_preview') or '')[:120]}"
                        )
                error_code = (
                    "LANGUAGE_MISMATCH"
                    if str(first.get("category") or "") == "language_mismatch"
                    else str(first.get("code") or "LANGUAGE_VALIDATION_FAILED").upper()
                )
                logger.error(
                    "Task %s: language gate failed after recovery — %d issues, "
                    "first idx=%s code=%s category=%s",
                    task_id,
                    len(lang_issues),
                    first.get("index"),
                    first.get("code"),
                    first.get("category"),
                )
                fail_pipeline(
                    task_id,
                    _msg,
                    stage="STUDIO",
                    error_code=error_code,
                )
                return

            with STATE_LOCK:
                _vv_info = dict(task.get("info") or {})
            # Stage 8: Simple — no post-TTS re-STT / voice-verification ASR.
            _skip_vv = bool(
                _vv_info.get("simple_pipeline")
                or _vv_info.get("happy_path")
                or _vv_info.get("simple_stt_locked")
                or _vv_info.get("voice_verification_asr_allowed") is False
                or _vv_info.get("post_tts_restt_allowed") is False
            )
            if _skip_vv:
                with STATE_LOCK:
                    task["info"]["voice_verification_skipped"] = "simple_stt_lock"
                logger.info(
                    "Task %s: voice_verification ASR skipped (Simple STT lock)",
                    task_id,
                )
            else:
                try:
                    _run_voice_verification_for_task(
                        task_id=task_id,
                        segments_data=segments_data,
                        task_info=_vv_info,
                        voice=voice,
                        target_lang=target_lang,
                        tts_rate=tts_rate,
                        tts_pitch=tts_pitch,
                        manifest_path=str(_vv_info.get("manifest_path") or ""),
                    )
                    with STATE_LOCK:
                        task["info"].update(_vv_info)
                        task["info"]["segments_data"] = segments_data
                except Exception as vv_exc:
                    logger.warning(
                        "Task %s: voice_verification skipped: %s", task_id, vv_exc
                    )

            try:
                from engines.autodub.project_package import build_autodub_project_package

                with STATE_LOCK:
                    _pkg_info = dict(task.get("info") or {})
                build_autodub_project_package(APP_DIR, task_id, _pkg_info)
                with STATE_LOCK:
                    task["info"].update(_pkg_info)
            except Exception as pkg_exc:
                logger.debug("Task %s: project package skipped: %s", task_id, pkg_exc)

            try:
                # P3.1 §2: do not delete segment WAVs before EXPORTED.
                # Only scrub non-registered intermediate work dirs via CleanupManager.
                from engines.pipeline_integrity.cleanup_manager import CleanupManager

                with STATE_LOCK:
                    _cu_info = dict(task.get("info") or {})
                    _cu_info["segments_data"] = segments_data
                _cm = CleanupManager(_cu_info)
                # Never wipe mux inputs at handoff (older_than_sec=0 deleted pads).
                if _cm.pipeline_allows_cleanup():
                    _cm.cleanup_orphans(
                        [
                            APP_DIR / "output" / "slot_fit",
                            _artifacts_dir(_handoff_info),
                        ],
                        segments=segments_data,
                        older_than_sec=3600,
                        actor="handoff_intermediate",
                    )
            except Exception as cleanup_err:
                logger.debug(
                    "Task %s: intermediate cleanup skipped: %s",
                    task_id,
                    cleanup_err,
                )

            tts_placed = []
            for idx, seg in enumerate(segments_data):
                if seg.get("merged_into") is not None:
                    continue
                tts_placed.append(
                    {
                        "index": idx,
                        "file": seg.get("file"),
                        "text_len": len(str(seg.get("text") or "")),
                        "tts_timing": seg.get("tts_timing"),
                    }
                )
            dev_diag.log_tts(voice=voice, groups=tts_groups, segment_files=tts_placed)

            with STATE_LOCK:
                _op_info = dict(task.get("info") or {})
            try:
                from engines.segment_timing_qa import build_openddf_full_report

                _op_info["task_id"] = task_id
                openddf_report = build_openddf_full_report(_op_info)
                with STATE_LOCK:
                    task["info"]["openddf_full_report"] = openddf_report
            except Exception as odf_err:
                logger.debug("Task %s: openddf_full_report skipped: %s", task_id, odf_err)

            # TZ Dub Engine: forbid SUCCESS with overflow + adaptation_executed=false
            try:
                from engines.dub_engine_v2.overflow_strategy import (
                    UnhandledOverflowError,
                    assert_pipeline_may_succeed,
                    collect_unhandled_overflows,
                )

                _gate = assert_pipeline_may_succeed(segments_data)
                with STATE_LOCK:
                    task["info"]["overflow_success_gate"] = _gate
            except UnhandledOverflowError as _ov_err:
                _bad = list(getattr(_ov_err, "segments", []) or [])
                # Do NOT silently auto-heal without skip_reason — require real stamp.
                # Only heal when overflow_decision proves a strategy was chosen.
                for _b in _bad:
                    _i = int(_b.get("index", -1))
                    if 0 <= _i < len(segments_data):
                        _seg = segments_data[_i]
                        _chosen = str((_seg.get("overflow_decision") or {}).get("chosen") or "")
                        if _chosen and _chosen not in ("", "ready"):
                            from engines.dub_engine_v2.adaptation_decision import (
                                mark_adaptation_executed,
                            )

                            mark_adaptation_executed(
                                _seg,
                                decision=_chosen,
                                stages=[f"overflow_strategy:{_chosen}"],
                            )
                        else:
                            from engines.dub_engine_v2.adaptation_decision import (
                                finalize_segment_adaptation_fields,
                            )

                            finalize_segment_adaptation_fields(_seg, index=_i)
                _still = collect_unhandled_overflows(segments_data)
                _fatal = [
                    x
                    for x in _still
                    if not x.get("adaptation_executed")
                ]
                with STATE_LOCK:
                    task["info"]["overflow_success_gate"] = {
                        "ok": not _fatal,
                        "unhandled": _still,
                        "auto_healed": False,
                        "failure_code": "OverflowDetected_AdaptationSkipped" if _fatal else "",
                    }
                if _fatal:
                    from engines.dubbing_engine.pipeline_failure_diag import fail_pipeline

                    _reasons = ", ".join(
                        f"#{int(x.get('index', -1)) + 1}:{x.get('skip_reason') or 'UnknownSkip'}"
                        for x in _fatal[:8]
                    )
                    fail_pipeline(
                        task_id,
                        f"Pipeline FAILED: OverflowDetected + AdaptationSkipped "
                        f"({len(_fatal)} segment(s)). "
                        f"skip_reason: {_reasons}. SUCCESS запрещён.",
                        stage="OVERFLOW_SUCCESS_GATE",
                        error_code="UNHANDLED_OVERFLOW",
                    )
                    return

            # ══ НОВЫЙ ПОРЯДОК ПАЙПЛАЙНА: остановка после TTS/slot_fit ════════
            # Финальный микс запускается пользователем через кнопку «Свести проект»
            # в Studio: POST /api/studio/mix/<task_id>

            # ── OpenDDF: finalize report ──────────────────────────────────────
            _open_ddf.record_agent(task_id, "StudioReady", called=True, success=True,
                                   decision="pipeline_complete_before_mix")
            _ddf_saved_path = _open_ddf.save(task_id)
            _ddf_url = f"/api/ddf/{task_id}" if _ddf_saved_path else None
            with STATE_LOCK:
                _ddf_task = AUTO_TASKS.get(task_id)
                if _ddf_task:
                    _ddf_report = _open_ddf.get_report(task_id)
                    _ddf_task.setdefault("info", {})["ddf_url"] = _ddf_url
                    _ddf_task["info"]["ddf_warnings"] = (
                        _ddf_report.get("summary", {}).get("warnings", 0)
                    )
                    _ddf_task["info"]["ddf_failed_agents"] = (
                        _ddf_report.get("summary", {}).get("failed_agents", 0)
                    )
            # ─────────────────────────────────────────────────────────────────

            try:
                from api.studio_api import publish_studio_ready as _pub_studio_ready
                _pub_studio_ready(task_id)
                logger.info(
                    "Task %s: pipeline → studio_ready. "
                    "Финальный микс: POST /api/studio/mix/%s",
                    task_id,
                    task_id,
                )
            except Exception as studio_err:
                logger.warning(
                    "Task %s: publish_studio_ready failed: %s", task_id, studio_err
                )
                with STATE_LOCK:
                    _st = AUTO_TASKS.get(task_id)
                    if _st:
                        _st["status"] = "studio_ready"
                        _st["step"] = "studio"
                        _st["progress"] = 80.0

            _auto_mix_env = os.getenv("VM_AUTO_MIX", "").strip().lower() in (
                "1",
                "true",
                "yes",
                "on",
            )
            _simple_auto_mix = False
            try:
                from engines.simple_dub_pipeline import should_auto_mix_mp4

                with STATE_LOCK:
                    _mix_info = dict((AUTO_TASKS.get(task_id) or {}).get("info") or {})
                _simple_auto_mix = bool(should_auto_mix_mp4(_mix_info))
            except Exception:
                _simple_auto_mix = False
            try:
                from engines.core.feature_flags import IS_DEBUG_LEARNING_MODE as _idl

                if _idl() or _auto_mix_env or _simple_auto_mix:
                    from api.studio_api import run_studio_mix_internal

                    _mix_ok, _mix_out, _mix_errs = run_studio_mix_internal(
                        task_id, force=True
                    )
                    if _mix_ok and _mix_out:
                        logger.info(
                            "Task %s: %s mix complete → %s",
                            task_id,
                            "simple_auto" if _simple_auto_mix else "debug/auto",
                            _mix_out,
                        )
                        with STATE_LOCK:
                            _mt = AUTO_TASKS.get(task_id)
                            if _mt:
                                _mt["status"] = "done"
                                _mt["step"] = "done"
                                _mt["progress"] = 100.0
                                _mt["output_file"] = _mix_out
                                _mt.setdefault("info", {})["simple_auto_mix_done"] = bool(
                                    _simple_auto_mix
                                )
                        _open_ddf.save(task_id)
                        log_stage_end(task_id, "STUDIO")
                        log_stage_transition(task_id, "STUDIO", "MP4")
                        _wtm_record_checkpoint(wtm_cp_log, task_id, "auto_mix_done")
                        return
                    if _mix_errs:
                        logger.warning(
                            "Task %s: debug/auto mix failed: %s",
                            task_id,
                            _mix_errs[0],
                        )
            except Exception as auto_mix_err:
                logger.warning(
                    "Task %s: debug/auto mix skipped: %s", task_id, auto_mix_err
                )

            log_stage_end(task_id, "STUDIO")
            log_stage_transition(task_id, "STUDIO", "MP4")

            _wtm_record_checkpoint(wtm_cp_log, task_id, "studio_ready")
            return

            # ══ ШАГ 5 (DEPRECATED): Timing Engine — перенесён в /api/studio/mix ═
            if not _ensure_control(task_id, ui_lang):
                return
            _set_step(task_id, "timing", 82.0)
            profiler.start("timing")
            if pipeline_timer is not None:
                pipeline_timer.start("timing")

            with STATE_LOCK:
                timed_audio_path = task["info"].get("timed_audio")
                current_segments_snapshot = copy.deepcopy(task["info"]["segments_data"])
                current_timing_map_snapshot = copy.deepcopy(
                    task["info"]["timing_map_backup"]
                )
                style_cfg = _style_params_from_info(task["info"])
                target_duration = task["info"].get("target_duration_ms")

            timing_warnings = []
            overlap_report = {"ok": True}

            if not timed_audio_path:
                from engines.overlap_quality import (
                    analyze_placed_segments,
                    build_quality_report,
                )

                placed_count = sum(
                    1
                    for s in current_segments_snapshot
                    if s.get("file") and s.get("merged_into") is None
                )
                _update_progress_detail(
                    task_id,
                    phase="timing",
                    timing_substep="adapt",
                    total_segments=placed_count or len(current_segments_snapshot),
                    segments_done=0,
                )

                pre_issues = analyze_placed_segments(
                    current_segments_snapshot, current_timing_map_snapshot
                )
                with STATE_LOCK:
                    slot_fit_meta = copy.deepcopy(task["info"].get("slot_fit_stats") or {})
                slot_fit_ran = bool(slot_fit_meta.get("enabled"))
                unresolved_pre = sum(
                    1 for row in pre_issues if int(row.get("overflow_ms") or 0) > 40
                )
                if unresolved_pre > 0 and not slot_fit_ran:
                    _adaptive_dub_resolve(
                        current_segments_snapshot,
                        current_timing_map_snapshot,
                        voice,
                        source_segments_snapshot,
                        tts_files,
                        tts_rate=tts_rate,
                        tts_pitch=tts_pitch,
                        semantic_log=semantic_log,
                        tgt_lang=target_lang,
                        src_lang=translation_source_lang,
                        style_allow_atempo=bool(style_cfg.get("allow_atempo", True)),
                        task_id=task_id,
                    )
                elif unresolved_pre > 0 and slot_fit_ran:
                    logger.info(
                        "Task %s: skipping post-slot-fit adaptive regen (%d unresolved pre-mix overflows)",
                        task_id,
                        unresolved_pre,
                    )

                try:
                    from engines.overlap_quality import analyze_placed_segments as _aps

                    for row in _aps(current_segments_snapshot, current_timing_map_snapshot):
                        semantic_log.update_final_tts_ms(int(row["idx"]), int(row.get("tts_ms") or 0))
                    semantic_log.flush(
                        phase="post_tts_timing",
                        src=translation_source_lang,
                        tgt=target_lang,
                    )
                except Exception as sem_err:
                    logger.debug("semantic_adaptation log flush: %s", sem_err)

                def _timing_mix_progress(done: int, total: int) -> None:
                    frac = done / max(total, 1)
                    with STATE_LOCK:
                        if task_id in AUTO_TASKS:
                            AUTO_TASKS[task_id]["progress"] = round(90.0 + frac * 5.0, 1)
                    _update_progress_detail(
                        task_id,
                        phase="timing",
                        timing_substep="mix",
                        current_segment=done,
                        total_segments=total,
                        segments_done=done,
                    )

                _update_progress_detail(
                    task_id,
                    phase="timing",
                    timing_substep="mix",
                    segments_done=0,
                    total_segments=placed_count,
                )

                timed_audio_obj, timing_warnings, overlap_report = _build_timed_dub_track(
                    current_segments_snapshot,
                    current_timing_map_snapshot,
                    target_duration,
                    task_id,
                    style_params=style_cfg,
                    on_segment_progress=_timing_mix_progress,
                )
                # Stage 24: repaired/padded snapshot is source of truth — write back.
                with STATE_LOCK:
                    if task_id in AUTO_TASKS:
                        AUTO_TASKS[task_id]["info"]["segments_data"] = list(
                            current_segments_snapshot or []
                        )
                        AUTO_TASKS[task_id]["info"]["timing_map_backup"] = list(
                            current_timing_map_snapshot or []
                        )
                        if isinstance(overlap_report, dict):
                            AUTO_TASKS[task_id]["info"]["overlap_count"] = int(
                                overlap_report.get("overlap_count")
                                or overlap_report.get("fitted_overlap_count")
                                or AUTO_TASKS[task_id]["info"].get("overlap_count")
                                or 0
                            )
                if not overlap_report.get("ok"):
                    logger.info(
                        "Task %s: %d timing overlaps after mix (accepted)",
                        task_id,
                        overlap_report.get("fitted_overlap_count", 0),
                    )

                overlap_report = build_quality_report(
                    pre_issues,
                    overlap_report.get("unresolved_overlaps") or [],
                    overlap_report.get("fitted_placements") or [],
                )
                dev_diag.log_overlap_quality(overlap_report)

                if timed_audio_obj is not None:
                    timed_audio_path = str(_artifacts_dir(task.get("info")) / f"{base_id}_timed.mp3")
                    _update_progress_detail(
                        task_id,
                        phase="timing",
                        timing_substep="export",
                    )
                    with STATE_LOCK:
                        if task_id in AUTO_TASKS:
                            AUTO_TASKS[task_id]["progress"] = 95.0

                    export_ok = _safe_export_audio(timed_audio_obj, timed_audio_path)
                    if not export_ok or not Path(timed_audio_path).exists():
                        return _fail(
                            task_id,
                            [lp["export_error"]],
                            stage=STAGE_TIMING,
                            error_code="AUDIO_EXPORT_FAILED",
                        )

                    with STATE_LOCK:
                        task["info"]["timed_audio"] = timed_audio_path
                    # Stage 17–19: silence vs EN speech → hard-fail on Simple (never soft-swallow).
                    from engines.dead_air import (
                        DeadAirError,
                        append_dead_air_to_trace,
                        audit_dead_air_post_mux,
                        enforce_dead_air_or_fail,
                    )

                    try:
                        with STATE_LOCK:
                            _da_info = task.get("info") or {}
                            _da_voice = str(
                                _da_info.get("pipeline_voice")
                                or _da_info.get("tts_voice")
                                or voice
                                or ""
                            )
                            # Prefer post-repair snapshot (pads/abs paths), not stale live.
                            _da_sd = list(
                                current_segments_snapshot
                                or _da_info.get("segments_data")
                                or []
                            )
                            _da_simple = bool(
                                _da_info.get("simple_pipeline")
                                or _da_info.get("happy_path")
                            )
                        _da_report = audit_dead_air_post_mux(
                            timed_audio_path,
                            current_timing_map_snapshot,
                            segments_data=_da_sd,
                            fitted_placements=list(
                                (overlap_report or {}).get("fitted_placements") or []
                            ),
                            voice_id=_da_voice,
                            task_info=None,
                            simple_mode=_da_simple,
                            hard_fail=False,
                        )
                        _da_regions = list(_da_report.get("dead_air_regions") or [])
                        with STATE_LOCK:
                            task["info"]["dead_air_regions"] = _da_regions
                            task["info"]["dead_air_audit"] = {
                                "count": int(_da_report.get("dead_air_count") or 0),
                                "max_allowed_ms": int(
                                    _da_report.get("max_allowed_ms") or 350
                                ),
                                "en_speech_intervals": int(
                                    _da_report.get("en_speech_intervals") or 0
                                ),
                                "unresolved_segs": list(
                                    _da_report.get("dead_air_unresolved_segs") or []
                                ),
                            }
                            if _da_regions:
                                task["info"]["dead_air_warning"] = (
                                    f"PIPELINE_DEAD_AIR: {len(_da_regions)} silence "
                                    f"region(s) >350ms on EN-speech zones"
                                )
                            else:
                                task["info"].pop("dead_air_warning", None)
                            if _da_sd:
                                task["info"]["segments_data"] = _da_sd
                            _da_timing = list(
                                task["info"].get("timing_fit_segments") or []
                            )
                        _da_phase = "dead_air"
                        if _da_simple:
                            try:
                                enforce_dead_air_or_fail(
                                    _da_regions, simple_mode=True
                                )
                            except DeadAirError as _dae:
                                _da_phase = "dead_air_fail"
                                try:
                                    append_dead_air_to_trace(
                                        Path(__file__).resolve().parents[1],
                                        task_id=task_id,
                                        regions=_da_regions,
                                        timing_rows=_da_timing,
                                        voice_id=_da_voice,
                                        phase=_da_phase,
                                    )
                                except Exception:
                                    pass
                                return _fail(
                                    task_id,
                                    [str(_dae)],
                                    stage=STAGE_TIMING,
                                    error_code="PIPELINE_DEAD_AIR",
                                    exc=_dae,
                                )
                        try:
                            append_dead_air_to_trace(
                                Path(__file__).resolve().parents[1],
                                task_id=task_id,
                                regions=_da_regions,
                                timing_rows=_da_timing,
                                voice_id=_da_voice,
                                phase=_da_phase,
                            )
                        except Exception as _tr_exc:
                            logger.debug(
                                "Task %s: dead_air trace append soft-fail: %s",
                                task_id,
                                _tr_exc,
                            )
                        logger.info(
                            "Task %s: dead_air_regions=%d",
                            task_id,
                            int(_da_report.get("dead_air_count") or 0),
                        )
                    except DeadAirError as _dae:
                        return _fail(
                            task_id,
                            [str(_dae)],
                            stage=STAGE_TIMING,
                            error_code="PIPELINE_DEAD_AIR",
                            exc=_dae,
                        )
                    except Exception as _da_exc:
                        # Stage 19: never soft-swallow PIPELINE_DEAD_AIR strings.
                        if "PIPELINE_DEAD_AIR" in str(_da_exc):
                            return _fail(
                                task_id,
                                [str(_da_exc)],
                                stage=STAGE_TIMING,
                                error_code="PIPELINE_DEAD_AIR",
                                exc=_da_exc,
                            )
                        logger.warning(
                            "Task %s: dead_air audit soft-fail: %s", task_id, _da_exc
                        )
                else:
                    # Never ship a silent 1s stub as a "successful" dub track.
                    return _fail(
                        task_id,
                        [
                            "TTS handoff empty: timed dub track could not be built "
                            "(no placeable segment audio)"
                        ],
                        stage=STAGE_TIMING,
                        error_code="TTS_HANDOFF_EMPTY",
                    )

            profiler.stop("timing")
            if pipeline_timer is not None:
                pipeline_timer.stop("timing")
            _runtime_stage_record(task_id, runtime_diag, 5, STAGE_TIMING)

            try:
                _publish_studio_session_keep_running(task_id, "timing")
            except Exception as studio_refresh_err:
                logger.debug(
                    "Task %s: studio refresh post-timing skipped: %s",
                    task_id,
                    studio_refresh_err,
                )

            segment_texts = [
                str(s.get("text") or "")
                for s in current_segments_snapshot
                if s.get("merged_into") is None
            ]
            dev_diag.log_timing_map(
                timing_map=current_timing_map_snapshot,
                segments=segment_texts,
                video_duration_ms=target_duration,
                timing_fit_warnings=timing_warnings,
            )
            with STATE_LOCK:
                task["info"]["timing_warnings"] = list(timing_warnings)
                task["info"]["overlap_quality"] = overlap_report
                task["info"]["dev_diagnostics"] = dev_diag.paths()

            try:
                from engines.core.feature_flags import is_enabled as _ff_enabled
                from engines.ai_director import (
                    format_report,
                    is_ai_director_enabled,
                    validate_pipeline,
                )
                from engines.core.events import get_event_bus

                if _ff_enabled("ai_director", developer_session=True) or is_ai_director_enabled():
                    src_segs = list(source_segments_snapshot)
                    tgt_segs = [
                        str(s.get("text") or "")
                        for s in current_segments_snapshot
                        if s.get("merged_into") is None
                    ]
                    director_report = validate_pipeline(
                        source_segments=src_segs,
                        translated_segments=tgt_segs,
                        timing_map=current_timing_map_snapshot,
                        word_maps=task["info"].get("source_word_maps"),
                        timing_warnings=timing_warnings,
                    )
                    with STATE_LOCK:
                        task["info"]["ai_director_report"] = director_report.to_dict()
                        task["info"]["ai_director_text"] = format_report(director_report)
                        if director_report.block_export:
                            task["info"]["ai_director_block_export"] = True
                    get_event_bus().emit(
                        "ai_director",
                        {"score": director_report.score, "task_id": task_id},
                    )
                    try:
                        from engines.project_format import autosave_from_task_info

                        autosave_from_task_info(
                            APP_DIR,
                            task["info"],
                            title=f"director-{task_id}",
                        )
                    except Exception:
                        pass
            except Exception:
                pass

        with STATE_LOCK:
            segments_data = task["info"].get("segments_data") or []

        from engines.ocr_engine import align_ocr_to_speech_slots

        ocr_by_slot = (
            align_ocr_to_speech_slots(
                ocr_result.get("segments") or [],
                current_timing_map_snapshot,
            )
            if ocr_enabled
            else [""] * len(current_timing_map_snapshot)
        )
        audit_rows = []
        for idx, seg in enumerate(segments_data):
            if seg.get("merged_into") is not None:
                continue
            speech = (
                source_segments_snapshot[idx]
                if idx < len(source_segments_snapshot)
                else ""
            )
            audit_rows.append(
                {
                    "segment_id": idx,
                    "speech_text": speech,
                    "ocr_text": ocr_by_slot[idx] if idx < len(ocr_by_slot) else "",
                    "translated_text": seg.get("text") or (segments[idx] if idx < len(segments) else ""),
                    "tts_text": seg.get("tts_text") or seg.get("text") or "",
                }
            )
        dev_diag.log_pipeline_audit(
            audit_rows,
            extra={
                "task_id": task_id,
                "segmentation_mode": segmentation_mode,
                "ocr_enabled": ocr_enabled,
                "translate_method": translate_method,
                "dub_style": dub_style,
                "skip_tts": skip_tts,
            },
        )
        with STATE_LOCK:
            task["info"]["dev_diagnostics"] = dev_diag.paths()

        # Stage 12: TTS lang/voice integrity before mux (Simple).
        try:
            from engines.tts_lang_lock import (
                assert_voice_matches_target,
                pre_mux_tts_integrity,
            )

            with STATE_LOCK:
                _mux_info = dict(task.get("info") or {})
                _mux_sd = list(_mux_info.get("segments_data") or segments_data)
                _mux_voice = str(
                    _mux_info.get("pipeline_voice")
                    or _mux_info.get("tts_voice")
                    or voice
                    or ""
                )
                _mux_tgt = str(_mux_info.get("target_lang") or target_lang or "uk")
                _mux_timeline = float(
                    _mux_info.get("target_duration_ms")
                    or _mux_info.get("duration_ms")
                    or 0
                )
            if _mux_info.get("simple_pipeline") or _mux_info.get("happy_path") or _mux_info.get(
                "simple_voice_locked"
            ):
                _ok_v, _why_v = assert_voice_matches_target(
                    _mux_voice, _mux_tgt, raise_error=False
                )
                if not _ok_v:
                    from engines.simple_voice_lock import (
                        DEFAULT_UK_VOICE,
                        lock_simple_pipeline_voice,
                    )

                    logger.warning(
                        "Task %s: Simple voice locale %s — reroute %s",
                        task_id,
                        _why_v,
                        DEFAULT_UK_VOICE,
                    )
                    lock_simple_pipeline_voice(
                        _mux_sd,
                        pipeline_voice=DEFAULT_UK_VOICE,
                        task_info=_mux_info,
                    )
                    _mux_voice = DEFAULT_UK_VOICE
                if int(_mux_info.get("unique_voices_used") or 1) != 1:
                    from engines.simple_voice_lock import lock_simple_pipeline_voice

                    lock_simple_pipeline_voice(
                        _mux_sd,
                        pipeline_voice=_mux_voice,
                        task_info=_mux_info,
                    )
                _integ = pre_mux_tts_integrity(
                    _mux_sd,
                    target_lang=_mux_tgt,
                    timeline_ms=_mux_timeline or None,
                    simple_mode=True,
                )
                with STATE_LOCK:
                    task["info"]["tts_pre_mux_integrity"] = {
                        k: v for k, v in _integ.items() if k != "rows"
                    }
                    task["info"]["tts_pre_mux_rows"] = _integ.get("rows") or []
                    if _integ.get("rerouted_default_uk"):
                        task["info"]["tts_integrity_rerouted_uk"] = True
        except RuntimeError as _integ_rt:
            _msg = str(_integ_rt)
            _simple_here = False
            try:
                from engines.simple_dub_pipeline import is_simple_pipeline as _isp2

                _simple_here = bool(_isp2(task.get("info") or {}))
            except Exception:
                _simple_here = bool(
                    (task.get("info") or {}).get("simple_pipeline")
                    or (task.get("info") or {}).get("happy_path")
                )
            if _simple_here and (
                "PIPELINE_LANG_MIX" in _msg or "PIPELINE_VOICE_LOCALE" in _msg
            ):
                logger.warning(
                    "Task %s: Simple TTS integrity %s — pad/reroute, mux continues",
                    task_id,
                    _integ_rt,
                )
            else:
                raise
        except Exception as _integ_exc:
            logger.warning("Task %s: pre-mux TTS integrity soft-fail: %s", task_id, _integ_exc)

        # ══ ШАГ 6: Dub Engine ════════════════════════════════════════════
        if not _ensure_control(task_id, ui_lang):
            return
        _set_step(task_id, "dub", 91.0)
        profiler.start("mux")
        if pipeline_timer is not None:
            pipeline_timer.start("mux")

        try:
            from engines.ai_core import current_ai_core

            _core_now = current_ai_core()
            _mix_plan = _core_now.decide_mix_plan() if _core_now is not None else {}
            if _core_now is not None:
                with STATE_LOCK:
                    task["info"]["ai_mix_plan"] = _mix_plan
                    task["info"]["ai_core"] = _core_now.to_dict()
        except Exception:
            logger.debug("[AICore] mix plan unavailable", exc_info=True)

        video_stem = Path(video_path).stem
        output_name = f"{video_stem}_OUTPUT_{base_id}.mp4"
        output_path = str(OUTPUT_DIR / output_name)

        with STATE_LOCK:
            target_duration = task["info"].get("target_duration_ms")

        dub_timeout_sec = max(600, int((target_duration or 0) / 1000) + 300)
        bg_path: str | None = None

        if skip_tts:
            from engines.dub_engine import mux_keep_original_audio

            logger.info(
                "Task %s: subtitles_only mux -> %s", task_id, output_path
            )
            ok, out_path, dub_errors = mux_keep_original_audio(
                video_path, output_path, timeout_sec=dub_timeout_sec
            )
            raw_result = (ok, out_path, dub_errors)
        else:
            if not timed_audio_path or not Path(timed_audio_path).exists():
                return _fail(
                    task_id,
                    [lp["timed_missing"]],
                    stage=STAGE_TIMING,
                    error_code="TIMED_AUDIO_MISSING",
                )

            from engines.dub_engine import DubEngine
            from engines.source_separation import (
                build_final_mix_diagnostics,
                get_background_mix_params,
            )

            logger.info("Task %s: starting DubEngine -> %s", task_id, output_path)

            with STATE_LOCK:
                _info_snapshot = task.get("info") or {}
                _vstretch_segs = _info_snapshot.get("video_stretch_segments") or []
                _sep_info = dict(_info_snapshot.get("source_separation") or {})
                _content_mode = _info_snapshot.get("content_mode") or ""
            bg_path, bg_atten_db, sep_ok = get_background_mix_params(
                {"source_separation": _sep_info}
            )
            # Isolated original-voice (vocals) stem — lets us underlay + duck the
            # original human voice independently of music/SFX (TZ Task 4/7/8).
            dialogue_path = ""
            if sep_ok and _sep_info.get("success"):
                _dlg = _sep_info.get("dialogue_path")
                if _dlg and Path(str(_dlg)).is_file():
                    dialogue_path = str(_dlg)
            if sep_ok and bg_path:
                logger.info(
                    "Task %s: mixing dubbed speech with accompaniment stem %s (voice=%s)",
                    task_id,
                    bg_path,
                    dialogue_path or "n/a",
                )

            # Resolve the professional mix policy (levels + intelligent ducking)
            # from the content mode + the user's requested volumes. Single source
            # of truth in engines/audio_mix_config.py — no duplicated mixing logic.
            _mix_config = None
            try:
                from engines.audio_mix_config import resolve_mix_config
                from engines.dubbing_engine.content_mode import get_profile

                _prof = get_profile(_content_mode) if _content_mode else None
                _mix_config = resolve_mix_config(
                    original_volume=(mix_volumes or {}).get("original_volume"),
                    dub_volume=(mix_volumes or {}).get("dub_volume"),
                    background_volume=(mix_volumes or {}).get("background_volume"),
                    content_mode_profile=_prof,
                    request=(mix_volumes or {}),
                )
            except Exception as _mc_exc:  # noqa: BLE001 — mixer policy must not break mux
                logger.debug("Task %s: mix config resolve failed: %s", task_id, _mc_exc)
                _mix_config = None

            raw_result = DubEngine(
                video_path=video_path,
                timed_audio=timed_audio_path,
                video_stretch_segments=_vstretch_segs,
                background_audio_path=bg_path or "",
                background_attenuation_db=bg_atten_db,
                dialogue_audio_path=dialogue_path,
                mix_config=_mix_config,
            ).run(
                output_path=output_path,
                mode=dub_mode,
                mix_mode=mix_mode,
                mix_volume=mix_volume,
                original_volume=(mix_volumes or {}).get("original_volume"),
                dub_volume=(mix_volumes or {}).get("dub_volume"),
                background_volume=(mix_volumes or {}).get("background_volume"),
                progress_callback=lambda pct: _set_step(
                    task_id, "dub", 91.0 + _normalize_progress(pct) * 0.08
                ),
                timeout_sec=dub_timeout_sec,
            )

        if isinstance(raw_result, tuple) and len(raw_result) >= 3:
            ok, out_path, dub_errors = raw_result[0], raw_result[1], raw_result[2]
        elif isinstance(raw_result, tuple) and len(raw_result) == 2:
            ok, out_path, dub_errors = raw_result[0], raw_result[1], []
        else:
            return _fail(
                task_id,
                [lp["contract_broken"]],
                stage=STAGE_FFMPEG,
                error_code="INTEGRITY_CONTRACT_BROKEN",
            )

        profiler.stop("mux")
        if pipeline_timer is not None:
            pipeline_timer.stop("mux")
            pipeline_timer.start("export")
            pipeline_timer.stop("export")
        _runtime_stage_record(task_id, runtime_diag, 6, STAGE_FFMPEG)

        if not ok:
            err_list = (
                dub_errors
                if isinstance(dub_errors, list) and dub_errors
                else [str(dub_errors) if dub_errors else lp.get("dub_errors", "DubEngine failed")]
            )
            return _fail(
                task_id,
                err_list,
                stage=STAGE_FFMPEG,
                error_code="FFMPEG_RENDER_FAILED",
            )

        final_output = out_path or output_path
        if not final_output or not Path(final_output).exists():
            return _fail(
                task_id,
                [lp["dub_missing"]],
                stage=STAGE_FFMPEG,
                error_code="OUTPUT_MISSING",
            )

        try:
            from engines.source_separation import build_final_mix_diagnostics

            with STATE_LOCK:
                _mix_sep = dict((task.get("info") or {}).get("source_separation") or {})
            mix_diag = build_final_mix_diagnostics(
                separation_info=_mix_sep,
                final_mp4_path=str(final_output),
                mix_success=True,
                used_stem_mix=bool(bg_path) if not skip_tts else False,
            )
            with STATE_LOCK:
                task["info"]["final_mix"] = mix_diag.to_dict()
        except Exception as mix_diag_err:
            logger.debug("Task %s: final mix diagnostics skipped: %s", task_id, mix_diag_err)

        from engines.video_integrity import verify_video_integrity

        integrity = verify_video_integrity(video_path, final_output)
        dev_diag.log_video_integrity(integrity)
        if not integrity.get("ok"):
            logger.warning(
                "Task %s: video integrity warnings: %s",
                task_id,
                integrity.get("warnings") or integrity.get("errors"),
            )

        with STATE_LOCK:
            task["info"]["video_integrity"] = integrity
            task["info"]["dev_diagnostics"] = dev_diag.paths()

        with STATE_LOCK:
            is_editing = control.get("editing", False)

        # Stamp tts_pipeline audio presence AFTER repair/mux and BEFORE cleanup
        # (disk truth — never count files that cleanup may later remove).
        with STATE_LOCK:
            _pre_clean_info = task.get("info") or {}
            try:
                from engines.segment_timing_qa import _build_openddf_tts_pipeline_block

                _pre_clean_info["tts_pipeline"] = _build_openddf_tts_pipeline_block(
                    _pre_clean_info
                )
                _sync_pad_census_fields(
                    _pre_clean_info, _pre_clean_info["tts_pipeline"]
                )
            except Exception:
                pass

        if not is_editing:
            keep_assets = bool(task.get("info", {}).get("keep_studio_assets"))
            # Never delete session segment audio (slot_fit_/pause_run_/tts_*.wav|mp3).
            # Output-dir loose TTS copies may still be trimmed when keep_assets=False.
            if not keep_assets:
                for f in set(tts_files):
                    p = OUTPUT_DIR / f
                    name = p.name.lower()
                    if name.startswith(("slot_fit_", "pause_run_", "tts_")):
                        continue
                    if p.suffix.lower() in (".wav", ".mp3", ".ogg", ".flac"):
                        # Prefer keeping if also referenced under session_dir
                        continue
                    p.unlink(missing_ok=True)
            if not keep_original_track:
                Path(audio_path).unlink(missing_ok=True)
            if timed_audio_path and Path(timed_audio_path).exists() and not keep_assets:
                Path(timed_audio_path).unlink(missing_ok=True)

            if not keep_assets:
                from engines.cleanup_engine import cleanup_project_success

                with STATE_LOCK:
                    _cleanup_info = dict(task.get("info") or {})
                session_dir = _cleanup_info.get("session_dir")
                _cleanup_dict = cleanup_project_success(
                    APP_DIR,
                    session_dir=Path(session_dir) if session_dir else None,
                    keep_names={Path(final_output).name},
                    info=_cleanup_info,
                )
                with STATE_LOCK:
                    task["info"]["storage_cleanup"] = _cleanup_dict
                    task["info"]["storage_report"] = _cleanup_dict
                    task["info"]["cleanup_engine"] = _cleanup_dict

        with STATE_LOCK:
            task.update(
                {
                    "status": "done",
                    "step": "dub",
                    "steps_done": len(PIPELINE_STEPS),
                    "progress": 100.0,
                    "output_file": Path(final_output).name,
                }
            )
            from engines.translation_review import build_translation_review

            info = task.get("info") or {}
            info["target_lang"] = target_lang
            info["mux_base_id"] = base_id
            info["output_path_full"] = str(final_output)
            from engines.segment_timing_qa import build_final_dub_qa_report

            dub_qa = build_final_dub_qa_report(info)
            info["final_dub_qa"] = dub_qa
            try:
                from engines.segment_timing_qa import _build_openddf_tts_pipeline_block

                # Stage 32: pad true holes on the archive list, then census with
                # first-existing paths (ghost g0000.mp3 must not hide pause_run).
                _segs_now = list(info.get("segments_data") or [])
                try:
                    _last_resort_pad_missing_segments(
                        _segs_now,
                        task_info=info,
                        task_id=task_id,
                    )
                    info["segments_data"] = _segs_now
                except Exception:
                    pass
                # Keep pre-cleanup census when post-cleanup would falsely show missing.
                _tp_now = _build_openddf_tts_pipeline_block(
                    info, segments_data=_segs_now or None
                )
                _tp_prev = info.get("tts_pipeline") or {}
                if int(_tp_prev.get("audio_present") or 0) >= int(
                    _tp_now.get("audio_present") or 0
                ) and int(_tp_prev.get("expected_segments") or 0) > 0:
                    info["tts_pipeline"] = _tp_prev
                else:
                    info["tts_pipeline"] = _tp_now
                _sync_pad_census_fields(info, info["tts_pipeline"])
            except Exception:
                pass
            try:
                from engines.dub_quality_stabilization import write_dub_quality_report_json

                write_dub_quality_report_json(
                    APP_DIR,
                    info,
                    task_id=task_id,
                    project_uuid=str(info.get("project_uuid") or ""),
                )
            except Exception as dq_err:
                logger.debug("Task %s: dub_quality_report skipped: %s", task_id, dq_err)
            from engines.segment_timing_qa import build_openddf_full_report

            info["task_id"] = task_id
            info["openddf_full_report"] = build_openddf_full_report(info)
            try:
                from engines.llm_adaptation_report import build_llm_adaptation_report

                info["llm_adaptation_report"] = build_llm_adaptation_report(info)
            except Exception as _llm_rep_exc:
                logger.debug("Task %s: llm_adaptation_report skipped: %s", task_id, _llm_rep_exc)
            task["info"]["translation_review"] = build_translation_review(info)
            _persist_task_review(task)
            try:
                from engines.pipeline_integrity.passive_openddf import ensure_session

                openddf = ensure_session(task_id, task_info=info)
                if openddf:
                    qa_artifacts = openddf.persist_project_qa_bundle(info, qa_report=dub_qa)
                    info["final_dub_qa_artifacts"] = qa_artifacts
                    if not dub_qa.get("ok"):
                        logger.warning(
                            "Task %s: final dub QA found %d issues — OpenDDF report written",
                            task_id,
                            dub_qa.get("issue_count", 0),
                        )
            except Exception as qa_err:
                logger.debug("final dub QA OpenDDF bundle skipped: %s", qa_err)

        try:
            from engines.tts_text_path import write_tts_path_work_report

            info_rep = AUTO_TASKS.get(task_id, {}).get("info") or {}
            tts_inputs = [
                str(s.get("text") or "")
                for s in (info_rep.get("segments_data") or [])
                if s.get("merged_into") is None
            ]
            write_tts_path_work_report(
                APP_DIR,
                task_id=task_id,
                info=info_rep,
                tts_inputs=tts_inputs,
                adapt_tts_text=bool(info_rep.get("tts_semantic_adapt", False)),
                translate_method=str(info_rep.get("translate_method") or ""),
                success=True,
            )
            from engines.word_timing_map.pipeline import (
                save_word_timing_dev_report,
                word_maps_from_task_info,
            )

            wtm_maps = word_maps_from_task_info(info_rep)
            if wtm_maps:
                save_word_timing_dev_report(
                    APP_DIR,
                    wtm_maps,
                    task_id=task_id,
                    extra=info_rep.get("word_timing_meta"),
                )
            _wtm_record_checkpoint(wtm_cp_log, task_id, "final")
            with STATE_LOCK:
                task = AUTO_TASKS.get(task_id)
                if task:
                    task["info"]["word_timing_checkpoints"] = wtm_cp_log.to_dict()
                    phase0_path = wtm_cp_log.flush()
                    task["info"]["word_timing_phase0_report"] = phase0_path
                    dev_diag.log_word_timing(task["info"])
                    task["info"]["dev_diagnostics"] = dev_diag.paths()
        except Exception as rep_err:
            logger.debug("TTS path work report skipped: %s", rep_err)

        try:
            from engines.translation_quality_log import (
                SegmentTranslationAudit,
                TranslationQualityLog,
            )

            info = AUTO_TASKS.get(task_id, {}).get("info", {})
            raw_audits = info.get("translation_audits") or []
            seg_data = info.get("segments_data") or []
            if raw_audits and seg_data:
                tql = TranslationQualityLog(APP_DIR)
                for row in raw_audits:
                    rec = SegmentTranslationAudit(**row)
                    if rec.index < len(seg_data):
                        tts = str(seg_data[rec.index].get("tts_text") or "").strip()
                        rec.tts_text = tts or rec.tts_text
                    tql.add(rec)
                tql.flush(task_id=task_id, extra={"phase": "tts_final"})
        except Exception as tql_err:
            logger.debug("translation_quality tts_final skipped: %s", tql_err)

        try:
            from engines.dub_quality_report import write_post_dub_work_report

            write_post_dub_work_report(
                APP_DIR,
                task_id=task_id,
                info=AUTO_TASKS.get(task_id, {}).get("info") or {},
                success=True,
            )
        except Exception as rep_err:
            logger.debug("Post-dub work report skipped: %s", rep_err)

    except Exception as e:
        logger.exception("Pipeline failed task=%s", task_id)
        from engines.dubbing_engine.pipeline_failure_diag import (
            STEP_TO_STAGE,
            STAGE_AUDIO_EXTRACTION,
            STAGE_TIMING,
            STAGE_TTS,
            fail_pipeline,
        )
        from engines.pipeline_integrity.exceptions import (
            PipelineIntegrityError,
            StageSnapshotIntegrityError,
        )

        if isinstance(e, StageSnapshotIntegrityError):
            fail_pipeline(
                task_id,
                e.format_user_reason(),
                stage=e.stage or STAGE_TTS,
                exc=e,
                error_code="STAGE_SNAPSHOT_INTEGRITY",
            )
        elif isinstance(e, PipelineIntegrityError):
            fail_pipeline(
                task_id,
                str(e),
                stage=e.stage or STAGE_TIMING,
                exc=e,
                error_code=getattr(e, "code", "PIPELINE_INTEGRITY").upper(),
            )
        else:
            with STATE_LOCK:
                _crash_task = AUTO_TASKS.get(task_id)
                _crash_step = _crash_task.get("step") if _crash_task else None
            _fail(
                task_id,
                [
                    f"Критическая ошибка пайплайна [{type(e).__name__}]: {str(e)}"
                ],
                stage=STEP_TO_STAGE.get(_crash_step or "", STAGE_AUDIO_EXTRACTION),
                exc=e,
                error_code="PIPELINE_CRITICAL",
            )
    finally:
        try:
            from engines.pipeline_progress_tracker import save_performance_diagnostics

            save_performance_diagnostics(task_id, app_dir=APP_DIR)
        except Exception:
            pass
        try:
            from engines.pipeline_watchdog import stop_pipeline_watchdog

            stop_pipeline_watchdog(task_id)
        except Exception:
            pass
        # TZ §29: TEMP deleted in finally on failure (FINAL never auto-deleted).
        try:
            with STATE_LOCK:
                _fin_task = AUTO_TASKS.get(task_id) or {}
                _fin_status = str(_fin_task.get("status") or "")
                _fin_info = dict(_fin_task.get("info") or {})
            if _fin_status in ("error", "failed"):
                from engines.pipeline_integrity.cleanup_manager import run_unified_cleanup

                _fail_report = run_unified_cleanup(
                    _fin_info,
                    session_dir=_fin_info.get("session_dir"),
                    success=False,
                    keep_studio=bool(_fin_info.get("keep_studio_assets")),
                    actor="pipeline_finally_failure",
                )
                with STATE_LOCK:
                    if task_id in AUTO_TASKS:
                        AUTO_TASKS[task_id].setdefault("info", {}).update(
                            {
                                "cleanup_deleted_files": _fail_report.get("removed") or [],
                                "cleanup_preserved_files": _fail_report.get("preserved") or [],
                                "cleanup_manager_ran": True,
                            }
                        )
        except Exception:
            pass
        if profiler is not None or pipeline_timer is not None:
            try:
                with STATE_LOCK:
                    final_status = AUTO_TASKS.get(task_id, {}).get("status")
                    timing_br_raw = (
                        (AUTO_TASKS.get(task_id, {}).get("info") or {}).get(
                            "translation_timing_breakdown"
                        )
                        or {}
                    )
                if pipeline_timer is not None:
                    json_path, report_path = pipeline_timer.finalize(
                        video_path=video_path,
                        success=(final_status == "done"),
                    )
                    summary = pipeline_timer.summary_lines()
                    logger.info(
                        "Task %s pipeline timing:\n%s",
                        task_id,
                        "\n".join(summary),
                    )
                    try:
                        from engines.translation_timing import (
                            TranslationTimingBreakdown,
                            log_pipeline_timing_summary,
                        )

                        br_raw = timing_br_raw or (
                            (pipeline_timer.to_dict().get("meta") or {}).get(
                                "translation_breakdown"
                            )
                            or {}
                        )
                        buckets = br_raw.get("ui_buckets") or {}
                        breakdown = TranslationTimingBreakdown(
                            marian_sec=float(buckets.get("marian_mt") or 0),
                            llm_adaptation_sec=float(buckets.get("llm_adaptation") or 0),
                            validation_sec=float(br_raw.get("validation_sec") or 0),
                        )
                        stages = pipeline_timer.to_dict().get("stages") or {}
                        log_pipeline_timing_summary(
                            APP_DIR,
                            task_id,
                            whisper_sec=float(stages.get("whisper") or 0),
                            breakdown=breakdown,
                            tts_sec=float(stages.get("tts") or 0),
                        )
                    except Exception as timing_log_err:
                        logger.debug(
                            "Translation timing log skipped task=%s: %s",
                            task_id,
                            timing_log_err,
                        )
                elif profiler is not None:
                    report_path = profiler.finalize(
                        video_path=video_path,
                        success=(final_status == "done"),
                    )
                    json_path = None
                with STATE_LOCK:
                    if task_id in AUTO_TASKS:
                        info = AUTO_TASKS[task_id].setdefault("info", {})
                        if pipeline_timer is not None:
                            info["pipeline_timing_json"] = json_path
                            info["pipeline_timing"] = pipeline_timer.to_dict()
                        info["performance_report"] = report_path
                try:
                    from engines.pipeline_performance_artifacts import write_performance_artifacts

                    with STATE_LOCK:
                        task_snapshot = dict(AUTO_TASKS.get(task_id) or {})
                        info_snapshot = dict(task_snapshot.get("info") or {})
                    timer_dict = (
                        pipeline_timer.to_dict()
                        if pipeline_timer is not None
                        else info_snapshot.get("pipeline_timing")
                    )
                    artifact_paths = write_performance_artifacts(
                        task_id,
                        app_dir=APP_DIR,
                        task_info=info_snapshot,
                        pipeline_timer_dict=timer_dict,
                        success=(final_status == "done"),
                        video_path=video_path,
                    )
                    with STATE_LOCK:
                        if task_id in AUTO_TASKS:
                            AUTO_TASKS[task_id].setdefault("info", {}).update(artifact_paths)
                    logger.info(
                        "Task %s: performance artifacts -> %s",
                        task_id,
                        artifact_paths.get("performance_report_json"),
                    )
                except Exception as artifact_err:
                    logger.warning(
                        "Performance artifacts failed task=%s: %s", task_id, artifact_err
                    )
                logger.info("Task %s: performance report -> %s", task_id, report_path)
            except Exception as perf_err:
                logger.warning("Performance report failed task=%s: %s", task_id, perf_err)

        with STATE_LOCK:
            current_control = AUTO_TASK_CONTROLS.get(task_id)
            current_status = AUTO_TASKS.get(task_id, {}).get("status")
            is_editing = (
                current_control.get("editing", False) if current_control else False
            )

            if (
                current_control
                and current_status in ("done", "error")
                and not is_editing
            ):
                AUTO_TASK_CONTROLS.pop(task_id, None)

        evict_expired_auto_tasks()


def _set_step(t_id, step, prog):
    try:
        from engines.pipeline_progress_tracker import record_stage_end, record_stage_start
        from engines.pipeline_watchdog import watchdog_stage_start

        with STATE_LOCK:
            prev_step = AUTO_TASKS.get(t_id, {}).get("step")
        if prev_step and prev_step != step:
            record_stage_end(t_id, prev_step)
        record_stage_start(t_id, step)
        watchdog_stage_start(t_id, step, progress_pct=float(prog or 0))
    except Exception:
        pass
    _STEP_AGENT = {
        "preparing": "extract",
        "extract_audio": "extract",
        "transcribe": "whisper",
        "translate": "translation",
        "tts": "tts",
        "timing": "timing",
        "dub": "mux",
        "studio": "mix",
        "done": "export",
    }
    with STATE_LOCK:
        if t_id in AUTO_TASKS:
            try:
                s_idx = PIPELINE_STEPS.index(step)
            except ValueError:
                s_idx = AUTO_TASKS[t_id].get("steps_done", 0)

            AUTO_TASKS[t_id].update(
                {"step": step, "progress": prog, "steps_done": s_idx}
            )
            info = AUTO_TASKS[t_id].setdefault("info", {})
            info["step_started_at"] = time.time()
            if step == "tts":
                info["tts_stage_started_at"] = time.time()
            if info.get("developer_preview_enabled"):
                from engines.developer_preview import record_agent_event

                agent = _STEP_AGENT.get(step)
                if agent:
                    prev = info.get("developer_timeline_active")
                    if prev and prev != agent:
                        record_agent_event(info, prev, "done")
                    record_agent_event(info, agent, "running")
            _update_progress_detail(t_id, phase=step)


def _fail(t_id, errs, *, stage=None, exc=None, error_code=None):
    """
    Fail-fast with structured pipeline diagnostics (Error Diagnostics v1.0).
    Editing mode: non-terminal pause unless exc is critical.
    """
    from engines.dubbing_engine.pipeline_failure_diag import (
        STEP_TO_STAGE,
        STAGE_AUDIO_EXTRACTION,
        fail_pipeline,
    )

    msg = errs[0] if errs else "Unknown error"
    with STATE_LOCK:
        task = AUTO_TASKS.get(t_id)
        step = task.get("step") if task else None
        editing = bool(
            AUTO_TASK_CONTROLS.get(t_id, {}).get("editing")
        )
    resolved_stage = stage or STEP_TO_STAGE.get(step or "", STAGE_AUDIO_EXTRACTION)
    fail_pipeline(
        t_id,
        msg,
        stage=resolved_stage,
        exc=exc,
        error_code=error_code,
        editing_pause=editing,
    )


# === КОНЕЦ ФАЙЛА ===
