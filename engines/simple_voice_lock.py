# -*- coding: utf-8 -*-
"""Simple / Happy Path — one Edge voice for the whole clip (Stage 9 + 12).

Narration must not flip Ostap ↔ Polina. Stage 12: voice locale must match target
(uk → only uk-UA-*). Forbidden: cs-CZ / pl-PL / auto-other.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("tubedub.simple_voice_lock")

DEFAULT_UK_VOICE = "uk-UA-OstapNeural"
_FORBIDDEN_PREFIXES = ("cs-CZ", "pl-PL", "sk-SK", "hu-HU", "ro-RO", "bg-BG")


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


def _sanitize_voice_for_target(voice: str, target_lang: str) -> str:
    v = str(voice or "").strip()
    tgt = str(target_lang or "").split("-")[0].lower()
    for bad in _FORBIDDEN_PREFIXES:
        if v.startswith(bad):
            logger.error(
                "simple_voice_lock: rejected forbidden voice=%s — fallback uk-UA",
                v,
            )
            v = ""
            break
    if tgt == "uk":
        try:
            from engines.tts_backends import is_uk_tts_voice

            if is_uk_tts_voice(v):
                return v
        except Exception:
            pass
        if not v.startswith("uk-UA-"):
            if v:
                logger.error(
                    "simple_voice_lock: voice=%s locale!=uk — forcing %s",
                    v,
                    DEFAULT_UK_VOICE,
                )
            return DEFAULT_UK_VOICE
    return v or DEFAULT_UK_VOICE


def resolve_pipeline_voice(
    task_info: dict[str, Any] | None = None,
    *,
    fallback: str | None = None,
) -> str:
    info = dict(task_info or {})
    target = str(info.get("target_lang") or "uk")
    candidates = (
        fallback,
        info.get("voice"),
        info.get("tts_voice"),
        info.get("pipeline_voice"),
    )
    for c in candidates:
        v = str(c or "").strip()
        if v and "mock" not in v.lower() and "silent" not in v.lower():
            return _sanitize_voice_for_target(v, target)
    return _sanitize_voice_for_target(DEFAULT_UK_VOICE, target)


def lock_simple_pipeline_voice(
    segments_data: list,
    *,
    pipeline_voice: str,
    task_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Force one voice on every active segment. Returns stamp dict for task.info.

    Stage 12: if voice locale != target → raise PIPELINE_VOICE_LOCALE (no mux).
    """
    info = dict(task_info or {})
    target = str(info.get("target_lang") or "uk")
    voice = _sanitize_voice_for_target(
        str(pipeline_voice or "").strip() or DEFAULT_UK_VOICE, target
    )

    from engines.tts_lang_lock import assert_voice_matches_target

    ok, reason = assert_voice_matches_target(voice, target, raise_error=False)
    if not ok:
        # Last resort remap to Ostap for uk; still hard-fail if impossible.
        if str(target).split("-")[0].lower() == "uk":
            voice = DEFAULT_UK_VOICE
            ok, reason = assert_voice_matches_target(voice, target, raise_error=False)
        if not ok:
            raise RuntimeError(f"PIPELINE_VOICE_LOCALE: {reason}")

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
        ai = seg.get("ai_voice")
        if isinstance(ai, dict):
            ai = dict(ai)
            ai["voice"] = voice
            ai["voice_uuid"] = voice
            ai["voice_id"] = voice
            seg["ai_voice"] = ai
        used.add(voice)

    unique = sorted(used) if used else [voice]
    if len(unique) != 1:
        for seg in segments_data or []:
            if isinstance(seg, dict) and seg.get("merged_into") is None:
                seg["assigned_voice"] = voice
                seg["voice"] = voice
        unique = [voice]

    stamp = {
        "simple_voice_locked": True,
        "pipeline_voice": voice,
        "tts_voice": voice,
        "unique_voices_used": 1,
        "unique_voices": unique,
        "voice_lock_pinned_segments": pinned,
        "voice_platform_skipped": "simple_single_voice",
        "oss_locked_voice": voice,
    }
    if task_info is not None:
        task_info.update(stamp)
        task_info["voice"] = voice
        task_info["target_lang"] = target
    logger.info(
        "simple_voice_lock: voice=%s unique=1 pinned=%d target=%s",
        voice,
        pinned,
        target,
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
