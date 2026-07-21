"""PSA3 — Immutable Segment Contract (Pipeline Stability v2).

Rules:
  1) Text must not move/swap between existing segment_id values.
  2) Resegment archives old segment_id and mints NEW ids + IdentityGuard rebind.
  3) Gated with IdentityGuard (VM_FLAG_IDENTITY_GUARD); default OFF → legacy.
"""

from __future__ import annotations

import logging
from typing import Any

from engines.pipeline_integrity.exceptions import SegmentImmutabilityError

logger = logging.getLogger("tubedub.pipeline_integrity.immutable_segment")

__all__ = [
    "SegmentImmutabilityError",
    "assert_no_text_move_or_swap",
    "forbid_swap_texts",
    "immutable_contract_enabled",
    "resegment_archive_and_reissue",
    "apply_split_reissue_in_place",
]


def immutable_contract_enabled(*, force: bool = False) -> bool:
    if force:
        return True
    from engines.pipeline_integrity.v2_gates import identity_guard_enabled

    return bool(identity_guard_enabled())


def _sid(seg: dict[str, Any]) -> str:
    return str(seg.get("segment_id") or seg.get("segment_uuid") or "").strip()


def _text(seg: dict[str, Any]) -> str:
    return str(
        seg.get("plain_text")
        or seg.get("translation_text")
        or seg.get("translated_text")
        or seg.get("final_text")
        or seg.get("text")
        or ""
    ).strip()


def forbid_swap_texts(
    seg_a: dict[str, Any],
    seg_b: dict[str, Any],
    *,
    stage: str = "immutable_swap",
    force: bool = False,
) -> None:
    """Explicitly reject swapping owned text between two live segment_ids."""
    if not immutable_contract_enabled(force=force):
        return
    a_id, b_id = _sid(seg_a), _sid(seg_b)
    if not a_id or not b_id or a_id == b_id:
        raise SegmentImmutabilityError(
            "ImmutableSegment: swap requires two distinct segment_id values",
            stage=stage,
            details={"a": a_id, "b": b_id},
        )
    if seg_a.get("archived") or seg_b.get("archived"):
        raise SegmentImmutabilityError(
            "ImmutableSegment: cannot swap involving archived segment_id",
            stage=stage,
            details={"a": a_id, "b": b_id},
        )
    raise SegmentImmutabilityError(
        "ImmutableSegment: move/swap of text between existing segment_id "
        "is forbidden — archive + reissue instead",
        stage=stage,
        details={"segment_id_a": a_id, "segment_id_b": b_id},
    )


