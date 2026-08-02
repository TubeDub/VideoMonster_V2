# -*- coding: utf-8 -*-
"""Stage 12/18 — TTS language lock for Simple (target=uk → only Ukrainian text/voice)."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.tts_lang_lock")

_CYR = re.compile(r"[\u0400-\u04FF]")
_LAT = re.compile(r"[A-Za-z]")

# Always-banned cross-locale voices when targeting uk (Stage 18: +ru-RU).
_FORBIDDEN_VOICE_PREFIXES_FOR_UK = (
    "cs-CZ",
    "pl-PL",
    "sk-SK",
    "hu-HU",
    "ro-RO",
    "bg-BG",
    "ru-RU",
    "en-US",
    "en-GB",
    "de-DE",
    "fr-FR",
)
# Legacy alias used by older imports/tests.
_FORBIDDEN_VOICE_PREFIXES = _FORBIDDEN_VOICE_PREFIXES_FOR_UK

# Stage 17/18: en→uk Edge TTS requires ≥55% cyrillic letters.
DEFAULT_UK_CYRILLIC_MIN = 0.55


def cyrillic_letter_ratio(text: str) -> float:
    letters = [c for c in str(text or "") if c.isalpha()]
    if not letters:
        return 0.0
    cyr = sum(1 for c in letters if _CYR.match(c))
    return cyr / len(letters)


def is_uk_tts_text_ok(
    text: str, *, min_ratio: float = DEFAULT_UK_CYRILLIC_MIN
) -> bool:
    clean = " ".join(str(text or "").split()).strip()
    if not clean:
        return False
    return cyrillic_letter_ratio(clean) >= float(min_ratio)


def voice_locale_prefix(voice: str) -> str:
    v = str(voice or "").strip()
    parts = v.split("-")
    if len(parts) >= 2:
        return f"{parts[0]}-{parts[1]}"
    return v[:5]


def assert_voice_matches_target(
    voice: str,
    target_lang: str,
    *,
    raise_error: bool = True,
) -> tuple[bool, str]:
    """Return (ok, reason). For uk: Edge uk-UA-*, tts_uk, or Piper uk_UA-*."""
    tgt = str(target_lang or "").split("-")[0].lower()
    v = str(voice or "").strip()
    if not v:
        msg = "empty_voice"
        if raise_error:
            raise RuntimeError(f"PIPELINE_VOICE_LOCALE: {msg}")
        return False, msg
    if tgt == "uk":
        for bad in _FORBIDDEN_VOICE_PREFIXES_FOR_UK:
            if v.startswith(bad):
                msg = f"forbidden_voice={v} for target={tgt}"
                if raise_error:
                    raise RuntimeError(f"PIPELINE_VOICE_LOCALE: {msg}")
                return False, msg
        try:
            from engines.tts_backends import is_uk_tts_voice

            if is_uk_tts_voice(v):
                return True, "ok"
        except Exception:
            pass
        if not v.startswith("uk-UA-"):
            msg = f"voice={v} locale!={tgt} (need uk-UA-* / tts_uk / piper uk_UA-*)"
            if raise_error:
                raise RuntimeError(f"PIPELINE_VOICE_LOCALE: {msg}")
            return False, msg
    return True, "ok"


def force_remt_segment_no_cache(
    source_text: str,
    *,
    src_lang: str,
    tgt_lang: str,
    app_dir: Path | None = None,
) -> str:
    """One Marian pass without reading/writing MT cache."""
    from engines.mt.glossary_en_uk import finalize_mt_text
    from engines.mt.stable_translate import translate_direct_marian

    base = Path(app_dir) if app_dir else Path(__file__).resolve().parents[1]
    src = " ".join(str(source_text or "").split()).strip()
    if not src:
        return ""
    out, _meta = translate_direct_marian(
        src, src_lang, tgt_lang, app_dir=base, segment_index=-1
    )
    return finalize_mt_text(src_lang, tgt_lang, str(out or "").strip())


def guard_uk_tts_text(
    text: str,
    *,
    source_text: str = "",
    src_lang: str = "en",
    tgt_lang: str = "uk",
    app_dir: Path | None = None,
    segment_index: int = -1,
    allow_remt: bool = True,
    fail_loud: bool = False,
) -> tuple[str, dict[str, Any]]:
    """Ensure TTS text is Ukrainian. Returns (text, meta).

    If cyrillic ratio < 0.55 → remt once.
    Stage 18 Simple/Happy Path (fail_loud): remt fail → raise PIPELINE_LANG_MIX
    (never skip→empty silence). Legacy: skip with text="".
    """
    meta: dict[str, Any] = {
        "tts_lang_ok": True,
        "cyrillic_ratio": 0.0,
        "rejected_non_target": False,
        "remt_attempted": False,
        "skipped": False,
        "fail_loud": bool(fail_loud),
    }
    tgt = str(tgt_lang or "").split("-")[0].lower()
    clean = " ".join(str(text or "").split()).strip()
    if tgt != "uk":
        meta["tts_lang_ok"] = True
        return clean, meta

    if not clean:
        meta["tts_lang_ok"] = False
        meta["rejected_non_target"] = True
        if fail_loud:
            raise RuntimeError(
                f"PIPELINE_LANG_MIX: empty TTS text seg#{segment_index if segment_index >= 0 else '?'}"
            )
        meta["skipped"] = True
        return "", meta

    ratio = cyrillic_letter_ratio(clean)
    meta["cyrillic_ratio"] = round(ratio, 3)
    if is_uk_tts_text_ok(clean, min_ratio=DEFAULT_UK_CYRILLIC_MIN):
        return clean, meta

    logger.warning(
        "[TTS] reject_non_target lang_mix seg#%s ratio=%.2f text=%.80s",
        segment_index if segment_index >= 0 else "?",
        ratio,
        clean,
    )
    meta["rejected_non_target"] = True
    meta["tts_lang_ok"] = False

    if allow_remt and source_text.strip():
        meta["remt_attempted"] = True
        try:
            remt = force_remt_segment_no_cache(
                source_text,
                src_lang=src_lang,
                tgt_lang=tgt_lang,
                app_dir=app_dir,
            )
            remt_ratio = cyrillic_letter_ratio(remt)
            meta["remt_cyrillic_ratio"] = round(remt_ratio, 3)
            if is_uk_tts_text_ok(remt, min_ratio=DEFAULT_UK_CYRILLIC_MIN):
                logger.info(
                    "[TTS] remt_ok seg#%s ratio=%.2f",
                    segment_index if segment_index >= 0 else "?",
                    remt_ratio,
                )
                meta["tts_lang_ok"] = True
                meta["rejected_non_target"] = False
                meta["remt_ok"] = True
                meta["engine"] = "marian_remt"
                return remt, meta
            meta["remt_ok"] = False
            meta["fail_reason"] = f"remt_still_bad ratio={remt_ratio:.2f}"
            logger.warning(
                "[TTS] remt_still_bad seg#%s ratio=%.2f",
                segment_index if segment_index >= 0 else "?",
                remt_ratio,
            )
        except Exception as exc:
            logger.warning("[TTS] remt_failed seg#%s: %s", segment_index, exc)
            meta["remt_error"] = str(exc)
            meta["remt_ok"] = False
            meta["fail_reason"] = f"remt_failed:{exc}"
    else:
        meta["fail_reason"] = "no_remt_or_empty_source"

    if fail_loud:
        reason = meta.get("fail_reason") or "cyrillic_ratio_low"
        raise RuntimeError(
            f"PIPELINE_LANG_MIX: seg#{segment_index if segment_index >= 0 else '?'} "
            f"{reason} ratio={meta.get('cyrillic_ratio')} — refuse skip→silence"
        )

    meta["skipped"] = True
    return "", meta


def enforce_segments_lang_lock(
    segments_data: list,
    *,
    target_lang: str,
    source_lang: str = "en",
    app_dir: Path | None = None,
    fail_if_reject_ratio: float = 0.20,
    simple_mode: bool = False,
    fail_loud: bool | None = None,
) -> dict[str, Any]:
    """In-place guard on segments_data TTS texts. Returns stats; may raise.

    Stage 18 Simple: fail_loud — never skip_tts / empty text (raise instead).
    """
    tgt = str(target_lang or "").split("-")[0].lower()
    # Stage 18b: simple_mode OR fail_loud → never skip→silence.
    loud = bool(simple_mode) or bool(fail_loud)
    stats: dict[str, Any] = {
        "checked": 0,
        "rejected_non_target": 0,
        "remt_ok": 0,
        "skipped": 0,
        "ok": 0,
        "fail_loud": loud,
    }
    if tgt != "uk":
        return stats

    active = 0
    for i, seg in enumerate(segments_data or []):
        if not isinstance(seg, dict):
            continue
        if seg.get("merged_into") is not None or seg.get("archived"):
            continue
        # Stage 18b: Simple/fail_loud — never skip→silence; clear stale skip flags.
        if loud and (seg.get("tts_blocked") or seg.get("skip_tts")):
            seg.pop("skip_tts", None)
            seg.pop("tts_blocked", None)
            seg.pop("tts_skip_reason", None)
        elif seg.get("tts_blocked") or seg.get("skip_tts"):
            continue
        text = str(
            seg.get("final_tts_text")
            or seg.get("tts_text")
            or seg.get("plain_text")
            or seg.get("translated_text")
            or seg.get("text")
            or ""
        ).strip()
        if not text:
            if loud:
                raise RuntimeError(
                    f"PIPELINE_LANG_MIX: empty TTS text seg#{i} — refuse skip→silence"
                )
            continue
        active += 1
        stats["checked"] += 1
        src = str(
            seg.get("original")
            or seg.get("original_text")
            or seg.get("whisper_text")
            or seg.get("source_text")
            or ""
        )
        new_text, meta = guard_uk_tts_text(
            text,
            source_text=src,
            src_lang=source_lang,
            tgt_lang=target_lang,
            app_dir=app_dir,
            segment_index=i,
            allow_remt=True,
            fail_loud=loud,
        )
        seg["tts_lang_hint"] = "uk" if meta.get("tts_lang_ok") else "reject"
        seg["tts_cyrillic_ratio"] = meta.get("cyrillic_ratio")
        if meta.get("skipped"):
            # Legacy non-loud path only — fail_loud guard never returns skipped.
            if loud:
                raise RuntimeError(
                    f"PIPELINE_LANG_MIX: seg#{i} rejected_non_target — refuse skip→silence"
                )
            stats["skipped"] += 1
            stats["rejected_non_target"] += 1
            seg["skip_tts"] = True
            seg["tts_blocked"] = True
            seg["tts_skip_reason"] = "reject_non_target_lang_mix"
            for k in (
                "final_tts_text",
                "tts_text",
                "plain_text",
                "text",
                "translated_text",
            ):
                if k in seg:
                    seg[k] = ""
            continue
        if meta.get("remt_attempted") and meta.get("tts_lang_ok"):
            stats["remt_ok"] += 1
            for k in (
                "final_tts_text",
                "tts_text",
                "plain_text",
                "text",
                "translated_text",
            ):
                if k in seg or k in ("final_tts_text", "tts_text", "plain_text"):
                    seg[k] = new_text
            if meta.get("engine"):
                seg["mt_engine"] = meta["engine"]
            stats["ok"] += 1
            continue
        if meta.get("rejected_non_target"):
            stats["rejected_non_target"] += 1
        else:
            stats["ok"] += 1

    if not loud and active > 0 and stats["rejected_non_target"] / active > fail_if_reject_ratio:
        raise RuntimeError(
            f"PIPELINE_LANG_MIX: {stats['rejected_non_target']}/{active} segments "
            f"rejected_non_target (>{fail_if_reject_ratio:.0%}) — abort before mux"
        )
    return stats


def pre_mux_tts_integrity(
    segments_data: list,
    *,
    target_lang: str,
    timeline_ms: float | None = None,
    simple_mode: bool = False,
) -> dict[str, Any]:
    """Log per-segment voice/text; check duration sum vs timeline loosely."""
    rows: list[dict[str, Any]] = []
    dur_sum = 0.0
    rejected = 0
    voiced = 0
    for i, seg in enumerate(segments_data or []):
        if not isinstance(seg, dict) or seg.get("merged_into") is not None:
            continue
        text = str(
            seg.get("final_tts_text")
            or seg.get("tts_text")
            or seg.get("plain_text")
            or ""
        ).strip()
        voice = str(
            seg.get("voice_id")
            or seg.get("assigned_voice")
            or seg.get("voice")
            or ""
        )
        hint = str(seg.get("tts_lang_hint") or "")
        if seg.get("skip_tts") or seg.get("tts_blocked"):
            rejected += 1
            if simple_mode and str(target_lang or "").split("-")[0].lower() == "uk":
                raise RuntimeError(
                    f"PIPELINE_LANG_MIX: seg#{i} skip_tts on Simple uk — refuse mux silence"
                )
        if voice and str(target_lang or "").split("-")[0].lower() == "uk":
            assert_voice_matches_target(voice, target_lang, raise_error=True)
        dur = float(seg.get("playback_duration") or seg.get("tts_duration") or 0)
        if dur <= 0:
            try:
                start = float(seg.get("start") or 0)
                end = float(seg.get("end") or 0)
                dur = max(0.0, end - start)
            except Exception:
                dur = 0.0
        if text and not seg.get("skip_tts"):
            voiced += 1
            dur_sum += dur
        row = {
            "index": i,
            "voice_id": voice,
            "tts_lang_hint": hint
            or (
                "uk"
                if cyrillic_letter_ratio(text) >= DEFAULT_UK_CYRILLIC_MIN
                else "?"
            ),
            "text": text[:80],
            "tts_text_hash": str(seg.get("tts_text_hash") or ""),
            "duration_s": round(dur, 3),
        }
        rows.append(row)
        logger.info(
            "[TTS integrity] seg#%d voice=%s lang=%s hash=%s dur=%.2f text=%.80s",
            i + 1,
            voice,
            row["tts_lang_hint"],
            row["tts_text_hash"] or "-",
            dur,
            text,
        )

    report = {
        "segments_logged": len(rows),
        "voiced": voiced,
        "rejected_or_skipped": rejected,
        "tts_duration_sum_s": round(dur_sum, 3),
        "timeline_ms": timeline_ms,
        "rows": rows,
    }
    if timeline_ms and timeline_ms > 0 and dur_sum > 0:
        ratio = (dur_sum * 1000.0) / float(timeline_ms)
        report["duration_vs_timeline"] = round(ratio, 3)
        if ratio < 0.35 or ratio > 2.5:
            logger.warning(
                "[TTS integrity] duration_sum/timeline odd ratio=%.2f sum=%.1fs timeline_ms=%.0f",
                ratio,
                dur_sum,
                timeline_ms,
            )
    if voiced > 0 and rejected / max(voiced + rejected, 1) > 0.20:
        raise RuntimeError(
            f"PIPELINE_LANG_MIX: {rejected} skipped / {voiced} voiced "
            "(>20% rejected_non_target) — refuse mux"
        )
    return report
