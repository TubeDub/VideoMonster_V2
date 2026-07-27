"""Post-TTS preference: resegment long overflows before aggressive shorten (TZ §11–12)."""

from __future__ import annotations

import copy
import logging
import re
from typing import Any

from engines.adaptive_segmentation.config import load_adaptive_seg_config
from engines.adaptive_segmentation.core import _allocate_times, _safe_split_chunks

logger = logging.getLogger("tubedub.adaptive_segmentation.post_tts")

_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


def should_prefer_resegment(
    *,
    slot_ms: int,
    tts_ms: int,
    overflow_ms: int = 0,
) -> bool:
    """True when overflow is likely caused by an oversized dub slot."""
    cfg = load_adaptive_seg_config()
    slot = max(0, int(slot_ms or 0))
    tts = max(0, int(tts_ms or 0))
    ov = max(0, int(overflow_ms or (tts - slot if tts > slot else 0)))
    if slot >= cfg.max_ms:
        return True
    if slot >= cfg.soft_max_ms and ov >= 400:
        return True
    if slot > 0 and tts > int(slot * 1.25) and slot >= cfg.preferred_ms:
        return True
    return False


def try_split_long_overflow_segment(
    *,
    segments_data: list[dict[str, Any]],
    source_segments: list[str],
    timing_map: list[Any],
    audits: list[dict[str, Any]] | None,
    idx: int,
) -> bool:
    """
    Split one oversized overflowing segment into two meaning-aware pieces.

    Mutates lists in place. Clears TTS paths so caller can regenerate.
    Returns True if a split was applied.
    """
    if idx < 0 or idx >= len(segments_data):
        return False
    cfg = load_adaptive_seg_config()
    seg = segments_data[idx]
    if seg.get("adaptive_resegment_done"):
        return False

    # Resolve timing
    if idx < len(timing_map):
        item = timing_map[idx]
        if isinstance(item, dict):
            start_ms, end_ms = int(item.get("start", 0)), int(item.get("end", 0))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            start_ms, end_ms = int(item[0]), int(item[1])
        else:
            start_ms, end_ms = 0, int(seg.get("slot_ms") or 0)
    else:
        start_ms = int(seg.get("start_ms") or 0)
        end_ms = start_ms + int(seg.get("slot_ms") or 0)
    slot_ms = max(0, end_ms - start_ms)
    # Align with should_prefer_resegment: allow split from preferred band upward
    # (soft_max-only gate left medium overflows stuck on shorten — TZ §11).
    tts_ms = int(
        seg.get("tts_ms")
        or seg.get("playback_duration")
        or 0
    )
    overflow_ms = max(0, tts_ms - slot_ms) if tts_ms and slot_ms else 0
    min_split_slot = min(cfg.soft_max_ms, max(cfg.preferred_ms, int(cfg.max_ms * 0.7)))
    if slot_ms < min_split_slot and not (
        slot_ms >= cfg.preferred_ms and overflow_ms >= 400
    ):
        return False
    if slot_ms < cfg.min_ms:
        return False

    src = ""
    if idx < len(source_segments):
        src = str(source_segments[idx] or "").strip()
    tgt = str(
        seg.get("plain_text")
        or seg.get("translation_text")
        or seg.get("text")
        or seg.get("final_text")
        or ""
    ).strip()
    if not tgt and not src:
        return False

    # Prefer splitting source (re-translate later); fall back to target text
    base = src if len(_safe_split_chunks(src)) >= 2 else tgt
    chunks = _safe_split_chunks(base)
    if len(chunks) < 2:
        return False

    # Two halves: first sentence vs rest (natural for dubbing)
    left, right = chunks[0], " ".join(chunks[1:]).strip()
    if not left or not right:
        return False
    if len(right.split()) < 3:
        return False

    allocated = _allocate_times([left, right], start_ms, end_ms)
    (t0, s0, e0), (t1, s1, e1) = allocated[0], allocated[1]

    # Source split (parallel if source had sentences)
    src_chunks = _safe_split_chunks(src) if src else []
    if len(src_chunks) >= 2:
        src_left, src_right = src_chunks[0], " ".join(src_chunks[1:]).strip()
    else:
        src_left, src_right = left, right

    # Target split MUST follow source ownership (Stage 3 anti-bleed).
    # Never leave the full UK blob on the left while the right keeps EN only.
    tgt_chunks = _safe_split_chunks(tgt) if tgt else []
    if len(src_chunks) >= 2 and tgt:
        try:
            from engines.translation_segment_parity import (
                split_translation_by_sources,
            )

            tgt_left, tgt_right = split_translation_by_sources(
                tgt, [src_left, src_right]
            )
        except Exception:
            tgt_left, tgt_right = "", ""
        if not (tgt_left and tgt_right) and len(tgt_chunks) >= 2:
            tgt_left, tgt_right = tgt_chunks[0], " ".join(tgt_chunks[1:]).strip()
        if not tgt_right and tgt_left == tgt:
            # Last resort: source-char proportional word split (never full→left).
            try:
                from engines.translation_segment_parity import (
                    split_translation_by_sources,
                )

                tgt_left, tgt_right = split_translation_by_sources(
                    tgt, [src_left, src_right]
                )
            except Exception:
                tgt_left, tgt_right = tgt, ""
    elif len(tgt_chunks) >= 2:
        tgt_left, tgt_right = tgt_chunks[0], " ".join(tgt_chunks[1:]).strip()
    else:
        tgt_left, tgt_right = tgt, ""

    # PSA3 — when IdentityGuard is ON: archive old id, mint NEW ids, rebind.
    # Translation algorithm untouched — only lifecycle / orchestration order.
    try:
        from engines.pipeline_integrity.immutable_segment import (
            apply_split_reissue_in_place,
            immutable_contract_enabled,
        )

        if immutable_contract_enabled():
            if apply_split_reissue_in_place(
                segments_data=segments_data,
                source_segments=source_segments,
                timing_map=timing_map,
                idx=idx,
                src_left=src_left,
                src_right=src_right,
                tgt_left=tgt_left,
                tgt_right=tgt_right,
                start0=s0,
                end0=e0,
                start1=s1,
                end1=e1,
                audits=audits,
            ):
                logger.info(
                    "[AdaptiveSeg:post_tts] PSA3 reissue split seg#%d (%dms) → %dms + %dms",
                    idx,
                    slot_ms,
                    e0 - s0,
                    e1 - s1,
                )
                return True
    except Exception as _imm_exc:
        logger.warning(
            "[AdaptiveSeg:post_tts] PSA3 reissue path failed, legacy split: %s",
            _imm_exc,
        )

    new_seg = copy.deepcopy(seg)
    for key in (
        "file",
        "tts_file_path",
        "tts_ms",
        "playback_duration",
        "tts_timing",
        "post_tts_retry",
        "text_adaptation_trace",
    ):
        seg.pop(key, None)
        new_seg.pop(key, None)

    # Always mint a NEW UUID for the right half (legacy path used to copy
    # parent segment_id → PipelineIdentityError at slot_fit).
    try:
        from engines.pipeline_integrity.segment import new_segment_id

        parent_sid = str(seg.get("segment_id") or "").strip()
        new_sid = new_segment_id()
        new_seg["segment_id"] = new_sid
        new_seg["segment_uuid"] = new_sid
        if parent_sid:
            new_seg["reissued_from"] = [parent_sid]
            new_seg["split_from_segment_id"] = parent_sid
    except Exception:
        pass

    for s, text, src_t, st, en in (
        (seg, tgt_left or t0, src_left, s0, e0),
        (new_seg, tgt_right or t1, src_right, s1, e1),
    ):
        s["plain_text"] = text
        s["text"] = text
        s["final_text"] = text
        s["translation_text"] = text
        s["start_ms"] = st
        s["end_ms"] = en
        s["slot_ms"] = max(1, en - st)
        s["adaptive_resegment_done"] = True
        s["needs_retranslate"] = not bool(text.strip())
        s["adaptation_stages"] = list(s.get("adaptation_stages") or []) + [
            "adaptive_resegment"
        ]

    # Insert after idx
    segments_data.insert(idx + 1, new_seg)
    if idx < len(source_segments):
        source_segments[idx] = src_left
        source_segments.insert(idx + 1, src_right)
    else:
        source_segments.append(src_left)
        source_segments.append(src_right)

    timing_left = {"start": s0, "end": e0}
    timing_right = {"start": s1, "end": e1}
    if idx < len(timing_map):
        timing_map[idx] = timing_left
        timing_map.insert(idx + 1, timing_right)
    else:
        timing_map.append(timing_left)
        timing_map.append(timing_right)

    if audits is not None:
        # Re-index audits after insert
        audit_by = {int(a.get("index", -1)): a for a in audits}
        left_audit = dict(audit_by.get(idx) or {"index": idx})
        left_audit["index"] = idx
        left_audit["whisper_text"] = src_left
        if tgt_left:
            left_audit["final_text"] = tgt_left
        right_audit = dict(audit_by.get(idx) or {"index": idx + 1})
        right_audit["index"] = idx + 1
        right_audit["whisper_text"] = src_right
        if tgt_right:
            right_audit["final_text"] = tgt_right
        else:
            right_audit["final_text"] = ""
            right_audit["needs_retranslate"] = True
        # Rebuild list preserving other indices (shift > idx)
        rebuilt: list[dict[str, Any]] = []
        for a in audits:
            ai = int(a.get("index", -1))
            if ai == idx:
                continue
            if ai > idx:
                a = dict(a)
                a["index"] = ai + 1
            rebuilt.append(a)
        rebuilt.append(left_audit)
        rebuilt.append(right_audit)
        rebuilt.sort(key=lambda x: int(x.get("index", 0)))
        audits.clear()
        audits.extend(rebuilt)

    logger.info(
        "[AdaptiveSeg:post_tts] split seg#%d (%dms) → %dms + %dms",
        idx,
        slot_ms,
        e0 - s0,
        e1 - s1,
    )
    return True
