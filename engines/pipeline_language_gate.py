"""Segment language validation and pipeline text trace logging."""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("tubedub.pipeline_language_gate")

_LATIN_WORD = re.compile(r"\b[A-Za-z]{2,}\b")
_CYRILLIC = re.compile(r"[а-яА-ЯёЁіїєІЇЄґҐ]")


def _base_lang(lang: str) -> str:
    return str(lang or "").strip().lower().split("-")[0]


def detect_segment_language(text: str, *, target_lang: str = "") -> str:
    """Heuristic: uk/ru/en/unknown based on script ratio."""
    t = str(text or "").strip()
    if not t:
        return "empty"
    cyr = len(_CYRILLIC.findall(t))
    lat_words = _LATIN_WORD.findall(t)
    lat_chars = sum(len(w) for w in lat_words)
    total = cyr + lat_chars
    if total == 0:
        return "unknown"
    lat_ratio = lat_chars / total
    if lat_ratio > 0.45:
        return "en"
    base = _base_lang(target_lang)
    if base in ("uk", "ru", "be"):
        return base if cyr > 0 else "unknown"
    return base or "unknown"


def english_leak_tokens(text: str, original: str, target_lang: str) -> list[str]:
    from engines.language_intelligence import rules as R

    return R.detect_english_leak(text, original, target_lang)


def is_critical_language_mismatch(
    text: str,
    *,
    target_lang: str,
    original: str = "",
) -> tuple[bool, str]:
    """True when final text is clearly not in target language (e.g. EN in UK dub)."""
    t = str(text or "").strip()
    if not t:
        return False, ""
    base = _base_lang(target_lang)
    if base not in ("uk", "ru", "be"):
        return False, ""

    detected = detect_segment_language(t, target_lang=target_lang)
    if detected == "en":
        leaked = english_leak_tokens(t, original, target_lang)
        if leaked or re.search(r"\b(that|was|from|the|and|but|had|have|been)\b", t, re.I):
            return True, f"english_in_{base}_track"

    cyr = len(_CYRILLIC.findall(t))
    lat = len(re.findall(r"[a-zA-Z]", t))
    if cyr > 0 and lat > 0 and lat / max(cyr + lat, 1) > 0.35:
        return True, "latin_dominant_in_cyrillic_track"

    if base in ("uk", "ru") and cyr == 0 and lat > 8:
        return True, "no_cyrillic_in_target_track"

    return False, ""


def log_segment_pipeline_trace(
    task_id: str,
    segments_data: list[dict[str, Any]],
    *,
    source_segments: list[str] | None = None,
    target_lang: str = "",
    audits: list[dict[str, Any]] | None = None,
) -> None:
    """Per-segment trace: original / translated / adapted / final / language / files."""
    audit_by = {int(a.get("index", -1)): a for a in (audits or [])}
    src_rows = list(source_segments or [])
    for idx, seg in enumerate(segments_data):
        if seg.get("merged_into") is not None:
            continue
        audit = audit_by.get(idx, {})
        original = (
            src_rows[idx] if idx < len(src_rows) else str(seg.get("source_text") or "")
        )
        translated = str(
            audit.get("final_text")
            or audit.get("translation_text")
            or seg.get("translation_text")
            or seg.get("plain_text")
            or ""
        ).strip()
        adapted = str(
            seg.get("adapted_text")
            or audit.get("semantic_text")
            or seg.get("semantic_text")
            or ""
        ).strip()
        final = str(
            seg.get("text") or seg.get("plain_text") or seg.get("tts_text") or ""
        ).strip()
        lang = detect_segment_language(final, target_lang=target_lang)
        logger.info(
            "[SegmentTrace] task=%s idx=%d lang=%s target=%s "
            "original=%r translated=%r adapted=%r final=%r "
            "audio=%s fitted=%s",
            task_id or "?",
            idx,
            lang,
            target_lang,
            original[:120],
            translated[:120],
            adapted[:120],
            final[:120],
            seg.get("file") or seg.get("tts_file_path"),
            seg.get("fitted_file"),
        )


