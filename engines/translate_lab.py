"""Translate section — same UniversalTranslationPipeline as full dub (no duplicate MT)."""

from __future__ import annotations

import copy
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

SESSIONS: dict[str, dict[str, Any]] = {}
SESSION_LOCK = threading.Lock()
SESSION_TTL_SEC = 3600 * 6


def _now() -> float:
    return time.time()


def _purge_old_sessions() -> None:
    cutoff = _now() - SESSION_TTL_SEC
    with SESSION_LOCK:
        dead = [k for k, v in SESSIONS.items() if float(v.get("ts") or 0) < cutoff]
        for k in dead:
            SESSIONS.pop(k, None)


def prepare_source_segments(
    text: str,
    *,
    timing_map: list | None = None,
    clean: bool = False,
) -> tuple[list[str], list, str | None]:
    """Split source into pipeline segments; optionally clean SRT first."""
    from engines.cleaner import clean_transcript, split_by_timing_map

    raw = str(text or "").strip()
    tm = list(timing_map or [])
    cleaned_text: str | None = None

    if clean and raw:
        raw, tm, _review = clean_transcript(raw)
        cleaned_text = raw

    if not raw:
        return [], tm, cleaned_text

    if tm:
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        if len(lines) == len(tm):
            return lines, tm, cleaned_text
        split = split_by_timing_map(raw, tm)
        if split:
            return split, tm, cleaned_text

    paras = [p.strip() for p in re.split(r"\n\s*\n", raw) if p.strip()]
    if len(paras) > 1:
        return paras, tm, cleaned_text

    sentences = re.split(r"(?<=[.!?…])\s+", raw)
    sentences = [s.strip() for s in sentences if s.strip()]
    if len(sentences) > 1:
        return sentences, tm, cleaned_text

    return [raw], tm, cleaned_text


def run_pipeline_translate(
    segments: list[str],
    timing_map: list,
    src_lang: str,
    tgt_lang: str,
    *,
    app_dir: Path | None = None,
    session_id: str = "",
) -> dict[str, Any]:
    """Run UniversalTranslationPipeline — identical path to auto_dub translate step."""
    from engines.translation_pipeline import UniversalTranslationPipeline

    base = app_dir or Path(__file__).resolve().parent.parent
    sid = session_id or uuid.uuid4().hex[:16]
    translate_meta: list[dict[str, Any]] = []

    pipe = UniversalTranslationPipeline(app_dir=base, task_id=f"translate_{sid}")
    result = pipe.translate_segments(
        segments,
        timing_map,
        src_lang,
        tgt_lang,
        translate_meta_out=translate_meta,
    )
    pipe.flush_quality_log(
        src=result.meta.get("src") or src_lang,
        tgt=result.meta.get("tgt") or tgt_lang,
        engines=result.meta.get("engines"),
    )

    audits = pipe.quality_log.records_as_dicts()
    translated = list(result.segments or [])

    if timing_map:
        from engines.cleaner import align_segments_to_timing_map, split_by_timing_map

        translated = align_segments_to_timing_map(translated, timing_map)
        if len(translated) != len(timing_map):
            block = "\n".join(result.segments)
            translated = split_by_timing_map(block, timing_map)

    display = "\n\n".join(s.strip() for s in translated if str(s).strip())

    info = {
        "task_id": sid,
        "source_lang": src_lang,
        "detected_lang": src_lang,
        "target_lang": tgt_lang,
        "source_segments": list(segments),
        "translated_segments": translated,
        "timing_map": list(timing_map or []),
        "translation_audits": audits,
        "translation_trace_log": result.meta.get("translation_trace_log") or "",
        "engines": result.meta.get("engines") or [],
        "meta": result.meta,
    }

    _purge_old_sessions()
    with SESSION_LOCK:
        SESSIONS[sid] = {"ts": _now(), "info": copy.deepcopy(info)}

    try:
        from engines.translate_session_log import save_session_log

        log_path = save_session_log(
            base,
            sid,
            source_lang=src_lang,
            target_lang=tgt_lang,
            source_segments=list(segments),
            translated_segments=translated,
            audits=audits,
            meta=result.meta,
            trace_log=str(result.meta.get("translation_trace_log") or ""),
        )
    except Exception:
        log_path = None

    return {
        "session_id": sid,
        "translated": display,
        "segments": translated,
        "audits": audits,
        "meta": result.meta,
        "translate_meta": translate_meta,
        "log_path": str(log_path) if log_path else "",
    }


def get_session(session_id: str) -> dict[str, Any] | None:
    with SESSION_LOCK:
        row = SESSIONS.get(str(session_id or ""))
        if not row:
            return None
        return copy.deepcopy(row.get("info") or {})


def build_inspector_report(session_id: str) -> dict[str, Any]:
    from engines.translation_inspector import build_translation_inspector

    info = get_session(session_id)
    if not info:
        return {"enabled": False, "error": "session_not_found", "segments": []}
    info["task_id"] = session_id
    return build_translation_inspector(info)
