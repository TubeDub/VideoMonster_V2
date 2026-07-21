"""Semantic block merge for DSAL (TZ v4.0 P1).

Merges 2–3 consecutive red/yellow segments into one duration budget,
adapts combined text, then redistributes sentences back to members.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from engines.dsal.core import adapt_duration_semantic, analyze_duration, stamp_dsal_on_segment

MAX_BLOCK_SIZE = 3
MIN_BLOCK_SIZE = 2


@dataclass
class BlockMergePlan:
    indices: list[int]
    block_slot_ms: int
    reason: str


@dataclass
class BlockMergeResult:
    plans: list[BlockMergePlan] = field(default_factory=list)
    merged_blocks: int = 0
    adapted_segments: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "merged_blocks": self.merged_blocks,
            "adapted_segments": self.adapted_segments,
            "plans": [
                {
                    "indices": p.indices,
                    "block_slot_ms": p.block_slot_ms,
                    "reason": p.reason,
                }
                for p in self.plans
            ],
        }


def _seg_text(seg: dict[str, Any]) -> str:
    return str(
        seg.get("final_text")
        or seg.get("translation_text")
        or seg.get("text")
        or seg.get("plain_text")
        or ""
    ).strip()


def _seg_source(seg: dict[str, Any], src_segs: list[str], idx: int) -> str:
    if idx < len(src_segs) and src_segs[idx]:
        return str(src_segs[idx])
    return str(seg.get("source_text") or seg.get("original_text") or "")


def _seg_slot(seg: dict[str, Any]) -> int:
    return max(0, int(seg.get("slot_ms") or 0))


def _band_for_seg(seg: dict[str, Any], tgt_lang: str) -> str:
    band = str(seg.get("dsal_band") or "")
    if band:
        return band
    text = _seg_text(seg)
    slot = _seg_slot(seg)
    actual = int(seg.get("tts_ms") or seg.get("playback_duration") or 0) or None
    if slot <= 0 or not text:
        return "green"
    return analyze_duration(
        slot_ms=slot, text=text, tgt_lang=tgt_lang, actual_tts_ms=actual
    ).band


def _spare_capacity_ms(seg: dict[str, Any], tgt_lang: str) -> int:
    """Positive = room to absorb overflow from a neighbor (underflow)."""
    text = _seg_text(seg)
    slot = _seg_slot(seg)
    if slot <= 0 or not text:
        return 0
    actual = int(seg.get("tts_ms") or seg.get("playback_duration") or 0) or None
    a = analyze_duration(
        slot_ms=slot, text=text, tgt_lang=tgt_lang, actual_tts_ms=actual
    )
    return max(0, int(a.delta_ms))


def _overflow_ms(seg: dict[str, Any], tgt_lang: str) -> int:
    text = _seg_text(seg)
    slot = _seg_slot(seg)
    if slot <= 0 or not text:
        return 0
    actual = int(seg.get("tts_ms") or seg.get("playback_duration") or 0) or None
    a = analyze_duration(
        slot_ms=slot, text=text, tgt_lang=tgt_lang, actual_tts_ms=actual
    )
    return max(0, -int(a.delta_ms))


def detect_block_candidates(
    segments: list[dict[str, Any]],
    *,
    tgt_lang: str = "uk",
) -> list[BlockMergePlan]:
    """Find chains of 2–3 consecutive yellow/red segments for semantic merge.

    Also merges a red overflow segment with a neighbor that has spare capacity
    (even if that neighbor is green) so duration can be redistributed.
    """
    plans: list[BlockMergePlan] = []
    n = len(segments)
    i = 0
    while i < n - 1:
        seg = segments[i]
        if not isinstance(seg, dict) or seg.get("merged_into") is not None:
            i += 1
            continue
        band = _band_for_seg(seg, tgt_lang)
        if band not in ("yellow", "red"):
            i += 1
            continue

        chain = [i]
        j = i + 1
        while j < n and len(chain) < MAX_BLOCK_SIZE:
            nxt = segments[j]
            if not isinstance(nxt, dict) or nxt.get("merged_into") is not None:
                break
            nb = _band_for_seg(nxt, tgt_lang)
            # yellow/red always; green only when primary overflows and next has spare
            if nb in ("yellow", "red"):
                chain.append(j)
                j += 1
                continue
            if (
                band == "red"
                and _overflow_ms(seg, tgt_lang) >= 400
                and _spare_capacity_ms(nxt, tgt_lang) >= 400
            ):
                chain.append(j)
                j += 1
                continue
            break

        if len(chain) >= MIN_BLOCK_SIZE:
            slot_sum = sum(_seg_slot(segments[k]) for k in chain)
            if slot_sum > 0:
                plans.append(
                    BlockMergePlan(
                        indices=chain,
                        block_slot_ms=slot_sum,
                        reason=f"dsal_bands:{','.join(_band_for_seg(segments[k], tgt_lang) for k in chain)}",
                    )
                )
                i = chain[-1] + 1
                continue
        i += 1
    return plans


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?…])\s+", " ".join(str(text or "").split()))
    return [p.strip() for p in parts if p.strip()]


def _redistribute(text: str, slots: list[int], n: int) -> list[str]:
    """Split adapted block text across n segments proportional to slot lengths."""
    sents = _split_sentences(text)
    if n <= 1:
        return [text]
    if not sents:
        return [text] + [""] * (n - 1)
    if len(sents) <= n:
        # Pad / assign one sentence each then leftovers to last
        out = [""] * n
        for i, s in enumerate(sents):
            out[min(i, n - 1)] = (
                (out[min(i, n - 1)] + " " + s).strip() if out[min(i, n - 1)] else s
            )
        return out

    total = max(1, sum(max(1, s) for s in slots))
    targets = [max(1, round(len(sents) * max(1, slots[i]) / total)) for i in range(n)]
    # Fix rounding so sum == len(sents)
    while sum(targets) > len(sents):
        k = max(range(n), key=lambda i: targets[i])
        if targets[k] > 1:
            targets[k] -= 1
        else:
            break
    while sum(targets) < len(sents):
        k = max(range(n), key=lambda i: slots[i] if i < len(slots) else 1)
        targets[k] += 1

    out: list[str] = []
    cursor = 0
    for t in targets:
        chunk = sents[cursor : cursor + t]
        cursor += t
        out.append(" ".join(chunk).strip())
    # Leftover sentences → last
    if cursor < len(sents):
        out[-1] = (out[-1] + " " + " ".join(sents[cursor:])).strip()
    return out


def apply_semantic_block_merges(
    segments: list[dict[str, Any]],
    *,
    source_segments: list[str] | None = None,
    tgt_lang: str = "uk",
) -> BlockMergeResult:
    """Merge 2–3 candidate segs, DSAL-adapt as a block, redistribute text."""
    from engines.translation_validation import apply_translated_text_to_segment

    src_segs = list(source_segments or [])
    plans = detect_block_candidates(segments, tgt_lang=tgt_lang)
    result = BlockMergeResult(plans=plans)
    used: set[int] = set()

    for plan in plans:
        if any(i in used for i in plan.indices):
            continue
        members = [segments[i] for i in plan.indices]
        texts = [_seg_text(s) for s in members]
        sources = [_seg_source(s, src_segs, i) for i, s in zip(plan.indices, members)]
        slots = [_seg_slot(s) for s in members]
        if not any(texts) or plan.block_slot_ms <= 0:
            continue

        combined_uk = " ".join(t for t in texts if t)
        combined_en = " ".join(s for s in sources if s)
        dsal = adapt_duration_semantic(
            combined_uk,
            source_hint=combined_en,
            slot_ms=plan.block_slot_ms,
            tgt_lang=tgt_lang,
        )
        parts = _redistribute(dsal.text, slots, len(members))
        primary = plan.indices[0]
        for local, (idx, seg, part) in enumerate(
            zip(plan.indices, members, parts)
        ):
            used.add(idx)
            if part:
                apply_translated_text_to_segment(seg, part)
            stamp_dsal_on_segment(
                seg,
                adapt_duration_semantic(
                    part or _seg_text(seg),
                    source_hint=sources[local],
                    slot_ms=slots[local],
                    tgt_lang=tgt_lang,
                ),
            )
            seg["block_merge_semantic"] = {
                "primary": primary,
                "members": list(plan.indices),
                "block_slot_ms": plan.block_slot_ms,
                "reason": plan.reason,
                "adapted": bool(dsal.changed or part != texts[local]),
            }
            if idx != primary:
                seg["block_merged_with_prev"] = primary
            else:
                seg["block_merged_with_next"] = (
                    plan.indices[1] if len(plan.indices) > 1 else None
                )
            result.adapted_segments += 1

        result.merged_blocks += 1

    return result
