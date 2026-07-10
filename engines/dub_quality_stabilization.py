"""AI Dub Quality Stabilization v1.0 — shared validators and OpenDDF metrics."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from engines.mt.lang_codes import normalize_lang
from engines.pipeline_language_gate import detect_segment_language, is_critical_language_mismatch
from engines.semantic_meaning import is_truncated_adaptation, word_count
from engines.sentence_integrity import enforce_tts_integrity, validate_tts_text
from engines.translation_validation import resolve_post_quality_text

from engines.reviewer_scores import (
    compute_slot_fit_for_segment,
    grammar_score_for_segment,
)

logger = logging.getLogger("tubedub.dub_quality_stabilization")

GOLDEN_VIDEO_PATH = "uploads/video_e00b875b63.mp4"
GOLDEN_VIDEO_ALIASES = (
    GOLDEN_VIDEO_PATH,
    "video_e00b875b63.mp4",
    "output/uploads/video_e00b875b63.mp4",
)

MAX_REVIEWER_RETRIES = 3
SLOT_FIT_PASS_DEFAULT = 0.85
GRAMMAR_SCORE_PASS_DEFAULT = 0.85
_TERMINAL_RE = re.compile(r"[.!?…]['\"»)\]】」』]*\s*$")


def is_sentence_complete(text: str, *, min_chars: int = 12, min_words: int = 4) -> tuple[bool, list[str]]:
    """True when text is a complete spoken sentence (not a dangling fragment)."""
    ok, issues = validate_tts_text(text, min_chars=min_chars)
    if ok:
        return True, []
    truncated = [c for c in issues if c in ("incomplete_sentence", "dangling_connector", "mid_word")]
    return not truncated, issues


def is_truncated_sentence(original: str, candidate: str) -> bool:
    """Detect tail clip relative to reference text."""
    return is_truncated_adaptation(str(original or ""), str(candidate or ""))


def detect_semantic_compression(original: str, candidate: str) -> bool:
    """Heuristic: meaningful shortening without truncation."""
    orig_w = word_count(original)
    cand_w = word_count(candidate)
    if orig_w < 6 or cand_w >= orig_w:
        return False
    if is_truncated_adaptation(original, candidate):
        return False
    return cand_w <= int(orig_w * 0.82)


def basic_meaning_preserved(source: str, candidate: str, *, min_overlap: float = 0.15) -> bool:
    """Lightweight overlap heuristic when LLM meaning check unavailable."""
    def _tokens(text: str) -> set[str]:
        return {
            t.lower()
            for t in re.findall(r"[\w']+", str(text or ""), flags=re.UNICODE)
            if len(t) > 2
        }

    src = _tokens(source)
    cand = _tokens(candidate)
    if not src or not cand:
        return bool(str(candidate or "").strip())
    overlap = len(src & cand) / max(len(src), 1)
    return overlap >= min_overlap or len(cand) >= max(3, int(len(src) * 0.35))


def audit_segment_for_reviewer(
    seg: dict[str, Any],
    *,
    source_lang: str,
    target_lang: str,
    slot_ms: int | None = None,
    voice_duration_ms: int | None = None,
    slot_fit_threshold: float = SLOT_FIT_PASS_DEFAULT,
    grammar_threshold: float = GRAMMAR_SCORE_PASS_DEFAULT,
) -> dict[str, Any]:
    """Pre-voice reviewer gate — read-only audit with routing hints."""
    idx = int(seg.get("index", 0))
    source = str(seg.get("text") or seg.get("original_text") or "").strip()
    translated = str(seg.get("translated_text") or "").strip()
    final = resolve_post_quality_text(seg)
    issues: list[str] = []
    route_to = ""
    slot_fit_score = 1.0
    grammar_score = 1.0

    if not final:
        issues.append("empty_text")
        route_to = "translation"
    elif not translated and source and normalize_lang(source_lang) != normalize_lang(target_lang):
        issues.append("missing_translation")
        route_to = route_to or "translation"

    bad, code = is_critical_language_mismatch(final, target_lang=target_lang, original=source)
    if bad:
        issues.append(code or "language_mismatch")
        route_to = route_to or "translation"

    complete, complete_issues = is_sentence_complete(final)
    if not complete:
        issues.append("incomplete_sentence")
        if "dangling_connector" in complete_issues or "incomplete_sentence" in complete_issues:
            route_to = route_to or "timing"
        if "mid_word" in complete_issues:
            route_to = route_to or "grammar"

    ref = str(seg.get("grammar_text") or seg.get("timing_text") or seg.get("semantic_text") or translated)
    if ref and is_truncated_sentence(ref, final):
        issues.append("truncated_sentence")
        route_to = route_to or "timing"

    if source and final and not basic_meaning_preserved(source, final):
        issues.append("meaning_loss")
        route_to = route_to or "semantic"

    if detect_semantic_compression(ref or source, final):
        issues.append("semantic_compression")
        if not route_to:
            route_to = "semantic"

    try:
        tgt = normalize_lang(target_lang)
        slot_fit_score = compute_slot_fit_for_segment(seg, tgt_lang=tgt)
        grammar_score = grammar_score_for_segment(seg)
    except Exception:
        pass

    if slot_fit_score < slot_fit_threshold:
        issues.append("slot_fit_low")
        route_to = route_to or "timing"

    if grammar_score < grammar_threshold:
        issues.append("grammar_score_low")
        route_to = route_to or "grammar"

    duration_ok = True
    if slot_ms and voice_duration_ms and voice_duration_ms > int(slot_ms * 1.25):
        issues.append("duration_overflow")
        route_to = route_to or "timing"
        duration_ok = False

    return {
        "index": idx,
        "segment_id": seg.get("segment_id"),
        "pass": not issues,
        "issues": issues,
        "route_to": route_to,
        "final_text_preview": final[:200],
        "duration_ok": duration_ok,
        "slot_fit_score": round(slot_fit_score, 4),
        "grammar_score": round(grammar_score, 4),
        "slot_fit_threshold": slot_fit_threshold,
        "grammar_threshold": grammar_threshold,
    }


def apply_reviewer_repairs(
    seg: dict[str, Any],
    audit: dict[str, Any],
    *,
    source_lang: str,
    target_lang: str,
    registry=None,
) -> tuple[bool, list[str]]:
    """Try inline repair (truncation/empty) before routing to upstream agents."""
    actions: list[str] = []
    issues = list(audit.get("issues") or [])

    if "empty_text" in issues or "missing_translation" in issues:
        from engines.translation_validation import retry_segment_translation

        source = str(seg.get("text") or "").strip()
        translated, attempts = retry_segment_translation(
            source,
            source_lang=source_lang,
            target_lang=target_lang,
            registry=registry,
        )
        seg["translation_retry_attempts"] = attempts
        if translated:
            from engines.translation_validation import apply_translated_text_to_segment

            apply_translated_text_to_segment(seg, translated)
            seg["translation_fallback_reason"] = "reviewer_retry"
            actions.append("translation_retry_ok")
        else:
            return False, ["translation_retry_failed"]

    ref = str(
        seg.get("grammar_text")
        or seg.get("timing_text")
        or seg.get("semantic_text")
        or seg.get("translated_text")
        or ""
    ).strip()
    source = str(seg.get("text") or "").strip()
    decision = enforce_tts_integrity(
        resolve_post_quality_text(seg),
        fallbacks=[ref, str(seg.get("translated_text") or ""), source],
        source=source,
    )
    repaired = str(decision.get("text") or "").strip()
    if repaired and repaired != resolve_post_quality_text(seg):
        seg["final_text"] = repaired
        seg["voice_input"] = repaired
        seg["grammar_text"] = repaired
        actions.append(f"integrity_fallback:{decision.get('reason', 'ok')}")

    return True, actions


def guarantee_translation_completeness(
    segments: list[dict[str, Any]],
    *,
    source_lang: str,
    target_lang: str,
    registry=None,
    task_id: str = "",
) -> tuple[int, list[dict[str, Any]]]:
    """Ensure every segment has translated text or recorded fallback reason."""
    from engines.ai_core.translation_agent.translator_interface import TranslatorRegistry
    from engines.translation_validation import (
        apply_translated_text_to_segment,
        retry_segment_translation,
    )

    if registry is None:
        registry = TranslatorRegistry({})

    src = normalize_lang(source_lang)
    tgt = normalize_lang(target_lang)
    fixed = 0
    rows: list[dict[str, Any]] = []

    for i, seg in enumerate(segments):
        original = str(seg.get("text") or "").strip()
        translated = str(seg.get("translated_text") or "").strip()
        row: dict[str, Any] = {
            "index": i,
            "segment_id": seg.get("segment_id"),
            "original_preview": original[:120],
            "status": "ok",
            "fallback_reason": "",
            "retry_count": int(seg.get("translation_attempts") or 0),
            "translator": seg.get("translator_used"),
        }

        if not original:
            row["status"] = "skipped_empty_source"
            rows.append(row)
            continue

        if src == tgt:
            if not translated:
                seg["translated_text"] = original
            row["status"] = "same_language_passthrough"
            rows.append(row)
            continue

        if translated:
            bad, code = is_critical_language_mismatch(translated, target_lang=tgt, original=original)
            if not bad:
                rows.append(row)
                continue
            row["fallback_reason"] = code or "language_mismatch"

        text, attempts = retry_segment_translation(
            original,
            source_lang=src,
            target_lang=tgt,
            registry=registry,
        )
        row["retry_count"] = len(attempts)
        row["attempts"] = attempts

        if text:
            apply_translated_text_to_segment(seg, text)
            seg["translation_completeness"] = "translated"
            row["status"] = "recovered"
            fixed += 1
        else:
            seg["translation_completeness"] = "fallback_failed"
            seg["translation_fallback_reason"] = row["fallback_reason"] or "all_translators_failed"
            row["status"] = "failed"
            try:
                from engines.open_ddf import open_ddf

                open_ddf.record_agent(
                    task_id,
                    "TranslationCompleteness",
                    called=True,
                    success=False,
                    error=seg["translation_fallback_reason"],
                    fallback_used=True,
                    segment_idx=i,
                    retry_count=len(attempts),
                )
            except Exception:
                pass

        rows.append(row)

    return fixed, rows


def compute_mix_quality_heuristic(
    *,
    separation_success: bool,
    used_stem_mix: bool,
    music_detected: bool,
    fallback_used: bool,
) -> float:
    score = 0.0
    if separation_success:
        score += 0.35
    if used_stem_mix:
        score += 0.35
    if music_detected:
        score += 0.25
    if not fallback_used:
        score += 0.05
    return round(min(1.0, score), 3)


def build_dub_quality_report(
    info: dict[str, Any],
    *,
    task_id: str = "",
    segments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Aggregate stabilization metrics for dub_quality_report.json."""
    segs = segments or info.get("segments_data") or info.get("segments") or []
    target_lang = normalize_lang(info.get("target_lang") or "uk")
    source_lang = normalize_lang(info.get("source_lang") or info.get("detected_lang") or "en")

    empty_segments = 0
    truncated_sentences = 0
    semantic_compression = 0
    per_segment: list[dict[str, Any]] = []

    for i, seg in enumerate(segs):
        if seg.get("merged_into") is not None:
            continue
        final = resolve_post_quality_text(seg)
        ref = str(
            seg.get("grammar_text")
            or seg.get("timing_text")
            or seg.get("semantic_text")
            or seg.get("translated_text")
            or ""
        )
        source = str(seg.get("text") or seg.get("original_text") or "")

        if not final.strip():
            empty_segments += 1

        truncated = bool(ref and is_truncated_sentence(ref, final))
        if truncated:
            truncated_sentences += 1

        compressed = detect_semantic_compression(ref or source, final)
        if compressed:
            semantic_compression += 1

        slot_ms = None
        start = seg.get("start")
        end = seg.get("end")
        if start is not None and end is not None:
            try:
                slot_ms = int(float(end) * 1000 - float(start) * 1000)
            except (TypeError, ValueError):
                slot_ms = None

        voice_ms = seg.get("tts_duration_ms") or seg.get("voice_duration_ms")
        try:
            voice_ms = int(voice_ms) if voice_ms is not None else None
        except (TypeError, ValueError):
            voice_ms = None

        per_segment.append(
            {
                "index": i,
                "segment_id": seg.get("segment_id"),
                "empty": not bool(final.strip()),
                "truncated": truncated,
                "semantic_compression": compressed,
                "retry_count": int(seg.get("reviewer_retry_count") or seg.get("translation_attempts") or 0),
                "final_voice_duration_ms": voice_ms,
                "slot_ms": slot_ms,
                "translation_status": seg.get("translation_completeness") or ("ok" if final else "empty"),
                "fallback_reason": seg.get("translation_fallback_reason") or "",
            }
        )

    sep = info.get("source_separation") or {}
    final_mix = info.get("final_mix") or {}
    separation_success = bool(sep.get("success"))
    used_stem_mix = bool(final_mix.get("used_stem_mix"))
    music_detected = bool(final_mix.get("music_detected_in_final"))
    fallback_used = bool(final_mix.get("fallback_used") or sep.get("fallback_used"))
    music_preserved = used_stem_mix and separation_success

    mix_quality = compute_mix_quality_heuristic(
        separation_success=separation_success,
        used_stem_mix=used_stem_mix,
        music_detected=music_detected,
        fallback_used=fallback_used,
    )

    reviewer_rows = info.get("reviewer_report") or {}
    return {
        "version": "1.0",
        "task_id": task_id or info.get("task_id") or "",
        "golden_video": GOLDEN_VIDEO_PATH,
        "summary": {
            "segment_count": len(per_segment),
            "empty_segments": empty_segments,
            "truncated_sentences": truncated_sentences,
            "semantic_compression": semantic_compression,
            "music_preserved": music_preserved,
            "mix_quality": mix_quality,
            "reviewer_failures": int(reviewer_rows.get("failed_count") or 0),
        },
        "music": {
            "separation_success": separation_success,
            "used_stem_mix": used_stem_mix,
            "music_detected_in_final": music_detected,
            "accompaniment_path": sep.get("accompaniment_path"),
            "fallback_used": fallback_used,
        },
        "per_segment": per_segment,
    }


