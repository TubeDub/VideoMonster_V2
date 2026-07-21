"""P4: Studio editorial helpers — DSAL refresh + re-lock after user edits."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("tubedub.engines.dsal.studio_editorial")


def refresh_dsal_on_segment(
    seg: dict[str, Any],
    *,
    source_hint: str = "",
    tgt_lang: str = "uk",
    allow_llm: bool = False,
) -> dict[str, Any]:
    """Re-run DSAL on one edited segment (pre-LOCK). Unlocks if was deferred."""
    from engines.dsal import adapt_duration_semantic, stamp_dsal_on_segment
    from engines.translation_validation import apply_translated_text_to_segment

    text = str(
        seg.get("final_text")
        or seg.get("translation_text")
        or seg.get("text")
        or seg.get("plain_text")
        or ""
    ).strip()
    slot_ms = int(seg.get("slot_ms") or 0)
    if not text or slot_ms <= 0:
        return {"ok": False, "reason": "missing_text_or_slot"}

    # Allow re-edit of deferred / unlocked segments only
    if seg.get("translation_locked") and not seg.get("needs_studio"):
        return {"ok": False, "reason": "locked"}

    actual = int(seg.get("tts_ms") or seg.get("playback_duration") or 0) or None
    result = adapt_duration_semantic(
        text,
        source_hint=source_hint,
        slot_ms=slot_ms,
        tgt_lang=tgt_lang,
        actual_tts_ms=actual,
        allow_llm=allow_llm,
    )
    stamp_dsal_on_segment(seg, result)
    if result.changed and result.text.strip() != text:
        apply_translated_text_to_segment(seg, result.text.strip())
    # Clear hard lock so gate can re-evaluate
    if seg.get("needs_studio") or seg.get("lock_gate_failed"):
        seg["translation_locked"] = False
    return {
        "ok": True,
        "changed": result.changed,
        "text": result.text,
        "band": result.analysis.band,
        "delta_ms": result.analysis.delta_ms,
        "duration_match_score": result.analysis.duration_match_score,
        "clause_coverage": result.clause_coverage,
        "method": result.method,
    }


def refresh_dsal_on_edits(
    info: dict[str, Any],
    *,
    indices: list[int] | None = None,
    allow_llm: bool = False,
) -> dict[str, Any]:
    """Refresh DSAL for edited segment indices (0-based)."""
    segments = list(info.get("segments_data") or [])
    src_segs = list(info.get("source_segments") or info.get("original_segments") or [])
    tgt = str(info.get("target_lang") or info.get("tgt_lang") or "uk")
    targets = indices if indices is not None else list(range(len(segments)))
    results = []
    for i in targets:
        if i < 0 or i >= len(segments) or not isinstance(segments[i], dict):
            continue
        src = src_segs[i] if i < len(src_segs) else ""
        if not src:
            src = str(
                segments[i].get("source_text") or segments[i].get("original_text") or ""
            )
        meta = refresh_dsal_on_segment(
            segments[i],
            source_hint=str(src),
            tgt_lang=tgt,
            allow_llm=allow_llm,
        )
        results.append({"index": i, **meta})
    info["dsal_editorial_refresh"] = {
        "count": len(results),
        "changed": sum(1 for r in results if r.get("changed")),
    }
    return info["dsal_editorial_refresh"]


def relock_after_editorial(info: dict[str, Any]) -> dict[str, Any]:
    """Re-evaluate LOCK gate after Studio editorial edits."""
    from engines.dsal.lock_gate import apply_lock_with_gate
    from engines.pipeline_integrity.translation_lock import lock_segments

    segments = list(info.get("segments_data") or [])
    # Unlock deferred segs that user fixed so gate can re-check
    for seg in segments:
        if isinstance(seg, dict) and seg.get("needs_studio"):
            seg["translation_locked"] = False

    meta = apply_lock_with_gate(segments, info=info, lock_segments_fn=lock_segments)
    logger.info(
        "editorial re-lock: deferred=%s locked=%s",
        meta.get("deferred_segments"),
        meta.get("locked_segments"),
    )
    return meta
