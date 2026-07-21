"""Per-segment translation validation, diagnostics, and auto-recovery."""

from __future__ import annotations

import json
import logging
import re
import unicodedata
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


def normalize_text_for_ownership(text: str) -> str:
    """Compare translations without prosody accents / punctuation drift."""
    s = unicodedata.normalize("NFD", str(text or ""))
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = s.casefold()
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
    return " ".join(s.split())


def texts_equivalent_for_ownership(a: str, b: str) -> bool:
    na = normalize_text_for_ownership(a)
    nb = normalize_text_for_ownership(b)
    return bool(na) and na == nb


def _semantic_authority_text(seg: dict[str, Any], audit: dict[str, Any] | None = None) -> str:
    a = audit or {}
    return str(
        seg.get("semantic_engine_text")
        or seg.get("semantic_text")
        or a.get("semantic_engine_text")
        or a.get("semantic_text")
        or ""
    ).strip()


def prefer_semantic_authority(
    *,
    semantic: str,
    candidate: str,
    raw_mt: str = "",
    source: str = "",
) -> bool:
    """True when meaning-first semantic must displace a stale / short final or raw.

    Only reclaim ownership when the working text still mirrors Raw MT (including
    accent-only prosody drift) or is the known clause-restore glue of that Raw MT.
    Do not undo legitimate post-semantic compress/expand results.
    """
    from engines.semantic_meaning import should_prefer_semantic_over_raw_mt

    sem = str(semantic or "").strip()
    cur = str(candidate or "").strip()
    raw = str(raw_mt or "").strip()
    if not sem:
        return False
    if not cur:
        return True
    if texts_equivalent_for_ownership(sem, cur):
        return False
    if not raw:
        return False
    if texts_equivalent_for_ownership(cur, raw):
        return not texts_equivalent_for_ownership(sem, raw)
    if not should_prefer_semantic_over_raw_mt(
        semantic=sem,
        raw_mt=raw,
        source=source,
    ):
        return False
    # Detect DSAL clause-restore applied on top of a short Raw MT fragment.
    try:
        from engines.dsal.clause_coverage import restore_missing_clauses

        restored, cov = restore_missing_clauses(raw, source)
        if cov.restored_phrases and texts_equivalent_for_ownership(cur, restored):
            return True
    except Exception:
        pass
    return False


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


def resolve_post_quality_text(
    seg: dict[str, Any],
    audit: dict[str, Any] | None = None,
) -> str:
    """Canonical post-pipeline text for TTS (never the STT source field).

    One authoritative owner: if a fuller semantic polish exists while final/voice
    still mirrors a short Raw MT fragment (including accent-only prosody drift),
    semantic wins so Review / TTS / DSAL cannot diverge.
    """
    a = audit or {}
    candidate = str(
        seg.get("voice_input")
        or seg.get("final_text")
        or a.get("final_text")
        or seg.get("quality_fallback_text")
        or seg.get("grammar_text")
        or seg.get("timing_text")
        or seg.get("semantic_text")
        or a.get("semantic_text")
        or seg.get("translated_text")
        or seg.get("translation_text")
        or a.get("tts_text")
        or ""
    ).strip()
    semantic = _semantic_authority_text(seg, a)
    raw = str(
        a.get("raw_translation")
        or seg.get("raw_translation")
        or seg.get("translated_text")
        or ""
    ).strip()
    source = str(seg.get("original_text") or seg.get("source_text") or a.get("whisper_text") or "")
    if prefer_semantic_authority(
        semantic=semantic,
        candidate=candidate,
        raw_mt=raw,
        source=source,
    ):
        return semantic
    return candidate


resolve_final_text = resolve_post_quality_text


def resolve_voice_input(seg: dict[str, Any], audit: dict[str, Any] | None = None) -> str:
    """Voice/TTS input — MUST NOT read original STT `text` when post-quality fields exist."""
    a = audit or {}
    return str(
        resolve_post_quality_text(seg, a)
        or a.get("final_text")
        or a.get("tts_text")
        or ""
    ).strip()


