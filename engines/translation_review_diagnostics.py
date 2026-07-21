"""Professional Translation Review diagnostics (timing / algorithms / quality).

Does not rewrite AutoDub — only computes display fields for Review UI.
"""

from __future__ import annotations

import re
from typing import Any

_WORD_RE = re.compile(r"[\w\u0400-\u04FF'-]+", re.UNICODE)


def estimate_tts_ms(text: str, lang: str = "uk") -> int:
    try:
        from engines.semantic_adaptation import estimate_tts_duration_ms

        return int(estimate_tts_duration_ms(text, lang) or 0)
    except Exception:
        t = str(text or "").strip()
        return int(len(t) / 13.5 * 1000) if t else 0


def fill_status(fill_pct: float) -> str:
    """green <95, yellow 95–100, orange 100–105, red >105."""
    if fill_pct <= 95:
        return "green"
    if fill_pct <= 100:
        return "yellow"
    if fill_pct <= 105:
        return "orange"
    return "red"


def status_label(status: str, *, lang: str = "uk") -> str:
    labels = {
        "uk": {
            "green": "Відмінно",
            "yellow": "Майже межа",
            "orange": "Можлива проблема",
            "red": "Потребує виправлення",
        },
        "ru": {
            "green": "Отлично",
            "yellow": "Почти предел",
            "orange": "Возможна проблема",
            "red": "Требует исправления",
        },
        "en": {
            "green": "Excellent",
            "yellow": "Near limit",
            "orange": "Possible issue",
            "red": "Needs fix",
        },
    }
    pack = labels.get((lang or "uk")[:2], labels["en"])
    return pack.get(status, status)


def overflow_text_split(text: str, *, slot_ms: int, tts_ms: int) -> dict[str, Any]:
    """Split text into fits / overflow tail for red highlighting."""
    t = str(text or "")
    if not t or slot_ms <= 0 or tts_ms <= 0 or tts_ms <= slot_ms:
        return {"fits": t, "overflow": "", "overflow_char_start": len(t)}
    ratio = slot_ms / float(tts_ms)
    cut = max(1, min(len(t) - 1, int(round(len(t) * ratio))))
    # Prefer split on word boundary
    if cut < len(t) and t[cut].isalnum():
        space = t.rfind(" ", 0, cut + 1)
        if space > cut * 0.5:
            cut = space + 1
    return {
        "fits": t[:cut],
        "overflow": t[cut:],
        "overflow_char_start": cut,
    }


def collect_algorithms(seg: dict[str, Any], audit: dict[str, Any]) -> list[str]:
    chips: list[str] = []
    stages = list(seg.get("adaptation_stages") or [])
    for st in stages:
        s = str(st or "").strip()
        if not s:
            continue
        low = s.lower()
        if "trim" in low and "silence" in low:
            chips.append("Trim Silence")
        elif "pause" in low:
            chips.append("Pause Optimization")
        elif "borrow" in low:
            chips.append("Borrow Time")
        elif "stretch" in low:
            chips.append("Stretch")
        elif "tempo" in low or "atempo" in low:
            chips.append("Tempo")
        elif "dsal" in low or "compress" in low:
            chips.append("DSAL")
        elif "semantic" in low:
            chips.append("Semantic Rewrite")
        elif "llm" in low:
            chips.append("LLM Rewrite")
        else:
            chips.append(s.replace("_", " ")[:40])

    if audit.get("semantic_adapted") or seg.get("semantic_adapted"):
        chips.append("Semantic Adapt")
    if audit.get("naturalizer_applied") or (
        audit.get("naturalizer_reasons") and "no_changes" not in (audit.get("naturalizer_reasons") or [])
    ):
        chips.append("Naturalizer")
    reasons = audit.get("naturalizer_reasons") or []
    if any(str(r).startswith("llm") or "llm" in str(r) for r in reasons):
        chips.append("LLM Rewrite")
    if any("literary" in str(r) or "catp" in str(r) for r in reasons):
        chips.append("Literary/CATP")
    if seg.get("dsal_applied"):
        chips.append("DSAL")
    if audit.get("timing_aware_applied"):
        chips.append("Timing Aware")
    catp = seg.get("catp") if isinstance(seg.get("catp"), dict) else {}
    if catp.get("selected_variant"):
        chips.append(f"CATP-{catp.get('selected_variant')}")
    if catp.get("rollback_due_to_length"):
        chips.append("Length Rollback")
    if seg.get("needs_manual_review"):
        chips.append("Manual Review")

    # Dedupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for c in chips:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out[:12]