def assert_no_text_move_or_swap(
    segments_data: list[dict[str, Any]],
    *,
    stage: str = "immutable_segment",
    force: bool = False,
) -> dict[str, Any]:
    """Detect text that left its bound segment_id and landed on another.

    Uses IdentityGuard ``identity_binding.text_hash`` when present.
    """
    if not immutable_contract_enabled(force=force):
        return {"enabled": False, "ok": True, "stage": stage}

    from engines.pipeline_integrity.identity_guard import text_content_hash

    bound_owner: dict[str, str] = {}  # text_hash → segment_id that bound it
    current_by_sid: dict[str, str] = {}

    for seg in segments_data or []:
        if not isinstance(seg, dict):
            continue
        if seg.get("merged_into") is not None or seg.get("archived"):
            continue
        sid = _sid(seg)
        if not sid:
            continue
        cur = _text(seg)
        cur_hash = text_content_hash(cur) if cur else ""
        current_by_sid[sid] = cur_hash
        binding = seg.get("identity_binding")
        if isinstance(binding, dict):
            b_hash = str(binding.get("text_hash") or "").strip()
            b_sid = str(binding.get("segment_id") or sid).strip()
            if b_hash:
                bound_owner[b_hash] = b_sid

    # Case 1: binding says hash H belongs to A, but H is now on B≠A and A lost it
    for seg in segments_data or []:
        if not isinstance(seg, dict) or seg.get("archived"):
            continue
        if seg.get("merged_into") is not None:
            continue
        sid = _sid(seg)
        binding = seg.get("identity_binding")
        if not isinstance(binding, dict):
            continue
        b_hash = str(binding.get("text_hash") or "").strip()
        if not b_hash:
            continue
        cur_hash = current_by_sid.get(sid, "")
        if cur_hash == b_hash:
            continue
        # Find who now holds the bound text
        holders = [oid for oid, h in current_by_sid.items() if h == b_hash and oid != sid]
        if holders:
            raise SegmentImmutabilityError(
                "ImmutableSegment: text moved between existing segment_id "
                f"({sid} → {holders[0]})",
                stage=stage,
                details={
                    "from_segment_id": sid,
                    "to_segment_id": holders[0],
                    "text_hash": b_hash,
                },
            )

    # Case 2: classic pairwise swap (A has B's bound hash, B has A's)
    sids = list(current_by_sid.keys())
    for i, a_id in enumerate(sids):
        for b_id in sids[i + 1 :]:
            a_seg = next(s for s in segments_data if _sid(s) == a_id)
            b_seg = next(s for s in segments_data if _sid(s) == b_id)
            a_bind = a_seg.get("identity_binding") if isinstance(a_seg, dict) else None
            b_bind = b_seg.get("identity_binding") if isinstance(b_seg, dict) else None
            if not isinstance(a_bind, dict) or not isinstance(b_bind, dict):
                continue
            a_bh = str(a_bind.get("text_hash") or "")
            b_bh = str(b_bind.get("text_hash") or "")
            if not a_bh or not b_bh or a_bh == b_bh:
                continue
            if current_by_sid.get(a_id) == b_bh and current_by_sid.get(b_id) == a_bh:
                raise SegmentImmutabilityError(
                    "ImmutableSegment: text swap between existing segment_id "
                    f"({a_id} ↔ {b_id})",
                    stage=stage,
                    details={"segment_id_a": a_id, "segment_id_b": b_id},
                )

    return {"enabled": True, "ok": True, "stage": stage, "checked": len(current_by_sid)}