def stamp_authoritative_final_text(
    seg: dict[str, Any],
    text: str,
    *,
    audit: dict[str, Any] | None = None,
    preserve_semantic_engine: bool = True,
) -> str:
    """Write one authoritative final into competing segment fields.

    Never overwrite an existing ``translated_text`` / ``raw_translation`` —
    those are the Raw MT anchors used by Review and meaning recovery.
    """
    final = str(text or "").strip()
    if not final:
        return ""
    try:
        from engines.stress_marks import strip_stress_marks

        final = strip_stress_marks(final)
    except Exception:
        pass
    existing_engine = str(seg.get("semantic_engine_text") or "").strip()
    raw_anchor = str(
        seg.get("translated_text")
        or (audit or {}).get("raw_translation")
        or ""
    ).strip()
    seg["final_text"] = final
    seg["voice_input"] = final
    seg["text_for_tts"] = final
    seg["plain_text"] = final
    seg["translation_text"] = final
    # Keep Raw MT immutable once present
    if not raw_anchor:
        seg["translated_text"] = final
    else:
        seg["translated_text"] = raw_anchor
    seg["text"] = final
    seg["grammar_text"] = final
    seg["timing_text"] = final
    seg["semantic_text"] = final
    if preserve_semantic_engine and existing_engine and prefer_semantic_authority(
        semantic=existing_engine,
        candidate=final,
        raw_mt=str((audit or {}).get("raw_translation") or raw_anchor or ""),
        source=str(seg.get("original_text") or ""),
    ):
        seg["semantic_engine_text"] = existing_engine
    else:
        seg["semantic_engine_text"] = existing_engine or final
    if audit is not None:
        audit["final_text"] = final
        audit["tts_text"] = final
        if not str(audit.get("raw_translation") or "").strip() and raw_anchor:
            audit["raw_translation"] = raw_anchor
        if not audit.get("semantic_text"):
            audit["semantic_text"] = existing_engine or final
        if not audit.get("semantic_engine_text"):
            audit["semantic_engine_text"] = existing_engine or final
    return final


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
    source_segments = list(info.get("source_segments") or [])

    for i, seg in enumerate(segments_data):
        row = audit_by.get(i)
        if row is None:
            row = {"index": i}
            audits.append(row)
            audit_by[i] = row
        if i < len(source_segments) and not seg.get("original_text"):
            seg["original_text"] = str(source_segments[i] or "")
        if i < len(source_segments) and not str(row.get("whisper_text") or "").strip():
            row["whisper_text"] = str(source_segments[i] or "")
        raw_anchor = str(
            seg.get("translated_text") or row.get("raw_translation") or ""
        ).strip()
        if raw_anchor and not str(row.get("raw_translation") or "").strip():
            row["raw_translation"] = raw_anchor
        final = resolve_post_quality_text(seg, row)
        if not final:
            continue
        stamp_authoritative_final_text(seg, final, audit=row, preserve_semantic_engine=True)
        if seg.get("grammar_text"):
            row["grammar_text"] = seg["grammar_text"]
        sem_engine = str(seg.get("semantic_engine_text") or "").strip()
        if sem_engine:
            row["semantic_text"] = row.get("semantic_text") or sem_engine
            row["semantic_engine_text"] = sem_engine
        elif seg.get("semantic_text"):
            row["semantic_text"] = seg["semantic_text"]
            row["semantic_engine_text"] = seg["semantic_text"]

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
    from engines.pipeline_integrity.translation_lock import (
        assert_text_field_writable,
        is_segment_locked,
    )

    if is_segment_locked(seg):
        # No silent fix after TRANSLATION LOCK — raise explicitly.
        assert_text_field_writable(
            seg, "translated_text", mutator="engines.translation_validation"
        )

    stamp_authoritative_final_text(seg, str(translated or "").strip())


