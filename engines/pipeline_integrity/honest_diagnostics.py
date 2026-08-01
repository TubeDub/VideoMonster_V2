"""PSA7 — Diagnostics truth (Pipeline Stability v2).

Split conflated algorithm_reason into stage-specific fields that must match
actually executed actions. Never claim «semantic shorten» for audio-only
strategies (AudioStrategyNoTextRewrite / trim / stretch / pause).

Summary metrics distinguish residual segment overflow from placement overlap.
"""

from __future__ import annotations

import re
from typing import Any

REASON_FIELDS = (
    "text_adaptation_reason",
    "audio_strategy_reason",
    "slot_strategy_reason",
    "scheduler_reason",
)

# Audio-only strategies — must NOT be reported as semantic shorten
_AUDIO_ONLY_TOKENS = (
    "audiostrategynotextrewrite",
    "audio_strategy_no_text_rewrite",
    "silence_trim",
    "trim",
    "stretch",
    "pause",
    "pause_compress",
    "atempo",
    "gap_absorb",
    "slot_fit",
    "fitted_file",
    "video_adapt",
    "no_text_rewrite",
)

_SEMANTIC_SHORTEN_RE = re.compile(
    r"semantic\s*shorten|semantic_shortening|text\s*adaptation.*shorten",
    re.I,
)

_TEXT_ADAPT_TOKENS = (
    "semantic_shortening",
    "semantic shorten",
    "llm_adaptation",
    "meaning_fit",
    "naturalizer",
    "retranslate",
    "text_compression",
    "timing_aware_translation",
)


def set_reason(
    seg: dict[str, Any],
    field: str,
    reason: str,
    *,
    append_trace: bool = True,
) -> None:
    if field not in REASON_FIELDS:
        raise ValueError(f"unknown reason field: {field}")
    reason = str(reason or "").strip()
    if not reason:
        return
    # PSA7: refuse to stamp semantic-shorten into audio_strategy_reason
    if field == "audio_strategy_reason" and _SEMANTIC_SHORTEN_RE.search(reason):
        reason = "AudioStrategyNoTextRewrite"
    # PSA7: refuse to stamp audio-only labels into text_adaptation_reason
    if field == "text_adaptation_reason" and _is_audio_only_label(reason):
        return
    seg[field] = reason
    if append_trace:
        trace = list(seg.get("decision_trace") or [])
        entry = f"{field}:{reason}"
        if not trace or trace[-1] != entry:
            trace.append(entry)
        seg["decision_trace"] = trace[-50:]


def sync_decision_trace(seg: dict[str, Any], executed: list[str]) -> None:
    """Replace decision_trace with the list of actions that actually ran."""
    seg["decision_trace"] = [str(x) for x in (executed or []) if str(x).strip()][-50:]


def residual_overflow_ms(seg: dict[str, Any]) -> int:
    """Segment speech vs slot overflow (NOT placement overlap)."""
    slot = int(seg.get("slot_ms") or 0)
    tts_ms = int(
        seg.get("playback_duration")
        or seg.get("tts_ms")
        or seg.get("actual_duration_ms")
        or 0
    )
    if slot > 0 and tts_ms > 0:
        return max(0, tts_ms - slot)
    pred = int(seg.get("predicted_overflow_ms") or 0)
    stamped = int(seg.get("residual_overflow_ms") or 0)
    return max(0, pred, stamped)


def _norm(s: str) -> str:
    return re.sub(r"[\s_\-]+", "", str(s or "").lower())


def _is_audio_only_label(reason: str) -> bool:
    n = _norm(reason)
    return any(tok.replace("_", "") in n for tok in _AUDIO_ONLY_TOKENS)


def _looks_like_semantic_shorten(reason: str) -> bool:
    return bool(_SEMANTIC_SHORTEN_RE.search(str(reason or "")))


def _overflow_decision_chosen(seg: dict[str, Any]) -> str:
    dec = seg.get("overflow_decision")
    if isinstance(dec, dict):
        return str(dec.get("chosen") or "").strip()
    return ""