def validate_segments_target_language(
    segments_data: list[dict[str, Any]],
    *,
    source_segments: list[str] | None = None,
    target_lang: str,
) -> list[dict[str, Any]]:
    """Return critical mismatch issues (stop assembly when non-empty)."""
    issues: list[dict[str, Any]] = []
    src_rows = list(source_segments or [])
    for idx, seg in enumerate(segments_data):
        if seg.get("merged_into") is not None:
            continue
        final = str(seg.get("text") or seg.get("plain_text") or "").strip()
        if not final:
            continue
        original = src_rows[idx] if idx < len(src_rows) else ""
        bad, code = is_critical_language_mismatch(
            final, target_lang=target_lang, original=original
        )
        if bad:
            issues.append(
                {
                    "index": idx,
                    "segment_id": seg.get("segment_id"),
                    "code": code,
                    "detected_lang": detect_segment_language(final, target_lang=target_lang),
                    "target_lang": target_lang,
                    "final_preview": final[:200],
                }
            )
    return issues


def build_language_mismatch_report(
    *,
    index: int,
    segment: dict[str, Any],
    audit: dict[str, Any] | None,
    original: str,
    target_lang: str,
) -> dict[str, Any]:
    """Detailed RCA report for a LANGUAGE_MISMATCH segment (TZ §4, §8).

    Walks the per-stage transformation chain and detects the FIRST stage where
    the target language is lost, so the failure is never just 'LANGUAGE_MISMATCH'
    without an explanation.
    """
    a = audit or {}
    # (stage_label, text, source_function, source_file)
    raw_stages = [
        ("original", original, "whisper_stt", "engines/whisper_*"),
        (
            "raw_mt",
            a.get("raw_translation") or a.get("raw_mt"),
            "translate_with_manager",
            "engines/translation_manager.py",
        ),
        (
            "naturalizer",
            a.get("naturalized_text"),
            "apply_naturalizer",
            "engines/naturalizer_v2/*",
        ),
        (
            "semantic_rewrite",
            a.get("semantic_text"),
            "apply_semantic_polish_lines",
            "engines/semantic_translation.py",
        ),
        (
            "final_translation",
            a.get("final_text")
            or segment.get("translation_text")
            or segment.get("plain_text")
            or segment.get("text"),
            "_translate_segments_body",
            "engines/translation_pipeline.py",
        ),
        (
            "tts_input",
            a.get("tts_text") or segment.get("tts_text"),
            "synthesize_segment",
            "engines/dubbing_engine/*",
        ),
    ]

    base = _base_lang(target_lang)
    chain: list[dict[str, Any]] = []
    first_non_target: dict[str, Any] | None = None
    prev_stage: dict[str, Any] | None = None
    for label, text, fn, src_file in raw_stages:
        t = str(text or "").strip()
        if not t:
            continue
        lang = detect_segment_language(t, target_lang=target_lang)
        bad, code = is_critical_language_mismatch(
            t, target_lang=target_lang, original=original
        )
        is_target = (base in ("uk", "ru", "be") and lang == base) or (
            not bad and label != "original"
        )
        entry = {
            "stage": label,
            "text_preview": t[:200],
            "lang": lang,
            "is_target_language": bool(is_target and not bad),
            "mismatch_code": code if bad else "",
            "source_function": fn,
            "source_file": src_file,
        }
        chain.append(entry)
        # original is expected to be English; the first POST-translation stage
        # that is still non-target is where the leak originates.
        if label != "original" and bad and first_non_target is None:
            first_non_target = {
                "stage": label,
                "previous_stage": (prev_stage or {}).get("stage", "original"),
                "text_before": (prev_stage or {}).get("text_preview", original[:200]),
                "text_after": t[:200],
                "lang": lang,
                "mismatch_code": code,
                "source_function": fn,
                "source_file": src_file,
            }
        prev_stage = entry

    if first_non_target is None:
        diagnosis = (
            "Target language lost before any stage produced target-language text "
            "(machine translation likely returned the source unchanged)."
        )
    else:
        diagnosis = (
            f"English text first appears at stage '{first_non_target['stage']}' "
            f"(after '{first_non_target['previous_stage']}'), produced by "
            f"{first_non_target['source_function']} ({first_non_target['source_file']}). "
            f"Reason code: {first_non_target['mismatch_code']}."
        )

    return {
        "index": index,
        "segment_id": segment.get("segment_id"),
        "target_lang": target_lang,
        "original_preview": str(original or "")[:200],
        "transformation_chain": chain,
        "first_non_target_stage": first_non_target,
        "diagnosis": diagnosis,
    }