def apply_dsal_before_lock(
    info: dict[str, Any],
    *,
    allow_llm: bool = True,
    block_merge: bool = True,
) -> dict[str, Any]:
    """TZ v4.0: Duration-Semantic Adaptation before TRANSLATION LOCK (rule-based)."""
    from engines.dsal import (
        adapt_duration_semantic,
        apply_semantic_block_merges,
        stamp_dsal_on_segment,
    )

    segments = list(info.get("segments_data") or [])
    timing_map = list(info.get("timing_map") or [])
    src_segs = list(info.get("source_segments") or info.get("original_segments") or [])
    tgt = str(info.get("target_lang") or info.get("tgt_lang") or "uk")
    adapted = 0
    yellow_red = 0

    for i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            continue
        if seg.get("translation_locked") or seg.get("merged_into") is not None:
            continue
        slot_ms = int(seg.get("slot_ms") or 0)
        if slot_ms <= 0 and i < len(timing_map):
            try:
                from engines.timing_fit import _parse_timing

                s, e = _parse_timing(timing_map[i])
                if e > s:
                    slot_ms = e - s
                    seg["slot_ms"] = slot_ms
            except Exception:
                pass
        if i < len(src_segs) and not seg.get("original_text"):
            seg["original_text"] = str(src_segs[i] or "")
        text = resolve_post_quality_text(seg).strip()
        if not text or slot_ms <= 0:
            continue
        src_hint = ""
        if i < len(src_segs):
            src_hint = str(src_segs[i] or "")
        if not src_hint:
            src_hint = str(seg.get("source_text") or seg.get("original_text") or "")
        # Re-stamp ownership before DSAL so clause restore cannot expand a stale raw fragment.
        stamp_authoritative_final_text(seg, text, preserve_semantic_engine=True)
        actual = int(seg.get("tts_ms") or seg.get("playback_duration") or 0) or None
        result = adapt_duration_semantic(
            text,
            source_hint=src_hint,
            slot_ms=slot_ms,
            tgt_lang=tgt,
            actual_tts_ms=actual,
            allow_llm=allow_llm,
        )
        stamp_dsal_on_segment(seg, result)
        if result.analysis.band in ("yellow", "red"):
            yellow_red += 1
        if result.changed and result.text.strip() and result.text.strip() != text:
            apply_translated_text_to_segment(seg, result.text.strip())
            adapted += 1
            logger.info(
                "DSAL pre-LOCK idx=%s band=%s detail=%s method=%s",
                i,
                result.analysis.band,
                result.detail,
                result.method,
            )

    # P1: semantic block merge for remaining yellow/red chains (2–3 segs)
    block_meta: dict[str, Any] = {}
    if block_merge:
        try:
            block_result = apply_semantic_block_merges(
                segments,
                source_segments=src_segs,
                tgt_lang=tgt,
            )
            block_meta = block_result.to_dict()
            if block_result.merged_blocks:
                adapted += block_result.adapted_segments
                logger.info(
                    "DSAL block-merge: blocks=%s adapted_segs=%s",
                    block_result.merged_blocks,
                    block_result.adapted_segments,
                )
        except Exception as block_exc:
            logger.warning("DSAL block-merge skipped: %s", block_exc)
            block_meta = {"error": str(block_exc)}
    else:
        block_meta = {"skipped": True}

    summary = {
        "dsal_pre_lock": True,
        "segments": len(segments),
        "yellow_red": yellow_red,
        "adapted": adapted,
        "block_merge": block_meta,
        "allow_llm": allow_llm,
    }
    info["dsal_pre_lock"] = summary
    return summary