def _decision_trace_has_audio_only(seg: dict[str, Any]) -> bool:
    trace = seg.get("decision_trace")
    blobs: list[str] = []
    if isinstance(trace, list):
        blobs.extend(str(x) for x in trace)
    elif isinstance(trace, dict):
        for st in trace.get("stages") or []:
            if isinstance(st, dict):
                blobs.append(str(st.get("reason") or ""))
                blobs.append(str(st.get("stage") or ""))
            else:
                blobs.append(str(st))
    stages = seg.get("adaptation_stages") or []
    if isinstance(stages, list):
        blobs.extend(str(x) for x in stages)
    blob = " ".join(blobs)
    if "AudioStrategyNoTextRewrite" in blob:
        return True
    chosen = _overflow_decision_chosen(seg).lower()
    if chosen in (
        "trim",
        "stretch",
        "pause",
        "silence_trim",
        "gap_absorb",
        "atempo",
        "audio_only",
        "audiostrategynotextrewrite",
    ):
        return True
    if seg.get("fitted_file") and not (
        seg.get("text_adaptation_trace") or {}
    ).get("executed"):
        return True
    return _is_audio_only_label(blob)


def _text_adaptation_really_executed(seg: dict[str, Any]) -> bool:
    if seg.get("rule_rewrite_used") or seg.get("expand_executed"):
        return True
    algo = str(seg.get("algorithm_reason") or seg.get("text_adaptation_reason") or "")
    if any(
        tok in algo
        for tok in (
            "TextSlotFitExpand",
            "TextSlotFitShorten",
            "TextThenAtemo",
            "text_slot_fit",
        )
    ):
        return True
    trace = seg.get("text_adaptation_trace")
    if isinstance(trace, dict) and trace.get("executed"):
        # Only true text rewrite — not a false stamp over audio-only
        reasons = " ".join(str(x) for x in (trace.get("reasons") or []))
        stages = " ".join(str(x) for x in (trace.get("stages") or []))
        if _is_audio_only_label(reasons) or _is_audio_only_label(stages):
            return False
        return True
    stages = " ".join(str(x) for x in (seg.get("adaptation_stages") or []))
    if any(
        tok in stages.lower()
        for tok in (
            "semantic",
            "llm_adapt",
            "meaning_fit",
            "naturalizer",
            "retranslate",
            "text_slot_fit",
            "textslotfit",
            "rule_expand",
            "expand_to_fill",
            "stage19b",
        )
    ):
        if "audiostrategynotextrewrite" in _norm(stages):
            return False
        return True
    return False


def detect_audio_strategy_reason(seg: dict[str, Any]) -> str:
    existing = str(seg.get("audio_strategy_reason") or "").strip()
    if existing and not _looks_like_semantic_shorten(existing):
        return existing
    chosen = _overflow_decision_chosen(seg)
    if chosen:
        if _is_audio_only_label(chosen) or chosen.lower() in (
            "trim",
            "stretch",
            "pause",
            "silence_trim",
            "gap_absorb",
            "atempo",
        ):
            return (
                "AudioStrategyNoTextRewrite"
                if chosen.lower() in ("audio_only", "audiostrategynotextrewrite", "")
                else f"audio_strategy:{chosen}"
            )
    if _decision_trace_has_audio_only(seg):
        return "AudioStrategyNoTextRewrite"
    mode = str(seg.get("video_adapt_mode") or "")
    if mode == "gap_absorb":
        return "gap_absorb"
    if mode == "video_adapt":
        return "video_adapt_stretch"
    if seg.get("fitted_file") and not _text_adaptation_really_executed(seg):
        return "slot_fit:silence_trim_or_pause"
    return existing


def detect_text_adaptation_reason(seg: dict[str, Any]) -> str:
    if not _text_adaptation_really_executed(seg):
        # Clear dishonest semantic shorten when audio-only
        existing = str(seg.get("text_adaptation_reason") or "").strip()
        if _looks_like_semantic_shorten(existing) and _decision_trace_has_audio_only(seg):
            return ""
        return existing if existing and not _looks_like_semantic_shorten(existing) else ""
    existing = str(seg.get("text_adaptation_reason") or "").strip()
    if existing and not _is_audio_only_label(existing):
        return existing
    return "semantic_shortening"


