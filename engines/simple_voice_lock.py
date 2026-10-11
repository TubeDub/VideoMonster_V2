# -*- coding: utf-8 -*-
"""Simple / Happy Path — UK voice lock (Stage 9 + 12 + 41).

One speaker → one voice (Stage 9). Dialogue (≥2 speaker_id) → one UK voice
per role, never the same Ostap for host and guest. Locale must match target
(uk → only uk-* / tts_uk). Forbidden: cs-CZ / sk-SK / pl-PL / auto-other.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("tubedub.simple_voice_lock")

DEFAULT_UK_VOICE = "uk-UA-OstapNeural"
DEFAULT_UK_VOICE_B = "uk-UA-PolinaNeural"
DEFAULT_TTS_UK_VOICE_A = "mykyta"
DEFAULT_TTS_UK_VOICE_B = "tetiana"
_FORBIDDEN_PREFIXES = ("cs-CZ", "pl-PL", "sk-SK", "hu-HU", "ro-RO", "bg-BG")
# Edge SSML: light living rate per speaker (not ±20%, not flat 0%).
EDGE_RATE_SPEAKER_A = "+4%"
EDGE_RATE_SPEAKER_B = "+6%"
EDGE_RATE_NATURAL = EDGE_RATE_SPEAKER_A
EDGE_PITCH_SPEAKER_A = "+0Hz"
EDGE_PITCH_SPEAKER_B = "+3Hz"
EDGE_QUESTION_PITCH_BUMP_HZ = 2


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


def _is_uk_question(text: str) -> bool:
    t = str(text or "").strip()
    if t.endswith("?"):
        return True
    low = t.lower()
    return low.startswith(("чи ", "невже", "хіба ", "чому ", "навіщо "))


def _bump_edge_pitch_hz(pitch: str, extra_hz: int) -> str:
    import re

    raw = str(pitch or "+0Hz").strip() or "+0Hz"
    match = re.match(r"([+-]?)(\d+(?:\.\d+)?)\s*Hz", raw, re.I)
    if not match:
        return f"{int(extra_hz):+d}Hz"
    sign = -1.0 if match.group(1) == "-" else 1.0
    val = sign * float(match.group(2)) + float(extra_hz)
    return f"{val:+.0f}Hz"


def living_uk_edge_rate(speaker_id: str | None = None) -> str:
    sid = str(speaker_id or "").upper()
    if "SPEAKER_B" in sid or sid.endswith("_B"):
        return EDGE_RATE_SPEAKER_B
    return EDGE_RATE_SPEAKER_A


def living_uk_edge_pitch(
    text: str = "",
    speaker_id: str | None = None,
) -> str:
    sid = str(speaker_id or "").upper()
    base = (
        EDGE_PITCH_SPEAKER_B
        if ("SPEAKER_B" in sid or sid.endswith("_B"))
        else EDGE_PITCH_SPEAKER_A
    )
    if _is_uk_question(text):
        return _bump_edge_pitch_hz(base, EDGE_QUESTION_PITCH_BUMP_HZ)
    return base


def _tts_uk_available() -> bool:
    try:
        from engines.tts_lang_lock import _tts_uk_backend_available

        return bool(_tts_uk_backend_available())
    except Exception:
        return False


def _is_forbidden_locale_voice(voice: str) -> bool:
    v = str(voice or "").strip()
    return any(v.startswith(bad) for bad in _FORBIDDEN_PREFIXES)


def _sanitize_voice_for_target(voice: str, target_lang: str) -> str:
    v = str(voice or "").strip()
    tgt = str(target_lang or "").split("-")[0].lower()
    if tgt == "uk" and _is_forbidden_locale_voice(v):
        logger.warning(
            "simple_voice_lock: attempted non-uk voice=%s — remap to UK pool (never silent cs/sk)",
            v,
        )
        v = ""
    if tgt == "uk":
        try:
            from engines.tts_backends import is_uk_tts_voice

            if is_uk_tts_voice(v):
                return v
        except Exception:
            pass
        if not v.startswith("uk-UA-") and v.lower() not in (
            "mykyta",
            "tetiana",
            "lada",
        ):
            if v:
                logger.warning(
                    "simple_voice_lock: voice=%s locale!=uk — forcing %s",
                    v,
                    DEFAULT_UK_VOICE,
                )
            return DEFAULT_UK_VOICE
    return v or DEFAULT_UK_VOICE


def resolve_uk_role_voices(
    task_info: dict[str, Any] | None = None,
    *,
    tts_uk_available: bool | None = None,
) -> dict[str, dict[str, str]]:
    """UK-only A/B pool. Mykyta is primary when tts_uk is installed.

    SPEAKER_A: mykyta (tts_uk) or Ostap (Edge fallback)
    SPEAKER_B: different UK voice — tetiana / Polina / Ostap, never the same as A
    BAN: cs/sk/pl and any non-uk.
    """
    info = dict(task_info or {})
    available = (
        _tts_uk_available() if tts_uk_available is None else bool(tts_uk_available)
    )
    if available:
        role_a = {
            "voice": DEFAULT_TTS_UK_VOICE_A,
            "backend": "tts_uk",
            "engine_id": "tts_uk",
            "pitch": EDGE_PITCH_SPEAKER_A,
            "rate": "1.02",
        }
        # Different from A: Edge Ostap (male) or local tetiana (female).
        role_b = {
            "voice": DEFAULT_UK_VOICE,
            "backend": "edge-offline",
            "engine_id": "edge-offline",
            "pitch": EDGE_PITCH_SPEAKER_B,
            "rate": EDGE_RATE_SPEAKER_B,
        }
    else:
        role_a = {
            "voice": DEFAULT_UK_VOICE,
            "backend": "edge-offline",
            "engine_id": "edge-offline",
            "pitch": EDGE_PITCH_SPEAKER_A,
            "rate": EDGE_RATE_SPEAKER_A,
        }
        role_b = {
            "voice": DEFAULT_UK_VOICE_B,
            "backend": "edge-offline",
            "engine_id": "edge-offline",
            "pitch": EDGE_PITCH_SPEAKER_B,
            "rate": EDGE_RATE_SPEAKER_B,
        }
    hint_a = str(info.get("speaker_a_voice") or "").strip()
    hint_b = str(info.get("speaker_b_voice") or "").strip()
    if hint_a and not _is_forbidden_locale_voice(hint_a):
        role_a["voice"] = _sanitize_voice_for_target(hint_a, "uk")
    if hint_b and not _is_forbidden_locale_voice(hint_b):
        role_b["voice"] = _sanitize_voice_for_target(hint_b, "uk")
    if str(role_b["voice"]).lower() == str(role_a["voice"]).lower():
        role_b["voice"] = (
            DEFAULT_UK_VOICE_B
            if role_a["voice"] != DEFAULT_UK_VOICE_B
            else DEFAULT_TTS_UK_VOICE_B
        )
        if role_b["voice"] == DEFAULT_TTS_UK_VOICE_B:
            role_b["backend"] = "tts_uk"
            role_b["engine_id"] = "tts_uk"
    return {"SPEAKER_A": role_a, "SPEAKER_B": role_b}


def _unique_speaker_ids(segments_data: list) -> list[str]:
    found: list[str] = []
    for seg in segments_data or []:
        if not isinstance(seg, dict) or seg.get("merged_into") is not None:
            continue
        try:
            from engines.diarization import extract_speaker_label

            sid = str(seg.get("speaker_id") or extract_speaker_label(seg) or "").strip()
        except Exception:
            sid = str(seg.get("speaker_id") or seg.get("speaker") or "").strip()
        if sid and sid.lower() not in ("default", "none", "") and sid not in found:
            found.append(sid)
    return found


def _apply_role_to_seg(seg: dict[str, Any], role: dict[str, str], speaker_id: str) -> None:
    voice = role["voice"]
    backend = str(role.get("backend") or role.get("engine_id") or "")
    spoken = str(
        seg.get("final_tts_text") or seg.get("plain_text") or seg.get("text") or ""
    )
    if backend in ("tts_uk",) or str(voice).lower() in ("mykyta", "tetiana", "lada"):
        rate = str(role.get("rate") or "1.02")
        pitch = str(role.get("pitch") or "0")
    else:
        rate = living_uk_edge_rate(speaker_id)
        pitch = living_uk_edge_pitch(spoken, speaker_id)
    seg["speaker_id"] = speaker_id
    seg["speaker"] = speaker_id
    seg["assigned_voice"] = voice
    seg["voice"] = voice
    seg["tts_voice"] = voice
    seg["tts_backend"] = backend or ""
    seg["tts_engine"] = role.get("engine_id") or ""
    seg["tts_language"] = "uk"
    seg["tts_rate"] = rate
    seg["tts_pitch"] = pitch
    seg["simple_voice_locked"] = True
    seg["uk_simple_prosody"] = True
    ai = seg.get("ai_voice")
    if isinstance(ai, dict):
        ai = dict(ai)
        ai["voice"] = voice
        ai["voice_uuid"] = voice
        ai["voice_id"] = voice
        seg["ai_voice"] = ai


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
    """Lock voices for Simple. One speaker → one voice; ≥2 roles → UK A/B pool.

    Stage 12: if voice locale != target → raise PIPELINE_VOICE_LOCALE (no mux).
    Stage 41: do not wipe SPEAKER_B onto Ostap after A's first success.
    """
    info = dict(task_info or {})
    target = str(info.get("target_lang") or "uk")
    tgt0 = target.split("-")[0].lower()

    try:
        from engines.diarization import ensure_speaker_ids

        if tgt0 == "uk":
            ensure_speaker_ids(list(segments_data or []), task_info=info)
    except Exception as exc:
        logger.debug("simple_voice_lock speaker_map skipped: %s", exc)

    if task_info is not None and info.get("speaker_map"):
        task_info["speaker_map"] = info["speaker_map"]
        task_info["speaker_ids"] = info.get("speaker_ids")

    roles = _unique_speaker_ids(segments_data)
    multi = tgt0 == "uk" and len(roles) >= 2

    if multi:
        pool = resolve_uk_role_voices(info)
        used: set[str] = set()
        pinned = 0
        role_lock: dict[str, dict[str, str]] = {}
        for i, sid in enumerate(roles):
            role_lock[sid] = pool["SPEAKER_A"] if i == 0 else pool["SPEAKER_B"]
        default_role = pool["SPEAKER_A"]
        for seg in segments_data or []:
            if not isinstance(seg, dict):
                continue
            if seg.get("merged_into") is not None or seg.get("archived"):
                continue
            sid = str(seg.get("speaker_id") or roles[0])
            role = role_lock.get(sid) or default_role
            prev = str(seg.get("assigned_voice") or seg.get("voice") or "").strip()
            if prev and prev != role["voice"]:
                pinned += 1
            _apply_role_to_seg(seg, role, sid)
            used.add(role["voice"])
        unique = sorted(used) if used else [default_role["voice"]]
        primary = pool["SPEAKER_A"]["voice"]
        stamp = {
            "simple_voice_locked": True,
            "pipeline_voice": primary,
            "tts_voice": primary,
            "unique_voices_used": len(unique),
            "unique_voices": unique,
            "voice_lock_pinned_segments": pinned,
            "voice_platform_skipped": "simple_per_speaker",
            "oss_locked_voice": primary,
            "speaker_voice_lock": {
                sid: {"voice": role_lock[sid]["voice"], "backend": role_lock[sid]["backend"]}
                for sid in roles
            },
            "per_speaker_voice_lock": True,
        }
        if task_info is not None:
            task_info.update(stamp)
            task_info["voice"] = primary
            task_info["target_lang"] = target
        logger.info(
            "simple_voice_lock: per-speaker roles=%s voices=%s target=%s",
            roles,
            unique,
            target,
        )
        return stamp

    raw_pipe = str(pipeline_voice or "").strip()
    voice = _sanitize_voice_for_target(raw_pipe or DEFAULT_UK_VOICE, target)
    if tgt0 == "uk" and _tts_uk_available():
        # B2: Mykyta is primary. Default Ostap / empty pipeline voice must not
        # keep Edge-first when tts_uk is installed.
        if not raw_pipe or raw_pipe == DEFAULT_UK_VOICE:
            voice = DEFAULT_TTS_UK_VOICE_A

    from engines.tts_lang_lock import assert_voice_matches_target

    ok, reason = assert_voice_matches_target(voice, target, raise_error=False)
    if not ok:
        if tgt0 == "uk":
            logger.warning(
                "simple_voice_lock: assert failed voice=%s (%s) — remap UK pool",
                voice,
                reason,
            )
            voice = DEFAULT_TTS_UK_VOICE_A if _tts_uk_available() else DEFAULT_UK_VOICE
            ok, reason = assert_voice_matches_target(voice, target, raise_error=False)
        if not ok:
            raise RuntimeError(f"PIPELINE_VOICE_LOCALE: {reason}")

    used = set()
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
        seg["tts_voice"] = voice
        seg["tts_language"] = "uk" if tgt0 == "uk" else target
        seg["simple_voice_locked"] = True
        if tgt0 == "uk":
            spoken = str(
                seg.get("final_tts_text") or seg.get("plain_text") or seg.get("text") or ""
            )
            sid = str(seg.get("speaker_id") or "SPEAKER_A")
            if voice.lower() == "mykyta":
                seg.setdefault("tts_rate", "1.02")
                seg.setdefault("tts_pitch", "0")
                seg.setdefault("tts_backend", "tts_uk")
            else:
                seg.setdefault("tts_rate", living_uk_edge_rate(sid))
                seg.setdefault("tts_pitch", living_uk_edge_pitch(spoken, sid))
                seg.setdefault("tts_backend", "edge-offline")
            seg.setdefault("speaker_id", sid)
            seg["uk_simple_prosody"] = True
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
        "per_speaker_voice_lock": False,
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