def quality_breakdown(
    *,
    quality_score: float,
    quality_analysis: dict[str, Any] | None,
    duration_match_score: int,
    qd: dict[str, Any] | None,
    entity_ok: bool,
) -> dict[str, Any]:
    qa = quality_analysis or {}
    dims = (qa.get("dimensions") or ((qd or {}).get("dimensions") if qd else None)) or {}

    def _pick(*keys: str, default: float = 0.0) -> float:
        for k in keys:
            if k in dims and dims[k] is not None:
                try:
                    return float(dims[k])
                except (TypeError, ValueError):
                    pass
            if k in qa and qa[k] is not None:
                try:
                    return float(qa[k])
                except (TypeError, ValueError):
                    pass
        return float(default)

    translation = _pick("translation", "meaning", "semantic", default=quality_score)
    naturalness = _pick("naturalness", "fluency", "style", default=quality_score)
    entities = 100.0 if entity_ok else _pick("entities", "entity", default=70.0)
    timing = float(duration_match_score or _pick("timing", "slot_fit", default=0))
    if timing <= 1.0:
        timing *= 100.0
    tts = _pick("tts", "prosody", default=max(0.0, 100.0 - max(0.0, 100.0 - timing) * 0.5))
    overall = float(quality_score or _pick("overall", default=0))

    return {
        "translation": round(translation, 1),
        "naturalness": round(naturalness, 1),
        "entities": round(entities, 1),
        "timing": round(timing, 1),
        "tts": round(tts, 1),
        "overall": round(overall, 1),
    }


def build_segment_diagnostics(
    *,
    seg: dict[str, Any],
    audit: dict[str, Any],
    text: str,
    original: str,
    slot_ms: float,
    tts_ms: float,
    tgt_lang: str = "uk",
    warnings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    slot = max(0, int(slot_ms or 0))
    # Prefer measured TTS; else estimate from Final
    measured = max(0, int(tts_ms or 0))
    estimated = estimate_tts_ms(text, (tgt_lang or "uk").split("-")[0])
    speech_ms = measured if measured > 0 else estimated
    overflow_ms = max(0, speech_ms - slot) if slot > 0 else 0
    fill_pct = round((speech_ms / slot) * 100.0, 1) if slot > 0 and speech_ms > 0 else 0.0
    status = fill_status(fill_pct) if slot > 0 else "green"
    split = overflow_text_split(text, slot_ms=slot, tts_ms=speech_ms)

    warn_codes = {str(w.get("code") or "") for w in (warnings or []) if isinstance(w, dict)}
    meaning_loss = any(
        c in warn_codes
        for c in ("meaning_or_clause_loss", "over_shortening", "entity_missing", "preserved_token")
    )
    entity_missing = "entity_missing" in warn_codes or "preserved_token" in warn_codes

    # TTS truncation heuristic: speech past slot OR post_tts flag
    voice_truncated = bool(
        seg.get("tts_truncated")
        or seg.get("voice_truncated")
        or (overflow_ms > 120 and measured > 0)
        or (seg.get("post_tts_retry") or {}).get("truncated")
    )
    voice_finished_naturally = (not voice_truncated) and (overflow_ms <= 80 or slot <= 0)

    start_ms = int(seg.get("start_ms") or seg.get("start") or 0)
    end_ms = int(seg.get("end_ms") or seg.get("end") or (start_ms + slot))
    if end_ms < start_ms:
        end_ms = start_ms + slot

    # Adaptive Segmentation forecast / Split·Merge advice (TZ §9–10)
    expected_tts_ms = estimated if measured <= 0 else speech_ms
    word_count = len(_WORD_RE.findall(str(text or original or "")))
    seg_advice = ""
    seg_status = ""
    try:
        from engines.adaptive_segmentation import (
            estimate_expected_tts_ms,
            segment_recommendation,
        )

        # Pre-TTS: forecast from original; post-TTS: use measured speech
        forecast_src = str(original or text or "")
        if measured <= 0 and forecast_src:
            expected_tts_ms = estimate_expected_tts_ms(
                forecast_src, tgt_lang=(tgt_lang or "uk").split("-")[0]
            )
        rec = segment_recommendation(slot_ms=slot, expected_tts_ms=expected_tts_ms)
        seg_advice = str(rec.get("advice") or "")
        seg_status = str(rec.get("status") or "")
        if measured <= 0 and expected_tts_ms > 0:
            fill_pct = (
                round((expected_tts_ms / slot) * 100.0, 1) if slot > 0 else fill_pct
            )
            status = fill_status(fill_pct) if slot > 0 else status
    except Exception:
        pass

    return {
        "tts_ms": speech_ms,
        "tts_ms_measured": measured,
        "tts_ms_estimated": estimated,
        "expected_tts_ms": int(expected_tts_ms or 0),
        "word_count": word_count,
        "seg_advice": seg_advice,
        "seg_status": seg_status,
        "slot_ms": slot,
        "overflow_ms": overflow_ms,
        "fill_pct": fill_pct,
        "fill_status": status,
        "status_label": seg_status
        or status_label(status, lang=tgt_lang)
        or "",
        "text_fits": split["fits"],
        "text_overflow": split["overflow"],
        "overflow_char_start": split["overflow_char_start"],
        "algorithms": collect_algorithms(seg, audit),
        "speech_end": {
            "original_start_ms": start_ms,
            "original_end_ms": end_ms,
            "dub_end_ms": start_ms + speech_ms,
            "original_duration_ms": max(0, end_ms - start_ms),
            "dub_duration_ms": speech_ms,
        },
        "meaning_loss_risk": meaning_loss,
        "entity_risk": entity_missing,
        "voice_truncated": voice_truncated,
        "voice_finished_naturally": voice_finished_naturally,
        "manual_review_required": bool(
            seg.get("needs_manual_review") or (voice_truncated and overflow_ms > 250)
        ),
    }
