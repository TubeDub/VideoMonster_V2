"""AI Core 4.2 — per-segment handlers for streaming stages beyond grammar."""

from __future__ import annotations

import copy
import logging
from typing import Any

from engines.mt.lang_codes import normalize_lang

logger = logging.getLogger("tubedub.ai_core.streaming_handlers")

MAX_STREAM_RETRIES = 2


def process_quality_segment(
    list_index: int,
    seg: dict[str, Any],
    *,
    manifest: dict[str, Any],
    state: dict[str, Any],
    task_id: str,
) -> dict[str, Any]:
    """Lightweight quality audit — one segment, inline retry if needed."""
    from engines.ai_core.quality_agent.decision_engine import decide
    from engines.ai_core.quality_agent.segment_auditor import audit_segment
    from engines.ai_core.quality_agent.smart_router import route_and_fix_segment
    from engines.core.feature_flags import IS_DEBUG_LEARNING_MODE

    out = copy.deepcopy(seg)
    segments = state.get("segments") or []
    src = normalize_lang(manifest.get("source_lang") or "en")
    tgt = normalize_lang(manifest.get("target_lang") or "ru")

    for attempt in range(MAX_STREAM_RETRIES + 1):
        audit = audit_segment(
            out,
            all_segments=segments,
            source_lang=src,
            target_lang=tgt,
        )
        decision = decide(
            audit,
            retry_count=attempt,
            debug_mode=IS_DEBUG_LEARNING_MODE(),
        )
        out["quality_decision"] = decision.decision
        out["quality_passed"] = decision.decision in ("ACCEPT", "WARNING", "FALLBACK")
        out["quality_reasons"] = decision.reasons
        out["quality_scores"] = audit.scores.to_dict() if hasattr(audit.scores, "to_dict") else {}

        if decision.decision != "RETRY" or attempt >= MAX_STREAM_RETRIES:
            break

        failure_type = decision.failure_type or (
            audit.failure_types[0] if audit.failure_types else None
        )
        updated, _agent = route_and_fix_segment(
            out,
            failure_type,
            manifest,
            state,
            task_id,
            segment_index=list_index,
        )
        if updated:
            out = updated
            segments[list_index] = out
            state["segments"] = segments

    return out


def process_reviewer_segment(
    list_index: int,
    seg: dict[str, Any],
    *,
    manifest: dict[str, Any],
    state: dict[str, Any],
    task_id: str,
) -> dict[str, Any]:
    """Live reviewer gate — route single segment on failure, never block belt."""
    from engines.ai_core.reviewer_loop import route_failed_segment
    from engines.dub_quality_stabilization import audit_segment_for_reviewer
    from engines.reviewer_scores import enrich_segment_scores
    from engines.translation_validation import resolve_post_quality_text

    out = copy.deepcopy(seg)
    tgt = normalize_lang(manifest.get("target_lang") or "uk")
    src = normalize_lang(manifest.get("source_lang") or "en")

    for attempt in range(MAX_STREAM_RETRIES + 1):
        enrich_segment_scores(out, tgt_lang=tgt)
        slot_ms = out.get("timing_slot_ms")
        audit = audit_segment_for_reviewer(
            out,
            source_lang=src,
            target_lang=tgt,
            slot_ms=slot_ms,
        )
        if audit.get("pass") or attempt >= MAX_STREAM_RETRIES:
            break
        out = route_failed_segment(
            out,
            audit,
            manifest=manifest,
            state=state,
            task_id=task_id,
            segment_index=list_index,
        )
        state["segments"][list_index] = out
        out["reviewer_retry_count"] = attempt + 1

    final = resolve_post_quality_text(out).strip()
    out["reviewer_approved"] = bool(final)
    out["final_text"] = final
    out["voice_input"] = final
    return out


def process_voice_prep_segment(
    list_index: int,
    seg: dict[str, Any],
    *,
    manifest: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    """Prepare one segment for TTS — snapshot-safe voice_input."""
    from engines.translation_validation import resolve_post_quality_text

    out = copy.deepcopy(seg)
    text = resolve_post_quality_text(out).strip()
    if not text:
        return out

    out["voice_input"] = text
    out["final_text"] = text
    out["text_for_tts"] = text
    out["plain_text"] = text
    brief = out.get("creative_brief") or {}
    if brief:
        out["voice_prep_emotion"] = brief.get("emotion")
        out["voice_prep_speed"] = brief.get("speaking_speed")
    return out


def process_voice_segment_stream(
    list_index: int,
    seg: dict[str, Any],
    *,
    manifest: dict[str, Any],
    state: dict[str, Any],
    task_id: str,
) -> dict[str, Any]:
    from engines.ai_core.streaming_pipeline.voice_stage import process_voice_segment

    return process_voice_segment(
        list_index, seg, manifest=manifest, state=state, task_id=task_id
    )
