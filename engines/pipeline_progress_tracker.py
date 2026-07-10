"""Pipeline progress transparency — segment metrics, ETA, live messages, performance diagnostics."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.pipeline_progress")

# Seconds before "longer than usual" notice (per stage).
SLOW_SEGMENT_FACTOR = 2.0
SLOW_SEGMENT_MIN_SEC: dict[str, float] = {
    "translate": 90.0,
    "tts": 45.0,
    "timing": 30.0,
    "dub": 60.0,
    "mix": 45.0,
}

_OPERATION_LABELS = {
    "ru": {
        "translate": "перевод",
        "translation": "перевод",
        "tts": "генерация речи",
        "timing": "синхронизация",
        "dub": "сборка MP4",
        "mix": "сведение",
        "studio": "сведение",
        "transcribe": "распознавание речи",
        "extract_audio": "извлечение аудио",
        "preparing": "подготовка",
        "quality_gate": "проверка качества",
        "ai_core": "AI Core",
        "slot_fit": "подгонка под тайминг",
        "voice_verification": "перепроверка озвучки",
        "adaptation": "адаптация текста",
    },
    "en": {
        "translate": "translation",
        "translation": "translation",
        "tts": "speech generation",
        "timing": "synchronization",
        "dub": "MP4 export",
        "mix": "mixing",
        "studio": "mixing",
        "transcribe": "speech recognition",
        "extract_audio": "audio extraction",
        "preparing": "preparation",
        "quality_gate": "quality check",
        "ai_core": "AI Core",
        "slot_fit": "slot fit",
        "voice_verification": "voice verification",
        "adaptation": "text adaptation",
    },
    "uk": {
        "translate": "переклад",
        "translation": "переклад",
        "tts": "генерація мовлення",
        "timing": "синхронізація",
        "dub": "збірка MP4",
        "mix": "зведення",
        "studio": "зведення",
        "transcribe": "розпізнавання мовлення",
        "extract_audio": "витяг аудіо",
        "preparing": "підготовка",
        "quality_gate": "перевірка якості",
        "ai_core": "AI Core",
        "slot_fit": "підгонка під таймінг",
        "voice_verification": "переперевірка озвучки",
        "adaptation": "адаптація тексту",
    },
}


def _format_duration(sec: float, lang: str = "ru") -> str:
    s = max(0, int(sec))
    m, r = divmod(s, 60)
    if lang == "en":
        return f"{m}m {r}s" if m else f"{r}s"
    return f"{m} мин {r} сек" if m else f"{r} сек"


def _get_state(task_id: str) -> dict[str, Any]:
    from engines.dub_task_state import AUTO_TASKS, STATE_LOCK

    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        if not task:
            return {}
        info = task.setdefault("info", {})
        return info.setdefault("pipeline_progress_state", {})


def _save_state(task_id: str, state: dict[str, Any]) -> None:
    from engines.dub_task_state import AUTO_TASKS, STATE_LOCK

    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        if task:
            task.setdefault("info", {})["pipeline_progress_state"] = state


def record_stage_start(task_id: str, stage: str) -> None:
    st = _get_state(task_id)
    now = time.time()
    st["current_stage"] = stage
    st["stage_started_at"] = now
    st.setdefault("stage_times", {})[stage] = {"started_at": now, "ended_at": None, "duration_sec": 0.0}
    _save_state(task_id, st)


def record_stage_end(task_id: str, stage: str) -> None:
    st = _get_state(task_id)
    now = time.time()
    entry = st.setdefault("stage_times", {}).setdefault(stage, {})
    started = float(entry.get("started_at") or st.get("stage_started_at") or now)
    entry["ended_at"] = now
    entry["duration_sec"] = round(now - started, 2)
    _save_state(task_id, st)


def record_segment_start(
    task_id: str,
    stage: str,
    segment_index: int,
    *,
    total_segments: int = 0,
    **meta: Any,
) -> None:
    st = _get_state(task_id)
    now = time.time()
    key = f"{stage}:{segment_index}"
    st["current_segment"] = segment_index
    st["current_stage"] = stage
    st["segment_started_at"] = now
    st.setdefault("segments", {})[key] = {
        "stage": stage,
        "index": segment_index,
        "started_at": now,
        "ended_at": None,
        "duration_sec": None,
        "meta": dict(meta),
    }
    if total_segments:
        st["total_segments"] = total_segments
    _save_state(task_id, st)


def record_segment_end(
    task_id: str,
    stage: str,
    segment_index: int,
    *,
    cause: str = "",
    error: str = "",
) -> None:
    st = _get_state(task_id)
    now = time.time()
    key = f"{stage}:{segment_index}"
    rec = st.setdefault("segments", {}).setdefault(key, {})
    started = float(rec.get("started_at") or st.get("segment_started_at") or now)
    duration = round(now - started, 2)
    rec.update(
        {
            "ended_at": now,
            "duration_sec": duration,
            "cause": cause,
            "error": error,
        }
    )
    hist = st.setdefault("segment_durations", {}).setdefault(stage, [])
    hist.append(duration)
    if len(hist) > 500:
        st["segment_durations"][stage] = hist[-500:]

    avg = sum(hist) / len(hist) if hist else duration
    threshold = max(
        SLOW_SEGMENT_MIN_SEC.get(stage, 30.0),
        avg * SLOW_SEGMENT_FACTOR,
    )
    if duration >= threshold:
        slow = st.setdefault("slow_segments", [])
        slow.append(
            {
                "stage": stage,
                "segment_index": segment_index,
                "duration_sec": duration,
                "avg_sec": round(avg, 2),
                "probable_cause": cause or _guess_slow_cause(stage, rec.get("meta") or {}),
                "meta": rec.get("meta") or {},
            }
        )
        if len(slow) > 100:
            st["slow_segments"] = slow[-100:]
    _save_state(task_id, st)


def _guess_slow_cause(stage: str, meta: dict[str, Any]) -> str:
    chars = int(meta.get("char_count") or meta.get("text_chars") or 0)
    if stage in ("translate", "translation"):
        if chars > 400:
            return "long_text"
        return "slow_llm_response"
    if stage == "tts":
        if chars > 300:
            return "long_text_tts"
        if meta.get("retry"):
            return "tts_retry"
        return "tts_engine_slow"
    if stage == "timing":
        return "timing_adaptation_retries"
    if stage == "quality_gate":
        return "quality_checks"
    return "unknown"


def _avg_segment_sec(st: dict[str, Any], stage: str) -> float | None:
    hist = st.get("segment_durations", {}).get(stage) or []
    if not hist:
        return None
    return sum(hist) / len(hist)


def enrich_progress_fields(task_id: str, **fields: Any) -> dict[str, Any]:
    """Compute derived progress metrics and live_message for UI."""
    from engines.dub_task_state import AUTO_TASKS, STATE_LOCK

    st = _get_state(task_id)
    now = time.time()
    phase = str(fields.get("phase") or st.get("current_stage") or "")
    if phase and fields.get("phase"):
        if phase != st.get("current_stage"):
            record_stage_start(task_id, phase)

    ui_lang = "ru"
    with STATE_LOCK:
        task = AUTO_TASKS.get(task_id)
        if task:
            ui_lang = str(task.get("ui_lang") or (task.get("info") or {}).get("ui_lang") or "ru")

    cur = int(fields.get("current_segment") or st.get("current_segment") or 0)
    total = int(fields.get("total_segments") or st.get("total_segments") or 0)
    done = fields.get("segments_done")
    if done is None:
        done = max(0, cur - 1) if cur else 0
    else:
        done = int(done)

    remaining = max(0, total - done) if total else 0
    stage_started = float(st.get("stage_started_at") or fields.get("stage_started_at") or 0)
    seg_started = float(st.get("segment_started_at") or fields.get("segment_started_at") or 0)

    if cur and (fields.get("current_segment") or fields.get("segments_done") is not None):
        if not seg_started or int(fields.get("current_segment") or 0) != int(st.get("current_segment") or 0):
            record_segment_start(
                task_id,
                phase or "segment",
                cur,
                total_segments=total,
                **{k: v for k, v in fields.items() if k.startswith(("tts_", "voice", "llm_", "char"))},
            )
            seg_started = time.time()

    stage_elapsed = round(now - stage_started, 1) if stage_started else 0.0
    segment_elapsed = round(now - seg_started, 1) if seg_started else 0.0
    avg_seg = _avg_segment_sec(st, phase)
    eta_sec = fields.get("eta_sec")
    if eta_sec is None and avg_seg and remaining:
        eta_sec = int(avg_seg * remaining)
    elif eta_sec is None and stage_elapsed and done and total and done < total:
        rate = done / max(stage_elapsed, 1)
        eta_sec = int(remaining / max(rate, 0.01))

    stage_pct = None
    if total and done is not None:
        stage_pct = round(min(100.0, (done / max(total, 1)) * 100.0), 1)

    ops = _OPERATION_LABELS.get(ui_lang, _OPERATION_LABELS["ru"])
    operation = str(fields.get("operation") or ops.get(phase, phase or "обработка"))

    live_message = _build_live_message(
        ui_lang,
        phase=phase,
        operation=operation,
        cur=cur,
        total=total,
        done=done,
        remaining=remaining,
        stage_elapsed=stage_elapsed,
        segment_elapsed=segment_elapsed,
        avg_seg=avg_seg,
        eta_sec=int(eta_sec) if eta_sec else None,
        stage_pct=stage_pct,
        fields=fields,
    )

    slow_notice = _slow_segment_notice(
        ui_lang,
        cur=cur,
        segment_elapsed=segment_elapsed,
        avg_seg=avg_seg,
        operation=operation,
        phase=phase,
    )

    enriched = {
        **fields,
        "current_segment": cur or fields.get("current_segment"),
        "total_segments": total or fields.get("total_segments"),
        "segments_done": done,
        "segments_remaining": remaining,
        "stage_started_at": stage_started or now,
        "segment_started_at": seg_started or None,
        "stage_elapsed_sec": stage_elapsed,
        "segment_elapsed_sec": segment_elapsed,
        "avg_segment_sec": round(avg_seg, 2) if avg_seg else None,
        "eta_sec": int(eta_sec) if eta_sec else fields.get("eta_sec"),
        "stage_progress_pct": stage_pct,
        "operation": operation,
        "live_message": slow_notice or live_message,
        "slow_segment_notice": slow_notice,
        "last_heartbeat_at": now,
    }
    if phase == "translate" and not enriched.get("translation_timing"):
        with STATE_LOCK:
            task = AUTO_TASKS.get(task_id)
            if task:
                info = task.get("info") or {}
                pd = info.get("progress_detail") or {}
                tt = pd.get("translation_timing") or info.get("translation_timing")
                if tt:
                    enriched["translation_timing"] = tt
                sub = pd.get("translation_subphase") or fields.get("translation_subphase")
                if sub:
                    enriched["translation_subphase"] = sub
    return enriched


def _slow_segment_notice(
    lang: str,
    *,
    cur: int,
    segment_elapsed: float,
    avg_seg: float | None,
    operation: str,
    phase: str,
) -> str | None:
    if cur <= 0 or segment_elapsed <= 0:
        return None
    threshold = max(
        SLOW_SEGMENT_MIN_SEC.get(phase, 30.0),
        (avg_seg or 0) * SLOW_SEGMENT_FACTOR,
    )
    if segment_elapsed < threshold:
        return None
    dur = _format_duration(segment_elapsed, lang)
    if lang == "en":
        return (
            f"Segment #{cur} is taking longer than usual ({dur}). "
            f"{operation.capitalize()} in progress. Process continues."
        )
    if lang == "uk":
        return (
            f"Сегмент №{cur} обробляється довше за звичай ({dur}). "
            f"Триває {operation}. Процес продовжується."
        )
    return (
        f"Сегмент №{cur} обрабатывается дольше обычного ({dur}). "
        f"Идёт {operation}. Процесс продолжается."
    )


def _build_live_message(
    lang: str,
    *,
    phase: str,
    operation: str,
    cur: int,
    total: int,
    done: int,
    remaining: int,
    stage_elapsed: float,
    segment_elapsed: float,
    avg_seg: float | None,
    eta_sec: int | None,
    stage_pct: float | None,
    fields: dict[str, Any],
) -> str:
    parts: list[str] = []

    if (
        phase == "voice_verification"
        or fields.get("tts_substep") == "voice_verify"
    ):
        if lang == "en":
            parts.append("Voice verification")
        elif lang == "uk":
            parts.append("Переперевірка озвучки")
        else:
            parts.append("Перепроверка озвучки")
        if cur and total:
            parts.append(f"#{cur}/{total}")
        attempt = fields.get("verification_attempt")
        if attempt:
            retry_lbl = "retry" if lang == "en" else "спроба" if lang == "uk" else "попытка"
            parts.append(f"{retry_lbl} {attempt}")
        route = fields.get("verification_route")
        if route and route not in ("voice", "voice_resynth"):
            parts.append(str(route))
    elif phase == "tts" or fields.get("tts_engine") or fields.get("voice"):
        voice = fields.get("voice") or fields.get("tts_voice") or ""
        engine = fields.get("tts_engine") or fields.get("tts_engine_id") or ""
        llm = fields.get("llm_model") or fields.get("translation_model") or ""
        chars = fields.get("char_count") or fields.get("text_chars")
        slot_ms = fields.get("segment_duration_ms") or fields.get("slot_ms")
        if engine:
            parts.append(f"TTS: {engine}")
        if voice:
            parts.append(str(voice).split("-")[-1].replace("Neural", "") if "Neural" in str(voice) else str(voice))
        if llm:
            parts.append(f"LLM: {llm}")
        if cur and total:
            parts.append(f"#{cur}/{total}")
        if chars:
            parts.append(f"{chars} {('симв.' if lang != 'en' else 'chars')}")
        if slot_ms:
            parts.append(f"{int(slot_ms) // 1000}s slot")
    elif phase in ("translate", "translation"):
        model_disp = fields.get("llm_model_display") or fields.get("llm_model") or ""
        provider = fields.get("llm_provider_label") or fields.get("llm_provider") or ""
        if model_disp:
            parts.append(str(model_disp))
        elif provider:
            parts.append(str(provider))
        if lang == "en":
            parts.append("Translation in progress")
        elif lang == "uk":
            parts.append("Триває переклад")
        else:
            parts.append("Идёт перевод")
        if cur and total:
            parts.append(f"#{cur}/{total}")
        if done and total:
            if remaining:
                left = "залишилось" if lang == "uk" else "осталось" if lang == "ru" else "left"
                parts.append(f"{left}: {remaining}")
    else:
        if operation:
            parts.append(operation.capitalize() if lang == "en" else operation)

    if cur and total and phase not in ("tts",):
        parts.append(f"{'Сегмент' if lang != 'en' else 'Segment'} {cur}/{total}")

    if stage_pct is not None and phase:
        parts.append(f"{stage_pct}%")

    if segment_elapsed > 3 and cur:
        parts.append(_format_duration(segment_elapsed, lang))

    if avg_seg and lang == "en":
        parts.append(f"avg {_format_duration(avg_seg, lang)}")
    elif avg_seg:
        parts.append(f"ср. {_format_duration(avg_seg, lang)}")

    if eta_sec and eta_sec > 5:
        if lang == "en":
            parts.append(f"ETA ~{_format_duration(eta_sec, lang)}")
        else:
            parts.append(f"~{_format_duration(eta_sec, lang)}")

    if stage_elapsed > 10 and not segment_elapsed:
        if lang == "en":
            parts.append(f"stage {_format_duration(stage_elapsed, lang)}")
        else:
            parts.append(f"этап {_format_duration(stage_elapsed, lang)}")

    return " · ".join(p for p in parts if p)


def save_performance_diagnostics(task_id: str, app_dir: Path | None = None) -> Path | None:
    """Persist segment/stage performance summary for developers."""
    root = app_dir or Path(__file__).resolve().parents[1]
    st = _get_state(task_id)
    if not st:
        return None

    stage_times = st.get("stage_times") or {}
    segment_durations = st.get("segment_durations") or {}
    slow_segments = st.get("slow_segments") or []

    summary: dict[str, Any] = {
        "schema": "tubedub.pipeline_performance.v1",
        "task_id": task_id,
        "stage_times_sec": {
            k: v.get("duration_sec") for k, v in stage_times.items() if isinstance(v, dict)
        },
        "segment_avg_sec": {
            k: round(sum(v) / len(v), 2) for k, v in segment_durations.items() if v
        },
        "segment_count": {k: len(v) for k, v in segment_durations.items()},
        "slow_segments": slow_segments,
    }

    slowest = None
    slowest_d = 0.0
    for stage, durs in segment_durations.items():
        for i, d in enumerate(durs):
            if d > slowest_d:
                slowest_d = d
                slowest = {"stage": stage, "segment_index": i + 1, "duration_sec": d}
    summary["slowest_segment"] = slowest

    try:
        from engines.dub_task_state import AUTO_TASKS, STATE_LOCK

        with STATE_LOCK:
            task = AUTO_TASKS.get(task_id)
            if task:
                info = task.setdefault("info", {})
                info["pipeline_performance"] = summary
                timer = info.get("pipeline_timing") or {}
                if timer:
                    summary["pipeline_timer"] = timer
                runtime = info.get("runtime_diagnostics") or []
                if runtime:
                    summary["runtime_diagnostics"] = runtime
    except Exception:
        pass

    out = root / "output" / "diagnostics" / task_id / "pipeline_performance.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return out
    except Exception as exc:
        logger.debug("performance diagnostics save failed: %s", exc)
        return None
