"""RASM R5 — DSAL / TQE hooks. Never mutates approved text after LOCK."""

from __future__ import annotations

import logging
from typing import Any

from engines.rasm.config import RasmSettings, default_settings
from engines.rasm.metrics import SegmentSyncMetrics, analyze_segments

logger = logging.getLogger("tubedub.rasm.hooks")


def _is_segment_locked(seg: dict[str, Any], info: dict[str, Any] | None = None) -> bool:
    if seg.get("translation_locked"):
        return True
    try:
        from engines.pipeline_integrity.translation_lock import is_segment_locked

        if is_segment_locked(seg):
            return True
    except Exception:
        pass
    if info and info.get("translation_locked") and seg.get("approved_text"):
        return True
    return False


def apply_tqe_sync_flags(
    segments: list[dict[str, Any]],
    rows: list[SegmentSyncMetrics] | None = None,
    *,
    settings: RasmSettings | None = None,
) -> int:
    """Stamp SYNC_WARNING / SYNC_FAIL on segments as QC flags (no text rewrite)."""
    cfg = settings or default_settings()
    metrics = rows or analyze_segments(segments, settings=cfg)
    by_id = {m.segment_id: m for m in metrics}
    stamped = 0
    for i, seg in enumerate(segments or []):
        if not isinstance(seg, dict):
            continue
        sid = str(seg.get("segment_id") or seg.get("id") or f"seg_{i}")
        m = by_id.get(sid) or (metrics[i] if i < len(metrics) else None)
        if not m:
            continue
        if m.sync_qc:
            seg["sync_qc"] = m.sync_qc
            seg["rasm_status"] = m.status
            seg["rasm_flags"] = list(m.flags)
            stamped += 1
        else:
            seg.pop("sync_qc", None)
            seg["rasm_status"] = m.status
            seg["rasm_flags"] = list(m.flags)
        if getattr(m, "overflow_cause", None):
            seg["rasm_overflow_cause"] = m.overflow_cause
        else:
            seg.pop("rasm_overflow_cause", None)
    return stamped


def propose_dsal_compression(
    segments: list[dict[str, Any]],
    *,
    info: dict[str, Any] | None = None,
    settings: RasmSettings | None = None,
    rows: list[SegmentSyncMetrics] | None = None,
) -> dict[str, Any]:
    """Propose DSAL compressed candidates ONLY for pre-LOCK segments with bad sync.

    Never mutates approved/locked text. Returns proposal list for Studio/Review.
    """
    cfg = settings or default_settings()
    info = info or {}
    metrics = rows or analyze_segments(segments, settings=cfg)
    proposals: list[dict[str, Any]] = []
    skipped_locked = 0

    for i, seg in enumerate(segments or []):
        if not isinstance(seg, dict):
            continue
        m = metrics[i] if i < len(metrics) else None
        if not m:
            continue
        if m.status == "green":
            continue
        if m.overflow_ms <= 0 and "tight_reserve" not in m.flags and m.status != "red":
            continue

        if _is_segment_locked(seg, info):
            skipped_locked += 1
            logger.info(
                "RASM DSAL skip locked seg=%s status=%s",
                m.segment_id,
                m.status,
            )
            continue

        text = (
            seg.get("approved_text")
            or seg.get("translated_text")
            or seg.get("text")
            or ""
        )
        proposals.append({
            "segment_id": m.segment_id,
            "index": i,
            "status": m.status,
            "overflow_ms": m.overflow_ms,
            "reserve_ms": m.reserve_ms,
            "reason": "rasm_sync_pre_lock",
            "action": "dsal_compress_candidate",
            "text_preview": str(text)[:120],
            # Caller (DSAL/Studio) may run compression; RASM does not rewrite here.
            "mutate_text": False,
        })

    return {
        "ok": True,
        "proposals": proposals,
        "skipped_locked": skipped_locked,
        "note": "Proposals only; text mutation must go through DSAL pre-LOCK path.",
    }


def run_rasm_hooks(
    segments: list[dict[str, Any]],
    *,
    info: dict[str, Any] | None = None,
    settings: RasmSettings | None = None,
    propose_dsal: bool = True,
) -> dict[str, Any]:
    """Apply TQE sync flags + optional pre-LOCK DSAL proposals."""
    cfg = settings or default_settings()
    rows = analyze_segments(segments, settings=cfg)
    stamped = apply_tqe_sync_flags(segments, rows, settings=cfg)
    dsal: dict[str, Any] = {"proposals": [], "skipped_locked": 0}
    if propose_dsal:
        dsal = propose_dsal_compression(
            segments, info=info, settings=cfg, rows=rows
        )
    return {
        "ok": True,
        "metrics": [r.to_dict() for r in rows],
        "tqe_flags_stamped": stamped,
        "dsal": dsal,
    }