def sanitize_algorithm_reason(
    algorithm_reason: str,
    *,
    seg: dict[str, Any] | None = None,
    text_adaptation_reason: str = "",
    audio_strategy_reason: str = "",
) -> str:
    """Forbid algorithm_reason containing «semantic shorten» for audio-only paths."""
    raw = str(algorithm_reason or "").strip()
    audio = audio_strategy_reason or (
        detect_audio_strategy_reason(seg) if seg is not None else ""
    )
    text = text_adaptation_reason or (
        detect_text_adaptation_reason(seg) if seg is not None else ""
    )
    audio_only = bool(audio) and (
        _is_audio_only_label(audio) or (seg is not None and _decision_trace_has_audio_only(seg))
    )
    if audio_only and _looks_like_semantic_shorten(raw):
        return audio or "AudioStrategyNoTextRewrite"
    if audio_only and not text:
        return audio or raw or "AudioStrategyNoTextRewrite"
    if text and not audio_only:
        return text if not _is_audio_only_label(text) else raw
    # Prefer explicit split fields over legacy blob
    if text:
        return text
    if audio:
        return audio
    return raw


def apply_honest_reasons(
    seg: dict[str, Any],
    *,
    timing_aware: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Stamp split reason fields + residual_overflow_ms; sanitize algorithm_reason."""
    if not isinstance(seg, dict):
        return {}

    residual = residual_overflow_ms(seg)
    seg["residual_overflow_ms"] = residual

    audio_r = detect_audio_strategy_reason(seg)
    text_r = detect_text_adaptation_reason(seg)

    # timing_aware text budget is text adaptation
    if timing_aware and timing_aware.get("adapted") and not audio_r:
        text_r = text_r or "timing_aware_translation"

    if audio_r:
        set_reason(seg, "audio_strategy_reason", audio_r, append_trace=False)
        seg["audio_strategy_reason"] = audio_r
    if text_r:
        set_reason(seg, "text_adaptation_reason", text_r, append_trace=False)
        seg["text_adaptation_reason"] = text_r
    elif _looks_like_semantic_shorten(str(seg.get("text_adaptation_reason") or "")) and audio_r:
        # Strip dishonest text reason
        seg["text_adaptation_reason"] = ""

    legacy = str(seg.get("algorithm_reason") or "")
    # Stage 19b: keep TextSlotFit* / TextThenAtemo as the honest algorithm_reason.
    if any(
        tok in legacy
        for tok in ("TextSlotFitExpand", "TextSlotFitShorten", "TextThenAtemo")
    ):
        text_r = text_r or legacy
        seg["text_adaptation_reason"] = text_r
        honest_algo = legacy
    else:
        honest_algo = sanitize_algorithm_reason(
            legacy
            or (
                "post_tts_text_adaptation: semantic shorten + TTS regen until slot fit"
                if text_r == "semantic_shortening"
                else ""
            )
            or audio_r
            or text_r,
            seg=seg,
            text_adaptation_reason=text_r,
            audio_strategy_reason=audio_r,
        )
    seg["algorithm_reason"] = honest_algo

    return collect_honest_summary(seg)


def collect_honest_summary(seg: dict[str, Any]) -> dict[str, Any]:
    text_r = str(seg.get("text_adaptation_reason") or "")
    audio_r = str(seg.get("audio_strategy_reason") or "")
    slot_r = str(
        seg.get("slot_strategy_reason")
        or (seg.get("slot_budget") or {}).get("reason")
        or ""
    )
    algo = sanitize_algorithm_reason(
        str(seg.get("algorithm_reason") or ""),
        seg=seg,
        text_adaptation_reason=text_r,
        audio_strategy_reason=audio_r,
    )
    return {
        "segment_id": seg.get("segment_id"),
        "text_adaptation_reason": text_r,
        "audio_strategy_reason": audio_r,
        "residual_overflow_ms": int(seg.get("residual_overflow_ms") or residual_overflow_ms(seg)),
        "slot_strategy_reason": slot_r,
        "scheduler_reason": seg.get("scheduler_reason") or "",
        "decision_trace": list(seg.get("decision_trace") or [])
        if isinstance(seg.get("decision_trace"), list)
        else [],
        # Legacy mirror — sanitized (never lies about semantic shorten)
        "algorithm_reason": algo,
    }


def _is_identity_mismatch_row(seg: dict[str, Any]) -> bool:
    owned = str(
        seg.get("translated_text")
        or seg.get("translation_text")
        or seg.get("plain_text")
        or ""
    ).strip()
    spoken = str(seg.get("final_tts_text") or seg.get("tts_text") or "").strip()
    if owned and spoken and owned != spoken:
        return True
    if seg.get("identity_mismatch") or seg.get("identity_shift"):
        return True
    return False


def _is_placement_overlap_row(seg: dict[str, Any], *, next_seg: dict[str, Any] | None) -> bool:
    """Timeline placement overlap — NOT the same as residual segment overflow."""
    if seg.get("merge_adjusted_start") or seg.get("placement_overlap"):
        return True
    if seg.get("audio_placement_overlap"):
        return True
    if not next_seg:
        return False
    start_a = int(seg.get("start_ms") or seg.get("start_time_ms") or 0)
    dur_a = int(
        seg.get("playback_duration")
        or seg.get("tts_ms")
        or seg.get("final_tts_duration_ms")
        or 0
    )
    start_b = int(next_seg.get("start_ms") or next_seg.get("start_time_ms") or 0)
    if dur_a > 0 and start_b > 0 and start_a + dur_a > start_b + 20:
        return True
    return False


def collect_stability_metrics(
    segments_data: list[dict[str, Any]] | None,
    *,
    task_info: dict[str, Any] | None = None,
    residual_threshold_ms: int = 350,
) -> dict[str, Any]:
    """Pipeline Stability summary metrics (PSA7).

    placement_overlap_count ≠ residual_overflow_count (segment speech vs slot).
    """
    rows = [
        s
        for s in (segments_data or [])
        if isinstance(s, dict) and s.get("merged_into") is None and not s.get("archived")
    ]

    identity_mismatch_count = 0
    micro_slot_count = 0
    residual_overflow_count = 0
    placement_overlap_count = 0

    try:
        from engines.pipeline_integrity.segment_normalizer import is_micro_or_fragment
    except Exception:
        is_micro_or_fragment = None  # type: ignore[assignment]

    for i, seg in enumerate(rows):
        apply_honest_reasons(seg)
        if _is_identity_mismatch_row(seg):
            identity_mismatch_count += 1
        text = str(
            seg.get("original")
            or seg.get("plain_text")
            or seg.get("text")
            or ""
        )
        slot = int(seg.get("slot_ms") or 0)
        if is_micro_or_fragment is not None:
            if is_micro_or_fragment(text, slot):
                micro_slot_count += 1
        elif 0 < slot < 850:
            micro_slot_count += 1
        if residual_overflow_ms(seg) > residual_threshold_ms:
            residual_overflow_count += 1
        nxt = rows[i + 1] if i + 1 < len(rows) else None
        if _is_placement_overlap_row(seg, next_seg=nxt):
            placement_overlap_count += 1

    # Also count placement overlaps reported on task_info OpenDDF issues
    if task_info is not None:
        for issue in task_info.get("placement_overlaps") or []:
            if isinstance(issue, dict) and issue.get("type") == "audio_placement_overlap":
                placement_overlap_count += 1
        # Prefer explicit identity_guard failures from log
        for entry in task_info.get("identity_guard_log") or []:
            if isinstance(entry, dict) and entry.get("ok") is False:
                identity_mismatch_count = max(
                    identity_mismatch_count, int(entry.get("mismatches") or 1)
                )

    metrics = {
        "identity_mismatch_count": identity_mismatch_count,
        "micro_slot_count": micro_slot_count,
        "residual_overflow_count": residual_overflow_count,
        "placement_overlap_count": placement_overlap_count,
        # Explicit disambiguation for consumers
        "notes": {
            "residual_overflow": "tts/playback duration vs slot_ms per segment",
            "placement_overlap": "timeline audio placement overlap between neighbors",
        },
    }
    if task_info is not None:
        task_info["stability_metrics"] = metrics
    return metrics


def map_segment_algorithm_reason(
    seg: dict[str, Any],
    timing_aware: dict[str, Any] | None = None,
) -> str:
    """Honest replacement for legacy _segment_algorithm_reason."""
    summary = apply_honest_reasons(seg, timing_aware=timing_aware or {})
    algo = str(summary.get("algorithm_reason") or "")
    if algo:
        return algo
    if summary.get("text_adaptation_reason") == "semantic_shortening":
        # Only when really executed
        if _text_adaptation_really_executed(seg):
            return "post_tts_text_adaptation: semantic shorten + TTS regen until slot fit"
    audio = str(summary.get("audio_strategy_reason") or "")
    if audio:
        return audio
    if timing_aware and timing_aware.get("adapted"):
        return "timing_aware_translation: pre-TTS text budget optimization"
    if seg.get("merge_adjusted_start"):
        return "block_merge: placement shifted after previous segment speech"
    if seg.get("block_merged_with_next"):
        return "block_merge: borrowed timing from next adjacent slot"
    return "direct_path: TTS fit source slot without text adaptation"
