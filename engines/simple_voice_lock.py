# -*- coding: utf-8 -*-
"""Simple / Happy Path — one Edge voice for the whole clip (Stage 9).

Narration (George Jr. etc.) must not flip Ostap ↔ Polina mid-roll.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("tubedub.simple_voice_lock")

DEFAULT_UK_VOICE = "uk-UA-OstapNeural"


def should_lock_simple_voice(task_info: dict[str, Any] | None = None) -> bool:
    info = dict(task_info or {})
    if info.get("simple_pipeline") or info.get("happy_path") or info.get("simple_voice_locked"):
        return True
    try:
        from engines.happy_path import is_simple_mode

        return bool(is_simple_mode(info))
    except Exception:
        mode = str(info.get("user_mode") or "").strip().lower()
        return mode in ("basic", "simple", "")


def resolve_pipeline_voice(
    task_info: dict[str, Any] | None = None,
    *,
    fallback: str | None = None,
) -> str:
    info = dict(task_info or {})
    candidates = (
        fallback,
        info.get("voice"),
        info.get("tts_voice"),
        info.get("pipeline_voice"),
    )
    for c in candidates:
        v = str(c or "").strip()
        if v and "mock" not in v.lower() and "silent" not in v.lower():
            return v
    lang = str(info.get("target_lang") or "").split("-")[0].lower()
    if lang == "uk":
        return DEFAULT_UK_VOICE
    return DEFAULT_UK_VOICE


def lock_simple_pipeline_voice(
    segments_data: list,
    *,
    pipeline_voice: str,
    task_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Force one voice on every active segment. Returns stamp dict for task.info."""
    voice = str(pipeline_voice or "").strip() or DEFAULT_UK_VOICE
    used: set[str] = set()
    pinned = 0
    for seg in segments_data or []:
        if not isinstance(seg, dict):
            continue
        if seg.get("merged_into") is not None or seg.get("archived"):
            continue
        prev = str(seg.get("assigned_voice") or seg.get("voice") or "").strip()
        if prev and prev != voice:
            pinned += 1
        seg["assigned_voice"] = voice
        seg["voice"] = voice
        seg["simple_voice_locked"] = True
        # Drop multi-speaker AI hints that reintroduce Polina etc.
        ai = seg.get("ai_voice")
        if isinstance(ai, dict):
            ai = dict(ai)
            ai["voice"] = voice
            ai["voice_uuid"] = voice
            ai["voice_id"] = voice
            seg["ai_voice"] = ai
        used.add(voice)

    unique = sorted(used) if used else [voice]
    stamp = {
        "simple_voice_locked": True,
        "pipeline_voice": voice,
        "tts_voice": voice,
        "unique_voices_used": len(unique),
        "unique_voices": unique,
        "voice_lock_pinned_segments": pinned,
        "voice_platform_skipped": "simple_single_voice",
    }
    if task_info is not None:
        task_info.update(stamp)
        task_info["voice"] = voice
    if len(unique) > 1:
        logger.error(
            "simple_voice_lock ASSERT failed unique=%s — forcing pin to %s",
            unique,
            voice,
        )
        # Force again
        for seg in segments_data or []:
            if isinstance(seg, dict) and seg.get("merged_into") is None:
                seg["assigned_voice"] = voice
                seg["voice"] = voice
        stamp["unique_voices_used"] = 1
        stamp["unique_voices"] = [voice]
        stamp["voice_lock_assert_forced"] = True
        if task_info is not None:
            task_info.update(stamp)
    logger.info(
        "simple_voice_lock: voice=%s unique=%d pinned=%d",
        voice,
        stamp["unique_voices_used"],
        pinned,
    )
    return stamp


def collect_unique_voices(segments_data: list) -> list[str]:
    found: set[str] = set()
    for seg in segments_data or []:
        if not isinstance(seg, dict) or seg.get("merged_into") is not None:
            continue
        for key in ("assigned_voice", "voice"):
            v = str(seg.get(key) or "").strip()
            if v:
                found.add(v)
    return sorted(found)
