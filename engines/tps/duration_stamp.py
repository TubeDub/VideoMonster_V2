"""Duration-only DSAL stamp after Approved Text (no text rewrite).

Yellow/red segments get band/delta/match metadata for DubbingEngine timing
strategy (atempo / video_adapt). Text stays `approved_text`.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("tubedub.tps.duration_stamp")


def stamp_duration_after_approved(
    info: dict[str, Any],
    *,
    task_id: str = "",
) -> dict[str, Any]:
    """Analyze slot vs approved text; stamp DSAL fields without rewriting text."""
    from engines.dsal.core import DSALResult, analyze_duration, stamp_dsal_on_segment
    from engines.tps.approved_text import get_approved_text
    from engines.tps.owners import DualWriterError, get_owner_registry

    segments = list(info.get("segments_data") or [])
    tgt = str(info.get("target_lang") or "uk")
    tid = str(task_id or info.get("task_id") or "_")
    owners = get_owner_registry(tid)

    counts = {"green": 0, "yellow": 0, "red": 0, "stamped": 0, "skipped": 0}

    for i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            counts["skipped"] += 1
            continue
        text = get_approved_text(seg)
        if not text:
            counts["skipped"] += 1
            continue

        # Single timing owner — claim once per segment (aliases TimingAgent/DSAL ok)
        try:
            owners.claim("timing_text_adapt", "TimingMeaningFitOwner", segment_index=i)
        except DualWriterError as exc:
            logger.debug("duration stamp dual-writer at %s: %s", i, exc)
            counts["skipped"] += 1
            continue
        except Exception:
            pass

        slot_ms = int(seg.get("slot_ms") or 0)
        if slot_ms <= 0:
            try:
                from engines.timing_fit import _parse_timing

                timing_map = info.get("timing_map_backup") or info.get("timing_map") or []
                if i < len(timing_map):
                    s, e = _parse_timing(timing_map[i])
                    if e > s:
                        slot_ms = max(1, e - s)
                        seg["slot_ms"] = slot_ms
            except Exception:
                pass

        analysis = analyze_duration(slot_ms=slot_ms, text=text, tgt_lang=tgt)
        result = DSALResult(
            text=text,
            changed=False,
            analysis=analysis,
            stages=["tps_duration_stamp"],
            adaptation_executed=False,
            method="duration_only",
            detail="post-APPROVED stamp; text immutable",
            clause_coverage=float(seg.get("clause_coverage") or 1.0),
        )
        stamp_dsal_on_segment(seg, result)
        seg["tps_duration_stamped"] = True
        seg["dsal_skip_reason"] = "tps_duration_only_no_text_rewrite"
        # Yellow/red → flag for audio/video timing strategy (no text rewrite)
        band = str(analysis.band or "").lower()
        if band in ("yellow", "red"):
            seg["timing_strategy_needed"] = True
            if band == "red":
                seg["needs_studio"] = bool(seg.get("needs_studio")) or analysis.duration_match_score < 40
        counts[band if band in counts else "red"] = counts.get(band, 0) + 1
        counts["stamped"] += 1

    info["segments_data"] = segments
    info["tps_duration_stamp"] = counts
    if counts["yellow"] or counts["red"]:
        info["needs_studio"] = bool(info.get("needs_studio")) or bool(counts["red"])
    logger.info(
        "[TPS] duration stamp task=%s stamped=%d green=%d yellow=%d red=%d",
        tid,
        counts["stamped"],
        counts["green"],
        counts["yellow"],
        counts["red"],
    )
    return counts