def write_dub_quality_report_json(
    app_dir: Path,
    info: dict[str, Any],
    *,
    task_id: str = "",
    project_uuid: str = "",
) -> list[Path]:
    """Persist dub_quality_report.json under diagnostics/ and manifests/."""
    payload = build_dub_quality_report(info, task_id=task_id)
    written: list[Path] = []

    if not task_id:
        return written

    diag_dir = app_dir / "output" / "diagnostics" / task_id
    diag_dir.mkdir(parents=True, exist_ok=True)
    diag_path = diag_dir / "dub_quality_report.json"
    with open(diag_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    written.append(diag_path)

    if project_uuid:
        man_dir = app_dir / "output" / "manifests" / project_uuid
        man_dir.mkdir(parents=True, exist_ok=True)
        man_path = man_dir / "dub_quality_report.json"
        with open(man_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        written.append(man_path)

    tpl_dir = app_dir / "data" / "templates"
    tpl_dir.mkdir(parents=True, exist_ok=True)
    tpl_path = tpl_dir / "dub_quality_report.json"
    if not tpl_path.is_file():
        template = {
            "version": "1.0",
            "task_id": "",
            "golden_video": GOLDEN_VIDEO_PATH,
            "summary": {
                "segment_count": 0,
                "empty_segments": 0,
                "truncated_sentences": 0,
                "semantic_compression": 0,
                "music_preserved": False,
                "mix_quality": 0.0,
                "reviewer_failures": 0,
            },
            "music": {
                "separation_success": False,
                "used_stem_mix": False,
                "music_detected_in_final": False,
                "accompaniment_path": None,
                "fallback_used": True,
            },
            "per_segment": [],
        }
        with open(tpl_path, "w", encoding="utf-8") as fh:
            json.dump(template, fh, ensure_ascii=False, indent=2)

    try:
        from engines.open_ddf import open_ddf

        open_ddf.record_agent(
            task_id,
            "DubQualityReport/v1",
            called=True,
            success=True,
            decision="metrics_written",
            output_metrics=payload.get("summary") or {},
        )
        open_ddf.save(task_id)
    except Exception:
        pass

    return written
