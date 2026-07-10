"""Per-segment translation validation, diagnostics, and auto-recovery."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from engines.mt.lang_codes import normalize_lang
from engines.pipeline_language_gate import (
    detect_segment_language,
    is_critical_language_mismatch,
)

logger = logging.getLogger("tubedub.translation_validation")

MAX_TRANSLATION_RETRIES = 3
_APP_DIR = Path(__file__).resolve().parent.parent


def ensure_segment_has_translation(
    seg: dict[str, Any],
    *,
    source_lang: str,
    target_lang: str,
    registry=None,
    task_id: str = "",
    segment_idx: int = 0,
) -> tuple[bool, str]:
    """Guarantee translated_text exists or record fallback reason in OpenDDF."""
    from engines.dub_quality_stabilization import guarantee_translation_completeness

    fixed, rows = guarantee_translation_completeness(
        [seg],
        source_lang=source_lang,
        target_lang=target_lang,
        registry=registry,
        task_id=task_id,
    )
    row = rows[0] if rows else {}
    ok = row.get("status") in ("ok", "recovered", "same_language_passthrough", "skipped_empty_source")
    reason = str(row.get("fallback_reason") or row.get("status") or "")
    if not ok and task_id:
        try:
            from engines.open_ddf import open_ddf

            open_ddf.record_agent(
                task_id,
                "TranslationCompleteness",
                called=True,
                success=False,
                error=reason or "translation_incomplete",
                fallback_used=True,
                segment_idx=segment_idx,
            )
        except Exception:
            pass
    return ok, reason


def resolve_post_quality_text(seg: dict[str, Any]) -> str:
    """Canonical post-pipeline text for TTS (never the STT source field)."""
    return str(
        seg.get("voice_input")
        or seg.get("final_text")
        or seg.get("quality_fallback_text")
        or seg.get("grammar_text")
        or seg.get("timing_text")
        or seg.get("semantic_text")
        or seg.get("translated_text")
        or seg.get("translation_text")
        or ""
    ).strip()


resolve_final_text = resolve_post_quality_text


def resolve_voice_input(seg: dict[str, Any], audit: dict[str, Any] | None = None) -> str:
    """Voice/TTS input — MUST NOT read original STT `text` when post-quality fields exist."""
    a = audit or {}
    return str(
        resolve_post_quality_text(seg)
        or a.get("final_text")
        or a.get("tts_text")
        or ""
    ).strip()


def validate_segment_for_target(
    text: str,
    *,
    target_lang: str,
    original: str = "",
) -> dict[str, Any]:
    bad, code = is_critical_language_mismatch(
        text, target_lang=target_lang, original=original
    )
    detected = detect_segment_language(text, target_lang=target_lang)
    stripped = str(text or "").strip()
    if not stripped:
        return {
            "pass": False,
            "fail": True,
            "reason": "empty_text",
            "detected_language": "empty",
            "target_language": normalize_lang(target_lang),
        }
    return {
        "pass": not bad,
        "fail": bad,
        "reason": code if bad else "",
        "detected_language": detected,
        "target_language": normalize_lang(target_lang),
    }


def detect_effective_source_lang(segments: list[dict], manifest_source: str) -> str:
    """Infer source language from segment text when manifest detection is wrong."""
    manifest_src = normalize_lang(manifest_source or "en")
    if not segments:
        return manifest_src

    votes: dict[str, int] = {}
    for seg in segments[: min(8, len(segments))]:
        src_text = str(seg.get("text") or seg.get("original_text") or "").strip()
        if not src_text:
            continue
        detected = detect_segment_language(src_text, target_lang="")
        if detected in ("en", "ru", "uk", "pt", "de", "fr", "es", "it"):
            votes[detected] = votes.get(detected, 0) + 1

    if not votes:
        return manifest_src

    best = max(votes, key=lambda k: votes[k])
    if best != manifest_src and votes[best] >= max(2, len(votes)):
        logger.warning(
            "Source lang corrected manifest=%s -> detected=%s votes=%s",
            manifest_src,
            best,
            votes,
        )
        return best
    return manifest_src


def build_validation_row(
    idx: int,
    seg: dict[str, Any],
    *,
    audit: dict[str, Any] | None,
    original_text: str,
    target_lang: str,
    source_lang: str = "",
    attempts: list[dict] | None = None,
) -> dict[str, Any]:
    a = audit or {}
    voice_input = resolve_voice_input(seg, a)
    final_text = resolve_post_quality_text(seg)
    translated = str(
        seg.get("translated_text")
        or seg.get("translation_text")
        or a.get("raw_translation")
        or ""
    ).strip()
    validation = validate_segment_for_target(
        voice_input or final_text,
        target_lang=target_lang,
        original=original_text,
    )
    return {
        "index": idx,
        "segment_id": seg.get("segment_id"),
        "original_language": source_lang or detect_segment_language(original_text),
        "detected_language": validation["detected_language"],
        "target_language": normalize_lang(target_lang),
        "original_text": original_text[:500],
        "translated_text": translated[:500],
        "semantic_output": str(seg.get("semantic_text") or a.get("semantic_text") or "")[:500],
        "grammar_output": str(seg.get("grammar_text") or "")[:500],
        "final_text": final_text[:500],
        "voice_input": voice_input[:500],
        "tts_language": normalize_lang(target_lang),
        "validation_result": {
            "pass": validation["pass"],
            "fail": validation["fail"],
            "reason": validation["reason"],
        },
        "attempts": list(attempts or []),
    }


def sync_final_text_to_state(info: dict[str, Any]) -> None:
    """Push post-quality segment text into translation_audits.final_text / tts_text."""
    segments_data = info.get("segments_data") or []
    audits = list(info.get("translation_audits") or [])
    audit_by = {int(a.get("index", -1)): a for a in audits}

    for i, seg in enumerate(segments_data):
        final = resolve_post_quality_text(seg)
        if not final:
            continue
        row = audit_by.get(i)
        if row is None:
            row = {"index": i}
            audits.append(row)
            audit_by[i] = row
        row["final_text"] = final
        row["tts_text"] = final
        seg["final_text"] = final
        seg["voice_input"] = final
        if seg.get("grammar_text"):
            row["grammar_text"] = seg["grammar_text"]
        if seg.get("semantic_text"):
            row["semantic_text"] = seg["semantic_text"]
        if seg.get("translated_text") and not row.get("raw_translation"):
            row["raw_translation"] = seg["translated_text"]

    info["segments_data"] = segments_data
    info["translation_audits"] = audits


sync_final_text_to_task_info = sync_final_text_to_state


def write_translation_validation_json(
    task_id: str,
    rows: list[dict[str, Any]],
    *,
    project_uuid: str = "",
    app_dir: Path | None = None,
) -> list[Path]:
    """Persist translation_validation.json under diagnostics/ and manifests/."""
    base = app_dir or _APP_DIR
    payload = {
        "task_id": task_id,
        "project_uuid": project_uuid,
        "segment_count": len(rows),
        "failed_count": sum(
            1 for r in rows if (r.get("validation_result") or {}).get("fail")
        ),
        "segments": rows,
    }
    written: list[Path] = []

    diag_dir = base / "output" / "diagnostics" / task_id
    diag_dir.mkdir(parents=True, exist_ok=True)
    diag_path = diag_dir / "translation_validation.json"
    with open(diag_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    written.append(diag_path)

    if project_uuid:
        man_dir = base / "output" / "manifests" / project_uuid
        man_dir.mkdir(parents=True, exist_ok=True)
        man_path = man_dir / "translation_validation.json"
        with open(man_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        written.append(man_path)

    try:
        from engines.open_ddf import open_ddf

        open_ddf.save(task_id)
    except Exception:
        pass

    return written


def build_validation_rows_from_info(
    info: dict[str, Any],
    *,
    target_lang: str | None = None,
) -> list[dict]:
    segments_data = info.get("segments_data") or []
    source_segments = info.get("source_segments") or []
    audits = info.get("translation_audits") or []
    audit_by = {int(a.get("index", -1)): a for a in audits}
    src = normalize_lang(info.get("source_lang") or "en")
    tgt = normalize_lang(target_lang or info.get("target_lang") or "ru")
    rows: list[dict] = []
    for i, seg in enumerate(segments_data):
        original = (
            source_segments[i]
            if i < len(source_segments)
            else str(seg.get("original_text") or "")
        )
        rows.append(
            build_validation_row(
                i,
                seg,
                audit=audit_by.get(i),
                original_text=str(original or ""),
                target_lang=tgt,
                source_lang=src,
            )
        )
    return rows


def retry_segment_translation(
    text: str,
    *,
    source_lang: str,
    target_lang: str,
    registry=None,
    max_retries: int = MAX_TRANSLATION_RETRIES,
) -> tuple[str, list[dict]]:
    """Retry translation with fallback chain; never return unchanged source."""
    from engines.ai_core.translation_agent.retry_policy import translate_with_fallback
    from engines.ai_core.translation_agent.translator_interface import TranslatorRegistry

    if registry is None:
        registry = TranslatorRegistry({})

    src = str(text or "").strip()
    attempts: list[dict] = []
    if not src:
        return "", attempts

    src_n = normalize_lang(source_lang)
    tgt_n = normalize_lang(target_lang)
    if src_n == tgt_n:
        return src, attempts

    for attempt in range(1, max_retries + 1):
        result = translate_with_fallback(
            src,
            src_n,
            tgt_n,
            registry,
            max_retries=1,
        )
        translated = str(result.translated or "").strip()
        bad, code = is_critical_language_mismatch(
            translated, target_lang=tgt_n, original=src
        )
        attempts.append(
            {
                "attempt": attempt,
                "translator": result.translator_name,
                "success": bool(translated) and not bad,
                "confidence": result.confidence,
                "language_mismatch": code if bad else "",
                "text_preview": translated[:200],
                "error": result.error,
            }
        )
        if translated and not bad:
            return translated, attempts

    try:
        from engines.ai_core.translation_agent.translators.deep_translator import (
            DeepTranslatorWrapper,
        )

        dt = DeepTranslatorWrapper()
        if dt.is_available():
            translated = str(dt.translate(src, src_n, tgt_n) or "").strip()
            bad, code = is_critical_language_mismatch(
                translated, target_lang=tgt_n, original=src
            )
            attempts.append(
                {
                    "attempt": max_retries + 1,
                    "translator": "deep-translator-direct",
                    "success": bool(translated) and not bad,
                    "language_mismatch": code if bad else "",
                    "text_preview": translated[:200],
                }
            )
            if translated and not bad:
                return translated, attempts
    except Exception as exc:
        attempts.append({"attempt": max_retries + 1, "error": str(exc)})

    return "", attempts


def apply_translated_text_to_segment(seg: dict[str, Any], translated: str) -> None:
    text = str(translated or "").strip()
    seg["translated_text"] = text
    seg["translation_text"] = text
    seg["semantic_text"] = text
    seg["timing_text"] = text
    seg["grammar_text"] = text
    seg["text_for_tts"] = text
    seg["text"] = text
    seg["plain_text"] = text
    seg["final_text"] = text
    seg["voice_input"] = text


def recover_mismatched_segments(
    info: dict[str, Any],
    issues: list[dict[str, Any]] | None = None,
    *,
    target_lang: str = "",
    source_lang: str = "",
    task_id: str = "",
    app_dir: Path | None = None,
) -> tuple[int, list[dict[str, Any]]]:
    """Auto-recover LANGUAGE_MISMATCH segments; return (fixed_count, remaining_issues)."""
    from engines.ai_core.translation_agent.translator_interface import TranslatorRegistry

    segments_data = list(info.get("segments_data") or [])
    source_segments = list(info.get("source_segments") or [])
    audits = list(info.get("translation_audits") or [])
    audit_by = {int(a.get("index", -1)): a for a in audits}

    tgt = normalize_lang(target_lang or info.get("target_lang") or "ru")
    src = normalize_lang(source_lang or info.get("source_lang") or "en")
    if src == tgt:
        src = detect_effective_source_lang(
            [{"text": s} for s in source_segments],
            src,
        )

    issue_indices = {
        int(i.get("index", -1))
        for i in (issues or [])
        if int(i.get("index", -1)) >= 0
    }
    if not issue_indices:
        for i, seg in enumerate(segments_data):
            original = (
                source_segments[i]
                if i < len(source_segments)
                else str(seg.get("original_text") or "")
            )
            voice_input = resolve_voice_input(seg, audit_by.get(i))
            v = validate_segment_for_target(
                voice_input, target_lang=tgt, original=original
            )
            if v["fail"]:
                issue_indices.add(i)

    registry = TranslatorRegistry(info.get("capability_matrix") or {})
    fixed = 0
    rows: list[dict] = []

    for i, seg in enumerate(segments_data):
        original = (
            source_segments[i]
            if i < len(source_segments)
            else str(seg.get("original_text") or "")
        )
        audit = audit_by.get(i, {})
        voice_input = resolve_voice_input(seg, audit)
        before = validate_segment_for_target(
            voice_input, target_lang=tgt, original=original
        )

        if i not in issue_indices and not before["fail"]:
            rows.append(
                build_validation_row(
                    i,
                    seg,
                    audit=audit,
                    original_text=str(original or ""),
                    target_lang=tgt,
                    source_lang=src,
                )
            )
            continue

        translated, attempts = retry_segment_translation(
            str(original or ""),
            source_lang=src,
            target_lang=tgt,
            registry=registry,
        )

        if translated:
            apply_translated_text_to_segment(seg, translated)
            if not audit:
                audit = {"index": i}
                audits.append(audit)
                audit_by[i] = audit
            audit["raw_translation"] = translated
            audit["final_text"] = translated
            audit["tts_text"] = translated
            fixed += 1
            after = validate_segment_for_target(
                translated, target_lang=tgt, original=original
            )
        else:
            after = before
            seg["translation_error"] = (
                f"LANGUAGE_MISMATCH сегмент #{i}: ожидался {tgt}, "
                f"обнаружен {before['detected_language']} ({before['reason']})"
            )

        row = build_validation_row(
            i,
            seg,
            audit=audit,
            original_text=str(original or ""),
            target_lang=tgt,
            source_lang=src,
            attempts=attempts,
        )
        row["validation_result"] = {
            "pass": after["pass"],
            "fail": after["fail"],
            "reason": after["reason"] or before["reason"],
        }
        rows.append(row)

    info["segments_data"] = segments_data
    info["translation_audits"] = audits
    info["translation_validation"] = rows

    if task_id:
        write_translation_validation_json(
            task_id,
            rows,
            project_uuid=str(info.get("project_uuid") or ""),
            app_dir=app_dir,
        )

    still_bad: list[dict[str, Any]] = []
    for i, seg in enumerate(segments_data):
        original = (
            source_segments[i]
            if i < len(source_segments)
            else str(seg.get("original_text") or "")
        )
        voice_input = resolve_voice_input(seg, audit_by.get(i))
        v = validate_segment_for_target(
            voice_input, target_lang=tgt, original=original
        )
        if v["fail"]:
            still_bad.append(
                {
                    "index": i,
                    "segment_id": seg.get("segment_id"),
                    "code": v["reason"] or "english_in_uk_track",
                    "detected_lang": v["detected_language"],
                    "target_lang": tgt,
                    "final_preview": voice_input[:200],
                    "message": (
                        f"ожидался {tgt}, обнаружен {v['detected_language']}"
                    ),
                }
            )

    return fixed, still_bad


def ensure_segment_translation(
    seg: dict[str, Any],
    *,
    source_lang: str,
    target_lang: str,
    original_text: str = "",
    registry=None,
) -> tuple[str, str, list[dict]]:
    """Guarantee non-empty translation or recorded fallback reason."""
    src_n = normalize_lang(source_lang)
    tgt_n = normalize_lang(target_lang)
    original = str(original_text or seg.get("text") or "").strip()
    existing = str(seg.get("translated_text") or seg.get("translation_text") or "").strip()

    if existing:
        v = validate_segment_for_target(existing, target_lang=tgt_n, original=original)
        if v["pass"]:
            return existing, "translated", []

    if not original:
        seg["translation_fallback_reason"] = "empty_source"
        return "", "failed", []

    if src_n == tgt_n:
        seg["translation_fallback_reason"] = "same_language"
        return original, "fallback", []

    translated, attempts = retry_segment_translation(
        original,
        source_lang=src_n,
        target_lang=tgt_n,
        registry=registry,
    )
    if translated:
        apply_translated_text_to_segment(seg, translated)
        seg.pop("translation_fallback_reason", None)
        seg["translation_attempts"] = len(attempts)
        return translated, "translated", attempts

    # Last-resort: mark reason, never leave silently empty
    reason = "all_translators_failed"
    if attempts:
        last = attempts[-1]
        reason = str(last.get("language_mismatch") or last.get("error") or reason)
    seg["translation_fallback_reason"] = reason
    seg["translation_attempts"] = attempts
    return "", "failed", attempts


def ensure_all_segments_translated(
    segments: list[dict[str, Any]],
    *,
    source_lang: str,
    target_lang: str,
    registry=None,
) -> dict[str, int]:
    """Pass over all segments; return stats {translated, fallback, failed}."""
    stats = {"translated": 0, "fallback": 0, "failed": 0, "retried": 0}
    for seg in segments:
        text, status, attempts = ensure_segment_translation(
            seg,
            source_lang=source_lang,
            target_lang=target_lang,
            original_text=str(seg.get("text") or ""),
            registry=registry,
        )
        stats[status] = stats.get(status, 0) + 1
        if attempts:
            stats["retried"] += len(attempts)
        if status == "failed" and not text:
            # Annotate for OpenDDF — segment still has reason, not blank silently
            seg.setdefault(
                "translation_error",
                seg.get("translation_fallback_reason") or "translation_failed",
            )
    return stats