def apply_translation_lock_after_validation(info: dict[str, Any]) -> dict[str, Any]:
    """
    Freeze TZ P0: after Translation Validation writes final text, apply LOCK.

    TZ v4.0: run DSAL (duration-semantic adapt) before LOCK.
    Advances pipeline state to LOCKED and stamps contract versions.
    Idempotent when already locked.
    """
    from engines.pipeline_integrity.pipeline_state import (
        PipelineState,
        advance_pipeline_state,
        get_pipeline_state,
    )
    from engines.pipeline_integrity.translation_lock import (
        is_project_locked,
        lock_segments,
    )

    segments = list(info.get("segments_data") or [])
    # Ensure every segment has a real slot_ms from timing before LOCK / Dub.
    timing_map = list(info.get("timing_map") or [])
    if timing_map:
        try:
            from engines.timing_fit import _parse_timing

            for i, seg in enumerate(segments):
                if not isinstance(seg, dict):
                    continue
                if int(seg.get("slot_ms") or 0) > 0:
                    continue
                if i >= len(timing_map):
                    break
                s, e = _parse_timing(timing_map[i])
                if e > s:
                    seg["slot_ms"] = max(1, e - s)
                    sid = str(seg.get("segment_id") or "").strip()
                    if sid and (seg.get("start_ms") is None or seg.get("end_ms") is None):
                        try:
                            from engines.scheduler import update_time

                            update_time([seg], sid, start_ms=s, end_ms=e, info=info)
                        except Exception:
                            pass
        except Exception as exc:
            logger.debug("slot_ms stamp before lock skipped: %s", exc)

    # TZ v4.0: Duration-Semantic Adaptation BEFORE lock (rule-based, LLM optional).
    # TPS Fast Path: skip DSAL text adapt — Timing/MeaningFit is single owner and
    # runs only for yellow/red after APPROVED (not on every segment).
    try:
        if not is_project_locked(info) and not info.get("skip_dsal_pre_lock"):
            apply_dsal_before_lock(info)
        elif info.get("skip_dsal_pre_lock"):
            # TPS: text rewrite skipped; duration-only stamp may already have run
            # after run_tps_pipeline. Re-stamp if approved texts exist and not stamped.
            try:
                if info.get("tps") and not info.get("tps_duration_stamp"):
                    from engines.tps.duration_stamp import stamp_duration_after_approved

                    stamp_duration_after_approved(
                        info, task_id=str(info.get("task_id") or "")
                    )
                else:
                    logger.info("DSAL pre-LOCK text adapt skipped (TPS duration-only)")
            except Exception as stamp_exc:
                logger.info(
                    "DSAL pre-LOCK skipped (TPS): %s", stamp_exc
                )
    except Exception as dsal_exc:
        logger.warning("DSAL pre-LOCK skipped: %s", dsal_exc)

    # TZ v4.0 P2: punctuation / name / USC polish before gate
    try:
        if not is_project_locked(info):
            from engines.dsal.pre_lock_polish import polish_segments_before_lock

            polish_segments_before_lock(info)
    except Exception as polish_exc:
        logger.warning("pre-LOCK polish skipped: %s", polish_exc)

    current = get_pipeline_state(info)

    # Ensure we are at least TRANSLATED before validation→lock.
    if current == PipelineState.NEW:
        advance_pipeline_state(info, PipelineState.TRANSCRIBED)
        advance_pipeline_state(info, PipelineState.TRANSLATED)
    elif current == PipelineState.TRANSCRIBED:
        advance_pipeline_state(info, PipelineState.TRANSLATED)

    if get_pipeline_state(info) == PipelineState.TRANSLATED:
        advance_pipeline_state(info, PipelineState.VALIDATED)

    if is_project_locked(info) and all(
        isinstance(s, dict) and s.get("translation_locked") for s in segments
    ):
        return dict(info.get("translation_lock") or {"pipeline_state": "LOCKED"})

    # TZ v4.0 P2: LOCK gate — duration_match ≥ 85, clause ≥ 0.85, entity pass
    try:
        from engines.dsal.lock_gate import apply_lock_with_gate

        meta = apply_lock_with_gate(segments, info=info, lock_segments_fn=lock_segments)
    except Exception as gate_exc:
        logger.warning("LOCK gate failed open: %s — locking anyway", gate_exc)
        meta = lock_segments(segments, info=info, advance_state=True)

    if meta.get("translation_lock_deferred"):
        logger.warning(
            "TRANSLATION_LOCK deferred: %s segs need Studio (gate fail)",
            meta.get("deferred_segments"),
        )
        return meta

    try:
        from engines.pipeline_integrity.uuid_chain import ensure_project_uuids

        uuid_meta = ensure_project_uuids(segments)
        meta["uuid_chain"] = uuid_meta
        info["uuid_chain"] = uuid_meta
    except Exception as uuid_exc:
        logger.debug("uuid_chain after lock skipped: %s", uuid_exc)
    logger.info(
        "TRANSLATION_LOCK applied: segments=%s state=%s contracts t=%s d=%s",
        meta.get("locked_segments"),
        meta.get("pipeline_state"),
        meta.get("translation_contract_version"),
        meta.get("dub_contract_version"),
    )
    return meta


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