def resegment_archive_and_reissue(
    old_segments: list[dict[str, Any]],
    new_texts: list[str],
    new_timing: list[Any],
    *,
    stage: str = "resegment",
    task_info: dict[str, Any] | None = None,
    force: bool = False,
    extra_fields: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    """Archive old segment_ids, mint NEW ids, IdentityGuard rebind.

    Does not call Translation — only lifecycle + bind orchestration.
    """
    from engines.pipeline_integrity.identity_guard import (
        archive_and_reissue_ids,
        assert_consistent,
        bind,
    )

    # Always mint new ids for boundary change (PSA3 contract). When flag OFF,
    # still allow force for tests; otherwise no-op returns empty archive + copy.
    if not immutable_contract_enabled(force=force):
        return [], list(old_segments or []), {}

    archived, fresh, uuid_map = archive_and_reissue_ids(
        old_segments, new_texts, new_timing
    )

    # Harden archive markers on live old rows
    for seg in old_segments or []:
        if not isinstance(seg, dict):
            continue
        seg["archived"] = True
        seg["segment_archived"] = True
        seg["immutable_archived_at"] = stage

    parent_ids = [
        str(a.get("segment_id") or "")
        for a in archived
        if str(a.get("segment_id") or "")
    ]
    for seg in fresh:
        seg["reissued_from"] = list(parent_ids)
        seg["reissued_from_resegment"] = True
        if extra_fields:
            for k, v in extra_fields.items():
                if k not in ("segment_id", "segment_uuid"):
                    seg[k] = v
        bind(
            seg,
            text=_text(seg),
            stage=stage,
            allow_rebind=True,
            force=force,
        )

    if fresh:
        assert_consistent(fresh, stage=f"post_{stage}", force=force)

    if task_info is not None:
        hist = list(task_info.get("archived_segments") or [])
        hist.extend(archived)
        task_info["archived_segments"] = hist[-200:]
        task_info["last_resegment"] = {
            "stage": stage,
            "archived_ids": parent_ids,
            "new_ids": [_sid(s) for s in fresh],
            "uuid_map": dict(uuid_map),
        }

    logger.info(
        "[ImmutableSegment] resegment archived=%d new=%d stage=%s",
        len(archived),
        len(fresh),
        stage,
    )
    return archived, fresh, uuid_map


def apply_split_reissue_in_place(
    *,
    segments_data: list[dict[str, Any]],
    source_segments: list[str],
    timing_map: list[Any],
    idx: int,
    src_left: str,
    src_right: str,
    tgt_left: str,
    tgt_right: str,
    start0: int,
    end0: int,
    start1: int,
    end1: int,
    audits: list[dict[str, Any]] | None = None,
    task_info: dict[str, Any] | None = None,
    force: bool = False,
) -> bool:
    """Replace one live segment with two NEW segment_ids (archive parent).

    Returns True when the immutable reissue path was applied.
    """
    if not immutable_contract_enabled(force=force):
        return False
    if idx < 0 or idx >= len(segments_data):
        return False

    old = segments_data[idx]
    if not isinstance(old, dict):
        return False

    # Prefer target halves; fall back to source so both NEW ids are always minted.
    left_text = str(tgt_left or "").strip() or str(src_left or "").strip()
    right_text = str(tgt_right or "").strip() or str(src_right or "").strip()
    new_texts = [left_text, right_text]
    new_timing = [
        {"start": start0, "end": end0},
        {"start": start1, "end": end1},
    ]
    archived, fresh, _uuid_map = resegment_archive_and_reissue(
        [old],
        new_texts,
        new_timing,
        stage="adaptive_resegment",
        task_info=task_info,
        force=force,
        extra_fields={
            "adaptive_resegment_done": True,
            "adaptation_stages": list(old.get("adaptation_stages") or [])
            + ["adaptive_resegment"],
        },
    )
    if len(fresh) < 2:
        return False

    fresh[0]["needs_retranslate"] = not bool(str(tgt_left or "").strip())
    fresh[1]["needs_retranslate"] = not bool(str(tgt_right or "").strip())

    # Clear TTS artifacts (new ids must rebind after regen)
    for s in fresh:
        for key in (
            "file",
            "tts_file_path",
            "tts_ms",
            "playback_duration",
            "tts_timing",
            "post_tts_retry",
            "final_tts_text",
            "tts_text",
        ):
            s.pop(key, None)

    segments_data[idx : idx + 1] = fresh

    if idx < len(source_segments):
        source_segments[idx] = src_left
        source_segments.insert(idx + 1, src_right)
    else:
        source_segments.append(src_left)
        source_segments.append(src_right)

    timing_left = {"start": start0, "end": end0}
    timing_right = {"start": start1, "end": end1}
    if idx < len(timing_map):
        timing_map[idx] = timing_left
        timing_map.insert(idx + 1, timing_right)
    else:
        timing_map.append(timing_left)
        timing_map.append(timing_right)

    if audits is not None:
        audit_by = {int(a.get("index", -1)): a for a in audits}
        left_audit = dict(audit_by.get(idx) or {"index": idx})
        left_audit["index"] = idx
        left_audit["whisper_text"] = src_left
        left_audit["segment_id"] = _sid(fresh[0])
        if tgt_left:
            left_audit["final_text"] = tgt_left
        right_audit = dict(audit_by.get(idx) or {"index": idx + 1})
        right_audit["index"] = idx + 1
        right_audit["whisper_text"] = src_right
        right_audit["segment_id"] = _sid(fresh[1])
        if tgt_right:
            right_audit["final_text"] = tgt_right
        else:
            right_audit["final_text"] = ""
            right_audit["needs_retranslate"] = True
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
        "[ImmutableSegment] split idx=%d archived=%s → %s + %s",
        idx,
        archived[0].get("segment_id") if archived else "?",
        _sid(fresh[0]),
        _sid(fresh[1]),
    )
    return True
