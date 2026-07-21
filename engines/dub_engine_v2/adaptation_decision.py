"""Adaptation decision logging — mandatory skip_reason when not executed.

Invariant:
  adaptation_executed == False  ⇒  skip_reason is a non-empty known code.

OverflowDetected + AdaptationSkipped ⇒ pipeline FAIL (not SUCCESS).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("tubedub.dub_engine.adaptation_decision")

# Canonical skip reasons (explicit, searchable in OpenDDF)
SKIP_TRANSLATION_LOCKED = "TranslationLocked"
SKIP_OVERFLOW_BELOW_THRESHOLD = "OverflowBelowThreshold"
SKIP_UNDERFLOW_BELOW_THRESHOLD = "UnderflowBelowThreshold"
SKIP_FITS_NO_CHANGE = "FitsNoChange"
SKIP_RULE_ADAPTER_DISABLED = "RuleAdapterDisabled"
SKIP_SEMANTIC_ADAPTER_DISABLED = "SemanticAdapterDisabled"
SKIP_NO_SEMANTIC_CANDIDATES = "NoSemanticCandidates"
SKIP_LLM_UNAVAILABLE_FALLBACK_FAILED = "LLMUnavailableFallbackFailed"
SKIP_DECISION_ENGINE_RETURNED_SKIP = "DecisionEngineReturnedSkip"
SKIP_NO_REGEN_CALLBACK = "NoRegenCallback"
SKIP_ALREADY_EXECUTED = "AlreadyExecuted"
SKIP_MERGED_SEGMENT = "MergedSegment"
SKIP_EMPTY_TEXT = "EmptyText"
SKIP_UNKNOWN = "UnknownSkip"

# Temporary hard gate (user TZ): force need_adaptation when |orig−tts| > this.
DURATION_DELTA_FORCE_ADAPT_MS = 500

KNOWN_SKIP_REASONS = frozenset(
    {
        SKIP_TRANSLATION_LOCKED,
        SKIP_OVERFLOW_BELOW_THRESHOLD,
        SKIP_UNDERFLOW_BELOW_THRESHOLD,
        SKIP_FITS_NO_CHANGE,
        SKIP_RULE_ADAPTER_DISABLED,
        SKIP_SEMANTIC_ADAPTER_DISABLED,
        SKIP_NO_SEMANTIC_CANDIDATES,
        SKIP_LLM_UNAVAILABLE_FALLBACK_FAILED,
        SKIP_DECISION_ENGINE_RETURNED_SKIP,
        SKIP_NO_REGEN_CALLBACK,
        SKIP_ALREADY_EXECUTED,
        SKIP_MERGED_SEGMENT,
        SKIP_EMPTY_TEXT,
        SKIP_UNKNOWN,
    }
)


def _int(v: Any, default: int = 0) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return default


def segment_tts_duration_ms(seg: dict[str, Any]) -> int:
    """Best available measured / final TTS duration for need_adaptation gate."""
    for key in (
        "final_tts_duration_ms",
        "fitted_ms",
        "actual_duration_ms",
        "playback_duration",
        "tts_ms",
        "first_tts_duration_ms",
    ):
        ms = _int(seg.get(key))
        if ms > 0:
            return ms
    return 0


def segment_original_duration_ms(seg: dict[str, Any]) -> int:
    """Slot / original duration used before TTS (often Whisper slot)."""
    for key in ("original_duration_ms", "slot_ms"):
        ms = _int(seg.get(key))
        if ms > 0:
            return ms
    start = _int(seg.get("start_ms") or seg.get("start_time_ms"))
    end = _int(seg.get("end_ms") or seg.get("end_time_ms"))
    if end > start:
        return end - start
    return 0


def duration_delta_ms(seg: dict[str, Any]) -> int:
    """|original_duration_ms − TTS duration|."""
    orig = segment_original_duration_ms(seg)
    tts = segment_tts_duration_ms(seg)
    if orig <= 0 or tts <= 0:
        return 0
    return abs(tts - orig)


def resolve_need_adaptation(
    seg: dict[str, Any],
    *,
    need_adaptation: bool | None = None,
    overflow_ms: int | None = None,
    underflow_ms: int | None = None,
) -> bool:
    """
    Single source of truth for need_adaptation.

    Base: overflow/underflow/requires_llm OR explicit caller flag.
    TEMP hardcode: |original−TTS| > 500ms ⇒ always True (even if caller said False).
    """
    ov = (
        _int(overflow_ms)
        if overflow_ms is not None
        else _int(seg.get("overflow_ms"))
    )
    und = (
        _int(underflow_ms)
        if underflow_ms is not None
        else _int(seg.get("underflow_ms") or seg.get("shortfall_ms"))
    )
    if need_adaptation is None:
        need = ov > 0 or und > 0 or bool(seg.get("requires_llm_adaptation"))
    else:
        need = bool(need_adaptation)

    delta = duration_delta_ms(seg)
    if delta > DURATION_DELTA_FORCE_ADAPT_MS:
        # #region agent log
        try:
            import json
            import time

            with open(
                r"c:\Users\serhii\Desktop\VideoMonster_V2\debug-ee98a6.log",
                "a",
                encoding="utf-8",
            ) as f:
                f.write(
                    json.dumps(
                        {
                            "sessionId": "ee98a6",
                            "runId": "need-adapt-force",
                            "hypothesisId": "N1",
                            "location": "adaptation_decision.py:resolve_need_adaptation",
                            "message": "force_need_adaptation_duration_delta",
                            "data": {
                                "segment_id": str(seg.get("segment_id") or ""),
                                "original_ms": segment_original_duration_ms(seg),
                                "tts_ms": segment_tts_duration_ms(seg),
                                "delta_ms": delta,
                                "caller_need": need_adaptation,
                                "overflow_ms": ov,
                                "underflow_ms": und,
                                "forced": True,
                            },
                            "timestamp": int(time.time() * 1000),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except Exception:
            pass
        # #endregion
        need = True
        seg["need_adaptation"] = True
        seg["need_adaptation_force_reason"] = (
            f"DurationDelta>{DURATION_DELTA_FORCE_ADAPT_MS}ms (delta={delta})"
        )
    else:
        seg["need_adaptation"] = bool(need)
    return bool(need)


def stamp_need_adaptation_gate(
    seg: dict[str, Any],
    *,
    index: int = 0,
    source: str = "post_tts",
) -> bool:
    """
    Persist need_adaptation as soon as TTS duration is known.

    Does not run adapters — only recognition + adaptation_decision snapshot.
    Safe to call after TTS sync and again at closed-loop entry.
    """
    if seg.get("merged_into") is not None or seg.get("status") in (
        "merged",
        "empty",
        "failed",
    ):
        return bool(seg.get("need_adaptation"))

    # Seed original_duration_ms from slot edges once (for stable OpenDDF deltas).
    if segment_original_duration_ms(seg) > 0 and _int(seg.get("original_duration_ms")) <= 0:
        seg["original_duration_ms"] = segment_original_duration_ms(seg)

    need = resolve_need_adaptation(seg)
    snap = build_decision_snapshot(
        seg,
        index=index,
        need_adaptation=need,
        decision=str(
            (seg.get("adaptation_decision") or {}).get("decision")
            or f"{source}_duration_gate"
        ),
        adaptation_executed=bool(seg.get("adaptation_executed")),
        skip_reason=str(seg.get("adaptation_skip_reason") or ""),
    )
    # Preserve prior skip/executed stamps; always refresh need + duration fields.
    prev = dict(seg.get("adaptation_decision") or {})
    prev.update(
        {
            "need_adaptation": snap["need_adaptation"],
            "duration_delta_ms": snap["duration_delta_ms"],
            "original_duration_ms": snap["original_duration_ms"],
            "tts_duration_ms": snap["tts_duration_ms"],
            "decision": snap["decision"] or prev.get("decision") or f"{source}_duration_gate",
        }
    )
    if "adaptation_executed" not in prev:
        prev["adaptation_executed"] = snap["adaptation_executed"]
    if "skip_reason" not in prev and snap.get("skip_reason"):
        prev["skip_reason"] = snap["skip_reason"]
    seg["adaptation_decision"] = prev
    seg["need_adaptation"] = bool(need)

    # #region agent log
    try:
        import json
        import time

        with open(
            r"c:\Users\serhii\Desktop\VideoMonster_V2\debug-ee98a6.log",
            "a",
            encoding="utf-8",
        ) as f:
            f.write(
                json.dumps(
                    {
                        "sessionId": "ee98a6",
                        "runId": "need-adapt-stamp",
                        "hypothesisId": "N2",
                        "location": "adaptation_decision.py:stamp_need_adaptation_gate",
                        "message": "stamped_need_adaptation_after_tts",
                        "data": {
                            "segment_id": str(seg.get("segment_id") or ""),
                            "index": index,
                            "source": source,
                            "need_adaptation": bool(need),
                            "force_reason": str(
                                seg.get("need_adaptation_force_reason") or ""
                            ),
                            "delta_ms": snap["duration_delta_ms"],
                            "original_ms": snap["original_duration_ms"],
                            "tts_ms": snap["tts_duration_ms"],
                            "stored_need": (seg.get("adaptation_decision") or {}).get(
                                "need_adaptation"
                            ),
                        },
                        "timestamp": int(time.time() * 1000),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except Exception:
        pass
    # #endregion
    return bool(need)


def build_decision_snapshot(
    seg: dict[str, Any],
    *,
    index: int = 0,
    overflow_ms: int | None = None,
    underflow_ms: int | None = None,
    need_adaptation: bool | None = None,
    translation_locked: bool | None = None,
    llm_available: bool | None = None,
    rule_adapter_enabled: bool = True,
    semantic_adapter_enabled: bool = True,
    decision: str = "",
    adaptation_executed: bool | None = None,
    skip_reason: str = "",
) -> dict[str, Any]:
    """Full decision-chain snapshot for logs / OpenDDF."""
    ov = (
        _int(overflow_ms)
        if overflow_ms is not None
        else _int(seg.get("overflow_ms"))
    )
    und = (
        _int(underflow_ms)
        if underflow_ms is not None
        else _int(seg.get("underflow_ms") or seg.get("shortfall_ms"))
    )
    if translation_locked is None:
        try:
            from engines.pipeline_integrity.translation_lock import is_segment_locked

            translation_locked = bool(is_segment_locked(seg))
        except Exception:
            translation_locked = bool(seg.get("translation_locked"))
    if llm_available is None:
        try:
            from engines.translation_adapt import llm_rephrase_available

            llm_available = bool(llm_rephrase_available())
        except Exception:
            llm_available = False
    executed = (
        bool(adaptation_executed)
        if adaptation_executed is not None
        else bool(seg.get("adaptation_executed"))
    )
    need_adaptation = resolve_need_adaptation(
        seg,
        need_adaptation=need_adaptation,
        overflow_ms=ov,
        underflow_ms=und,
    )
    reason = str(skip_reason or seg.get("adaptation_skip_reason") or "").strip()
    if not executed and not reason:
        reason = infer_skip_reason(
            seg,
            overflow_ms=ov,
            underflow_ms=und,
            translation_locked=bool(translation_locked),
            llm_available=bool(llm_available),
            rule_adapter_enabled=rule_adapter_enabled,
            semantic_adapter_enabled=semantic_adapter_enabled,
        )
    return {
        "segment_id": str(seg.get("segment_id") or seg.get("id") or index),
        "index": int(index),
        "overflow_ms": ov,
        "underflow_ms": und,
        "need_adaptation": bool(need_adaptation),
        "duration_delta_ms": duration_delta_ms(seg),
        "original_duration_ms": segment_original_duration_ms(seg),
        "tts_duration_ms": segment_tts_duration_ms(seg),
        "translation_locked": bool(translation_locked),
        "llm_available": bool(llm_available),
        "rule_adapter_enabled": bool(rule_adapter_enabled),
        "semantic_adapter_enabled": bool(semantic_adapter_enabled),
        "decision": str(decision or (seg.get("overflow_decision") or {}).get("chosen") or ""),
        "adaptation_executed": executed,
        "skip_reason": reason if not executed else "",
    }


def infer_skip_reason(
    seg: dict[str, Any],
    *,
    overflow_ms: int = 0,
    underflow_ms: int = 0,
    translation_locked: bool = False,
    llm_available: bool = False,
    rule_adapter_enabled: bool = True,
    semantic_adapter_enabled: bool = True,
    overflow_threshold_ms: int = 40,
) -> str:
    """Best-effort skip_reason when caller forgot to set one."""
    existing = str(seg.get("adaptation_skip_reason") or "").strip()
    if existing:
        return existing
    if seg.get("merged_into") is not None:
        return SKIP_MERGED_SEGMENT
    text = str(
        seg.get("final_text")
        or seg.get("plain_text")
        or seg.get("text")
        or ""
    ).strip()
    if not text:
        return SKIP_EMPTY_TEXT
    if translation_locked and (overflow_ms > overflow_threshold_ms or underflow_ms > 0):
        # Locked + overflow: text rewrite skipped (audio chain may still run)
        return SKIP_TRANSLATION_LOCKED
    if overflow_ms > 0 and overflow_ms <= overflow_threshold_ms:
        return SKIP_OVERFLOW_BELOW_THRESHOLD
    if underflow_ms > 0 and underflow_ms <= overflow_threshold_ms:
        return SKIP_UNDERFLOW_BELOW_THRESHOLD
    if overflow_ms <= 0 and underflow_ms <= 0 and not seg.get("requires_llm_adaptation"):
        return SKIP_FITS_NO_CHANGE
    if not rule_adapter_enabled:
        return SKIP_RULE_ADAPTER_DISABLED
    if not semantic_adapter_enabled:
        return SKIP_SEMANTIC_ADAPTER_DISABLED
    if not llm_available and seg.get("requires_llm_adaptation"):
        if seg.get("rule_fallback_applied") or seg.get("dsal_applied"):
            return SKIP_DECISION_ENGINE_RETURNED_SKIP
        return SKIP_LLM_UNAVAILABLE_FALLBACK_FAILED
    if seg.get("requires_llm_adaptation") and not seg.get("llm_called"):
        return SKIP_LLM_UNAVAILABLE_FALLBACK_FAILED
    return SKIP_UNKNOWN


def mark_adaptation_executed(
    seg: dict[str, Any],
    *,
    decision: str = "",
    stages: list[str] | None = None,
) -> None:
    """Record successful adaptation (clears skip_reason)."""
    seg["adaptation_executed"] = True
    seg["adaptation_status"] = "ADAPTATION EXECUTED"
    seg["adaptation_skip_reason"] = ""
    snap = build_decision_snapshot(
        seg,
        adaptation_executed=True,
        decision=decision,
        skip_reason="",
    )
    seg["adaptation_decision"] = snap
    if stages:
        trace = seg.setdefault("text_adaptation_trace", {})
        trace["executed"] = True
        prev = list(trace.get("stages") or [])
        for s in stages:
            if s not in prev:
                prev.append(s)
        trace["stages"] = prev
    try:
        from engines.dub_engine_v2.decision_trace import (
            STATUS_SUCCESS,
            record_final_result,
            record_stage,
            record_strategy_choice,
            STAGE_STRATEGY_RESULT,
        )

        why = str((seg.get("overflow_decision") or {}).get("why") or decision)
        record_strategy_choice(
            seg,
            chosen=str(decision or "adapted"),
            why=why,
            cost=(seg.get("overflow_decision") or {}).get("chosen_cost"),
            overflow_ms=int(snap.get("overflow_ms") or 0),
        )
        record_stage(
            seg,
            stage=STAGE_STRATEGY_RESULT,
            status=STATUS_SUCCESS,
            reason=why,
            detail={"chosen": decision, "stages": list(stages or [])},
        )
        record_final_result(
            seg,
            status=STATUS_SUCCESS,
            reason=str(decision or "adapted"),
            overflow_ms=int(snap.get("overflow_ms") or 0),
            duration_ms=int(seg.get("actual_duration_ms") or seg.get("tts_ms") or 0),
        )
    except Exception:
        pass
    logger.info(
        "ADAPTATION_DECISION segment=%s overflow=%s underflow=%s "
        "need=%s locked=%s llm=%s decision=%s executed=True skip_reason=",
        snap["segment_id"],
        snap["overflow_ms"],
        snap["underflow_ms"],
        snap["need_adaptation"],
        snap["translation_locked"],
        snap["llm_available"],
        snap["decision"],
    )


def mark_adaptation_skipped(
    seg: dict[str, Any],
    *,
    skip_reason: str,
    index: int = 0,
    overflow_ms: int | None = None,
    underflow_ms: int | None = None,
    need_adaptation: bool | None = None,
    decision: str = "skip",
    rule_adapter_enabled: bool = True,
    semantic_adapter_enabled: bool = True,
) -> dict[str, Any]:
    """Record skipped adaptation — skip_reason is mandatory."""
    reason = str(skip_reason or "").strip() or SKIP_UNKNOWN
    if reason not in KNOWN_SKIP_REASONS:
        # Allow custom codes but normalize empty
        reason = reason if reason else SKIP_UNKNOWN
    seg["adaptation_executed"] = False
    seg["adaptation_status"] = "ADAPTATION NOT EXECUTED"
    seg["adaptation_skip_reason"] = reason
    snap = build_decision_snapshot(
        seg,
        index=index,
        overflow_ms=overflow_ms,
        underflow_ms=underflow_ms,
        need_adaptation=need_adaptation,
        decision=decision,
        adaptation_executed=False,
        skip_reason=reason,
        rule_adapter_enabled=rule_adapter_enabled,
        semantic_adapter_enabled=semantic_adapter_enabled,
    )
    seg["adaptation_decision"] = snap
    try:
        from engines.dub_engine_v2.decision_trace import (
            STATUS_FAILED,
            STATUS_SKIPPED,
            record_adapter_outcome,
            record_final_result,
            record_need_adaptation,
            record_stage,
            STAGE_DECISION_ENGINE,
        )

        ov = int(snap.get("overflow_ms") or 0)
        und = int(snap.get("underflow_ms") or 0)
        need = bool(snap.get("need_adaptation"))
        record_need_adaptation(
            seg, need=need, overflow_ms=ov, underflow_ms=und, index=index
        )
        record_stage(
            seg,
            stage=STAGE_DECISION_ENGINE,
            status=STATUS_SKIPPED,
            reason=reason,
            detail={"decision": decision},
            index=index,
        )
        if reason == SKIP_TRANSLATION_LOCKED or snap.get("translation_locked"):
            record_adapter_outcome(
                seg,
                adapter="rule",
                status=STATUS_SKIPPED,
                reason=reason,
                index=index,
            )
            record_adapter_outcome(
                seg,
                adapter="semantic",
                status=STATUS_SKIPPED,
                reason=reason,
                index=index,
            )
        elif reason == SKIP_RULE_ADAPTER_DISABLED:
            record_adapter_outcome(
                seg,
                adapter="rule",
                status=STATUS_SKIPPED,
                reason=reason,
                index=index,
            )
        elif reason in (
            SKIP_NO_SEMANTIC_CANDIDATES,
            SKIP_SEMANTIC_ADAPTER_DISABLED,
            SKIP_LLM_UNAVAILABLE_FALLBACK_FAILED,
        ):
            record_adapter_outcome(
                seg,
                adapter="semantic",
                status=STATUS_SKIPPED if reason != SKIP_LLM_UNAVAILABLE_FALLBACK_FAILED else STATUS_FAILED,
                reason=reason,
                index=index,
            )
        if ov > 0:
            record_final_result(
                seg,
                status=STATUS_FAILED,
                reason=f"OverflowDetected_AdaptationSkipped:{reason}",
                overflow_ms=ov,
                index=index,
            )
        else:
            record_final_result(
                seg,
                status=STATUS_SKIPPED,
                reason=reason,
                overflow_ms=ov,
                index=index,
            )
    except Exception:
        pass
    logger.warning(
        "ADAPTATION_DECISION segment=%s overflow=%s underflow=%s "
        "need=%s locked=%s llm=%s rule=%s semantic=%s decision=%s "
        "executed=False skip_reason=%s",
        snap["segment_id"],
        snap["overflow_ms"],
        snap["underflow_ms"],
        snap["need_adaptation"],
        snap["translation_locked"],
        snap["llm_available"],
        snap["rule_adapter_enabled"],
        snap["semantic_adapter_enabled"],
        snap["decision"],
        snap["skip_reason"],
    )
    return snap


def ensure_skip_reason(seg: dict[str, Any], *, index: int = 0) -> str:
    """Invariant: if not executed, skip_reason must be set. Returns reason or ''."""
    if bool(seg.get("adaptation_executed")):
        if "adaptation_skip_reason" not in seg:
            seg["adaptation_skip_reason"] = ""
        return ""
    reason = str(seg.get("adaptation_skip_reason") or "").strip()
    if not reason:
        reason = infer_skip_reason(seg)
        seg["adaptation_skip_reason"] = reason
        seg["adaptation_status"] = "ADAPTATION NOT EXECUTED"
        seg["adaptation_decision"] = build_decision_snapshot(
            seg, index=index, adaptation_executed=False, skip_reason=reason
        )
    return reason


def finalize_segment_adaptation_fields(seg: dict[str, Any], *, index: int = 0) -> None:
    """Call before OpenDDF / SUCCESS gate — never leave false without skip_reason."""
    if seg.get("adaptation_executed"):
        seg["adaptation_status"] = "ADAPTATION EXECUTED"
        seg.setdefault("adaptation_skip_reason", "")
    else:
        ensure_skip_reason(seg, index=index)
        seg["adaptation_status"] = "ADAPTATION NOT EXECUTED"
    try:
        from engines.dub_engine_v2.decision_trace import ensure_decision_trace_complete

        ensure_decision_trace_complete(seg, index=index)
    except Exception:
        pass


def overflow_adaptation_violation(seg: dict[str, Any]) -> dict[str, Any] | None:
    """
    Illegal state: overflow > 0 AND adaptation_executed == false.
    Returns diagnostic dict or None if OK.
    """
    ov = _int(seg.get("overflow_ms"))
    if ov <= 0 and not seg.get("slot_overflow"):
        return None
    if ov <= 0:
        ov = _int((seg.get("overlap_info") or {}).get("overflow_ms"))
    if ov <= 0:
        return None
    if bool(seg.get("adaptation_executed")):
        return None
    reason = ensure_skip_reason(seg)
    return {
        "code": "OverflowDetected_AdaptationSkipped",
        "overflow_ms": ov,
        "adaptation_executed": False,
        "skip_reason": reason,
        "segment_id": str(seg.get("segment_id") or ""),
        "message": (
            f"Pipeline FAILED: OverflowDetected + AdaptationSkipped "
            f"(skip_reason={reason}, overflow_ms={ov})"
        ),
    }
