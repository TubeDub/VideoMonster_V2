"""
Conflict Resolver — Stage 3A timing stabilization.

Deterministic, local-only overlap resolution without domino shifts.
Strategies (strict order):
  1. free_window   — shift into free gap before segment
  2. local_shift   — micro shift toward lip-sync anchor
  3. local_reflow  — virtual pause compression (metadata-driven)
  4. safe_stretch  — atempo within safe limits (planning only)
  5. overflow      — mark for manual Studio fix
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.conflict_resolver")

# ── Policy constants (aligned with timing_fit.py) ─────────────────────────────
MIN_GAP_MS = 80
OVERLAP_TOLERANCE_MS = 40
MAX_LOCAL_SHIFT_MS = 250
MAX_EARLY_START_MS = 120
MAX_LATE_START_MS = 180
SAFE_ATEMPO_MAX = 1.05
SAFE_ATEMPO_MIN = 0.95  # TZ v4.0 P2: ±5%
EMERGENCY_ATEMPO_MAX = 1.12  # red overflow after DSAL exhausted
MAX_REFLOW_MS = 185
LOCAL_WINDOW_NEIGHBORS = 1
# Stage 22: force ripple shift of neighbors when placement overlap is severe.
STAGE22_RIPPLE_OVERLAP_MS = 400
# Stage 24: ripple when overlap >80ms; cap single shift; force-clear residual >400.
STAGE23_RIPPLE_OVERLAP_MS = 80
STAGE23_RIPPLE_MAX_SHIFT_MS = 400
STAGE23_FORCE_SPLIT_OVERLAP_MS = 400
STAGE24_ATEMPO_CLAMP_MIN = 0.92
STAGE24_ATEMPO_CLAMP_MAX = 1.08  # Stage 31: never >1.08 / <0.92


@dataclass
class SegmentPlacement:
    idx: int
    original_start_ms: int
    slot_end_ms: int
    place_start_ms: int
    duration_ms: int
    pause_compressed_ms: int = 0
    atempo: float = 1.0
    status: str = "intact"
    strategy: str = "none"
    decision_path: list[str] = field(default_factory=list)

    @property
    def effective_end_ms(self) -> int:
        dur = self.duration_ms
        if self.atempo > 1.001:
            dur = int(round(dur / self.atempo))
        return self.place_start_ms + max(0, dur)

    def lip_delta_ms(self) -> int:
        return abs(self.place_start_ms - self.original_start_ms)


@dataclass
class ConflictResolverResult:
    segments: list[SegmentPlacement]
    overlaps_resolved: int
    strategy_counts: dict[str, int]
    intervention_map: dict[str, Any]
    profile: dict[str, Any]
    decision_traces: list[dict[str, Any]] = field(default_factory=list)

    def to_report_dict(self) -> dict[str, Any]:
        return {
            "overlaps_resolved": self.overlaps_resolved,
            "strategy_counts": self.strategy_counts,
            "intervention_map": self.intervention_map,
            "profile": self.profile,
            "segments": [
                {
                    "idx": s.idx,
                    "original_start_ms": s.original_start_ms,
                    "place_start_ms": s.place_start_ms,
                    "duration_ms": s.duration_ms,
                    "atempo": round(s.atempo, 4),
                    "status": s.status,
                    "strategy": s.strategy,
                    "decision_path": s.decision_path,
                    "lip_delta_ms": s.lip_delta_ms(),
                }
                for s in self.segments
            ],
            "decision_traces": self.decision_traces,
        }


def _debug_enabled() -> bool:
    return os.getenv("VM_DEV_MODE", "").strip().lower() in ("1", "true", "yes", "on") or os.getenv(
        "VM_ARCHITECT_MODE", ""
    ).strip().lower() in ("1", "true", "yes", "on")


def placements_from_fitted(
    fitted_placements: list[dict],
    timing_map: list[Any],
) -> list[SegmentPlacement]:
    """Build resolver input from timing_fit fitted_placements rows."""
    out: list[SegmentPlacement] = []
    for row in sorted(fitted_placements, key=lambda r: int(r.get("idx", 0))):
        idx = int(row["idx"])
        orig = int(row.get("original_start_ms", 0))
        if orig <= 0 and idx < len(timing_map):
            tm = timing_map[idx]
            if isinstance(tm, dict):
                orig = int(tm.get("start", tm.get("start_ms", 0)))
            elif isinstance(tm, (list, tuple)) and len(tm) >= 1:
                orig = int(tm[0])
        slot_end = int(row.get("slot_end_ms", orig))
        if slot_end <= orig and idx < len(timing_map):
            tm = timing_map[idx]
            if isinstance(tm, dict):
                slot_end = int(tm.get("end", tm.get("end_ms", orig + 3000)))
            elif isinstance(tm, (list, tuple)) and len(tm) >= 2:
                slot_end = int(tm[1])
        out.append(
            SegmentPlacement(
                idx=idx,
                original_start_ms=orig,
                slot_end_ms=slot_end,
                place_start_ms=int(row.get("place_start", orig)),
                duration_ms=int(row.get("fitted_ms", 0)),
                pause_compressed_ms=int(row.get("pause_compressed_ms", 0)),
                atempo=float(row.get("atempo", 1.0)),
            )
        )
    return out


def _overlap_ms(a: SegmentPlacement, b: SegmentPlacement) -> int:
    end_a = a.effective_end_ms
    start_b = b.place_start_ms
    return max(0, end_a - start_b + MIN_GAP_MS)


def _prev_end(segments: list[SegmentPlacement], index: int) -> int:
    if index <= 0:
        return 0
    return segments[index - 1].effective_end_ms


def _free_window_before(seg: SegmentPlacement, prev_end: int) -> int:
    return max(0, seg.place_start_ms - max(prev_end + MIN_GAP_MS, 0))


def _score_shift(seg: SegmentPlacement, new_start: int) -> int:
    """Lower is better: lip-sync distance + penalty for lateness."""
    lip = abs(new_start - seg.original_start_ms)
    late_penalty = max(0, new_start - seg.original_start_ms) * 2
    early_penalty = max(0, seg.original_start_ms - new_start)
    return lip + late_penalty + early_penalty // 2


def _try_free_window(
    seg: SegmentPlacement,
    nxt: SegmentPlacement,
    prev_end: int,
    overlap: int,
) -> bool:
    window = _free_window_before(seg, prev_end)
    if window <= 0:
        seg.decision_path.append("free_window:skip(no_gap)")
        return False
    shift = min(window, overlap, MAX_LOCAL_SHIFT_MS)
    if shift <= 0:
        seg.decision_path.append("free_window:skip(insufficient)")
        return False
    new_start = seg.place_start_ms - shift
    if new_start < seg.original_start_ms - MAX_LOCAL_SHIFT_MS:
        seg.decision_path.append("free_window:skip(lip_limit)")
        return False
    seg.place_start_ms = new_start
    seg.strategy = "free_window"
    seg.status = "shift"
    seg.decision_path.append(f"free_window:shift_left={shift}ms")
    return _overlap_ms(seg, nxt) <= OVERLAP_TOLERANCE_MS


def _try_local_shift(
    seg: SegmentPlacement,
    nxt: SegmentPlacement,
    prev_end: int,
    overlap: int,
) -> bool:
    candidates: list[tuple[int, str]] = []
    for delta in range(10, MAX_LOCAL_SHIFT_MS + 1, 10):
        left = seg.place_start_ms - delta
        if left >= prev_end + MIN_GAP_MS and left >= seg.original_start_ms - MAX_LOCAL_SHIFT_MS:
            candidates.append((left, f"left_{delta}"))
        right = seg.place_start_ms + delta
        if right <= seg.original_start_ms + MAX_LATE_START_MS:
            candidates.append((right, f"right_{delta}"))
    if not candidates:
        seg.decision_path.append("local_shift:skip(no_candidates)")
        return False
    candidates.sort(key=lambda c: (_score_shift(seg, c[0]), c[0]))
    for new_start, label in candidates:
        old = seg.place_start_ms
        seg.place_start_ms = new_start
        if _overlap_ms(seg, nxt) <= OVERLAP_TOLERANCE_MS:
            seg.strategy = "local_shift"
            seg.status = "shift"
            seg.decision_path.append(f"local_shift:{label} from={old} to={new_start}")
            return True
        seg.place_start_ms = old
    seg.decision_path.append("local_shift:fail")
    return False


def _try_local_reflow(seg: SegmentPlacement, nxt: SegmentPlacement, overlap: int) -> bool:
    available = MAX_REFLOW_MS - seg.pause_compressed_ms
    if available <= 0:
        seg.decision_path.append("local_reflow:skip(no_headroom)")
        return False
    save = min(available, overlap + OVERLAP_TOLERANCE_MS)
    if save < 20:
        seg.decision_path.append("local_reflow:skip(too_small)")
        return False
    seg.duration_ms = max(1, seg.duration_ms - save)
    seg.pause_compressed_ms += save
    seg.strategy = "local_reflow"
    seg.status = "reflow"
    seg.decision_path.append(f"local_reflow:save={save}ms")
    return _overlap_ms(seg, nxt) <= OVERLAP_TOLERANCE_MS


def _try_safe_stretch(seg: SegmentPlacement, nxt: SegmentPlacement, overlap: int) -> bool:
    available = nxt.place_start_ms - MIN_GAP_MS - seg.place_start_ms
    if available <= 0:
        seg.decision_path.append("safe_stretch:skip(no_window)")
        return False
    need = seg.duration_ms / max(available, 1)
    if need <= 1.001:
        seg.decision_path.append("safe_stretch:skip(already_fits)")
        return False
    cap = SAFE_ATEMPO_MAX
    # Severe overflow vs available window → emergency ±12% before Studio mark.
    if need > SAFE_ATEMPO_MAX and need <= EMERGENCY_ATEMPO_MAX:
        cap = EMERGENCY_ATEMPO_MAX
        seg.decision_path.append(f"safe_stretch:emergency_cap={cap}")
    if need > cap:
        seg.decision_path.append(f"safe_stretch:skip(need={need:.3f}>{cap})")
        return False
    seg.atempo = min(cap, max(seg.atempo, need))
    seg.strategy = "safe_stretch" if cap <= SAFE_ATEMPO_MAX else "safe_stretch_emergency"
    seg.status = "stretch"
    seg.decision_path.append(f"safe_stretch:atempo={seg.atempo:.3f}")
    return _overlap_ms(seg, nxt) <= OVERLAP_TOLERANCE_MS


def _resolve_pair(
    segments: list[SegmentPlacement],
    i: int,
    *,
    traces: list[dict[str, Any]] | None = None,
) -> bool:
    """Resolve overlap between segments[i] and segments[i+1]. Returns True if resolved."""
    if i + 1 >= len(segments):
        return True
    cur = segments[i]
    nxt = segments[i + 1]
    overlap = _overlap_ms(cur, nxt)
    if overlap <= OVERLAP_TOLERANCE_MS:
        return True

    prev_end = _prev_end(segments, i)
    cur.decision_path.append(f"conflict:overlap={overlap}ms with idx={nxt.idx}")
    t0 = time.perf_counter()

    if _try_free_window(cur, nxt, prev_end, overlap):
        _record_trace(traces, cur, "free_window", t0)
        return True
    if _try_local_shift(cur, nxt, prev_end, overlap):
        _record_trace(traces, cur, "local_shift", t0)
        return True
    if _try_local_reflow(cur, nxt, overlap):
        _record_trace(traces, cur, "local_reflow", t0)
        return True
    if _try_safe_stretch(cur, nxt, overlap):
        _record_trace(traces, cur, "safe_stretch", t0)
        return True

    # Stage 23: overlap >300ms → ripple (max shift 400); residual >400 → force clear.
    if overlap > STAGE23_RIPPLE_OVERLAP_MS and _try_ripple_shift_neighbors(
        segments, i
    ):
        _record_trace(traces, nxt, "ripple_shift", t0)
        return True

    cur.status = "overflow"
    cur.strategy = "overflow"
    cur.decision_path.append("overflow:unresolved")
    _record_trace(traces, cur, "overflow", t0)
    return False


def _try_ripple_shift_neighbors(segments: list[SegmentPlacement], i: int) -> bool:
    """Push following segments forward until overlap with i is cleared (cascade)."""
    if i + 1 >= len(segments):
        return False
    cur = segments[i]
    nxt = segments[i + 1]
    if _overlap_ms(cur, nxt) <= STAGE23_RIPPLE_OVERLAP_MS:
        return False
    for j in range(i, len(segments) - 1):
        a = segments[j]
        b = segments[j + 1]
        ov = _overlap_ms(a, b)
        if ov <= OVERLAP_TOLERANCE_MS:
            continue
        desired = a.effective_end_ms + MIN_GAP_MS
        if desired <= b.place_start_ms:
            continue
        old = b.place_start_ms
        shift = desired - old
        # Prefer capped shift; always clear residual so overlaps die.
        if shift > STAGE23_RIPPLE_MAX_SHIFT_MS:
            capped = old + STAGE23_RIPPLE_MAX_SHIFT_MS
            b.place_start_ms = capped
            b.status = "shift"
            b.strategy = "ripple_shift"
            b.decision_path.append(f"stage23:ripple_shift_capped {old}->{capped}")
            residual = _overlap_ms(a, b)
            if residual > OVERLAP_TOLERANCE_MS:
                b.place_start_ms = desired
                b.decision_path.append(
                    f"stage23:ripple_force_clear {capped}->{desired}"
                )
                if residual > STAGE23_FORCE_SPLIT_OVERLAP_MS or shift > (
                    STAGE23_RIPPLE_MAX_SHIFT_MS + STAGE23_FORCE_SPLIT_OVERLAP_MS
                ):
                    longer = a if a.duration_ms >= b.duration_ms else b
                    longer.decision_path.append(
                        "stage23:force_split_longest_offender"
                    )
        else:
            b.place_start_ms = desired
            b.status = "shift"
            b.strategy = "ripple_shift"
            b.decision_path.append(f"stage23:ripple_shift {old}->{desired}")
    return _overlap_ms(cur, nxt) <= OVERLAP_TOLERANCE_MS


def _record_trace(
    traces: list[dict[str, Any]] | None,
    seg: SegmentPlacement,
    outcome: str,
    t0: float,
) -> None:
    if traces is None:
        return
    traces.append(
        {
            "idx": seg.idx,
            "outcome": outcome,
            "strategy": seg.strategy,
            "place_start_ms": seg.place_start_ms,
            "duration_ms": seg.duration_ms,
            "atempo": round(seg.atempo, 4),
            "decision_path": list(seg.decision_path),
            "resolve_ms": round((time.perf_counter() - t0) * 1000.0, 3),
        }
    )


def _fix_unreasonable_gap(seg: SegmentPlacement, prev_end: int) -> None:
    """Close micro-gaps only when free window exists (no domino)."""
    gap = seg.place_start_ms - prev_end
    if gap <= MIN_GAP_MS or gap <= 200:
        return
    if seg.original_start_ms <= prev_end + MIN_GAP_MS:
        return
    target = max(prev_end + MIN_GAP_MS, seg.original_start_ms)
    if target < seg.place_start_ms and seg.lip_delta_ms() > abs(seg.place_start_ms - target):
        old = seg.place_start_ms
        seg.place_start_ms = target
        seg.decision_path.append(f"gap_close:{old}->{target}")
        if seg.status == "intact":
            seg.status = "shift"
            seg.strategy = "gap_close"


def resolve_conflicts(
    segments: list[SegmentPlacement],
    *,
    collect_traces: bool | None = None,
) -> ConflictResolverResult:
    """
    Resolve all local timing conflicts. Segments sorted by idx (deterministic).
    Never shifts segment i+1 to fix segment i (no domino).
    """
    if not segments:
        empty_map = {
            "intact_pct": 100.0,
            "shift_only_pct": 0.0,
            "stretch_only_pct": 0.0,
            "overflow_pct": 0.0,
            "timeline_drift_ms": 0,
        }
        return ConflictResolverResult(
            segments=[],
            overlaps_resolved=0,
            strategy_counts={},
            intervention_map=empty_map,
            profile={"total_ms": 0.0, "avg_segment_ms": 0.0, "max_segment_ms": 0.0},
        )

    ordered = sorted(segments, key=lambda s: (s.place_start_ms, s.idx))
    traces: list[dict[str, Any]] = [] if (collect_traces if collect_traces is not None else _debug_enabled()) else []

    t_total = time.perf_counter()
    per_seg_times: list[float] = []
    overlaps_resolved = 0

    for i, seg in enumerate(ordered):
        prev_end = _prev_end(ordered, i)
        _fix_unreasonable_gap(seg, prev_end)

        if seg.place_start_ms < seg.original_start_ms - MAX_EARLY_START_MS:
            seg.place_start_ms = seg.original_start_ms - MAX_EARLY_START_MS
            seg.decision_path.append("lip_policy:clamp_early")
            if seg.status == "intact":
                seg.status = "shift"
                seg.strategy = "lip_clamp"

        if seg.place_start_ms > seg.original_start_ms + MAX_LATE_START_MS:
            new_start = seg.original_start_ms + MAX_LATE_START_MS
            if new_start >= prev_end + MIN_GAP_MS:
                seg.place_start_ms = new_start
                seg.decision_path.append("lip_policy:clamp_late")
                if seg.status == "intact":
                    seg.status = "shift"
                    seg.strategy = "lip_clamp"

        if i + 1 < len(ordered):
            t0 = time.perf_counter()
            before = _overlap_ms(seg, ordered[i + 1])
            if before > OVERLAP_TOLERANCE_MS:
                if _resolve_pair(ordered, i, traces=traces):
                    after = _overlap_ms(seg, ordered[i + 1])
                    if after <= OVERLAP_TOLERANCE_MS:
                        overlaps_resolved += 1
            per_seg_times.append((time.perf_counter() - t0) * 1000.0)

    total_ms = (time.perf_counter() - t_total) * 1000.0
    n = len(ordered)
    strategy_counts: dict[str, int] = {
        "free_window": 0,
        "local_shift": 0,
        "local_reflow": 0,
        "safe_stretch": 0,
        "ripple_shift": 0,
        "overflow": 0,
        "intact": 0,
    }
    for seg in ordered:
        if seg.status == "overflow":
            strategy_counts["overflow"] += 1
        elif seg.status == "intact":
            strategy_counts["intact"] += 1
        elif seg.strategy in strategy_counts:
            strategy_counts[seg.strategy] += 1
        elif seg.status == "stretch":
            strategy_counts["safe_stretch"] += 1
        elif seg.status == "shift":
            strategy_counts["local_shift"] += 1

    intact = strategy_counts["intact"]
    shift_only = (
        strategy_counts["free_window"]
        + strategy_counts["local_shift"]
        + strategy_counts["ripple_shift"]
    )
    stretch_only = strategy_counts["safe_stretch"] + strategy_counts["local_reflow"]
    overflow = strategy_counts["overflow"]

    drift = 0
    if ordered:
        last = ordered[-1]
        drift = last.effective_end_ms - (last.slot_end_ms or last.original_start_ms)

    intervention_map = {
        "total_segments": n,
        "intact_pct": round(100.0 * intact / max(n, 1), 2),
        "shift_only_pct": round(100.0 * shift_only / max(n, 1), 2),
        "stretch_only_pct": round(100.0 * stretch_only / max(n, 1), 2),
        "overflow_pct": round(100.0 * overflow / max(n, 1), 2),
        "timeline_drift_ms": int(drift),
        "overlaps_resolved": overlaps_resolved,
    }

    profile = {
        "total_ms": round(total_ms, 3),
        "avg_segment_ms": round(sum(per_seg_times) / max(len(per_seg_times), 1), 3),
        "max_segment_ms": round(max(per_seg_times) if per_seg_times else 0.0, 3),
        "segment_count": n,
    }

    return ConflictResolverResult(
        segments=ordered,
        overlaps_resolved=overlaps_resolved,
        strategy_counts=strategy_counts,
        intervention_map=intervention_map,
        profile=profile,
        decision_traces=traces,
    )


def ripple_shift_segment_dicts(
    segments: list[dict[str, Any]],
    *,
    overlap_trigger_ms: int = STAGE23_RIPPLE_OVERLAP_MS,
    min_gap_ms: int = MIN_GAP_MS,
    clear_all_above_ms: int | None = OVERLAP_TOLERANCE_MS,
    max_shift_ms: int = STAGE23_RIPPLE_MAX_SHIFT_MS,
    force_clear_above_ms: int = STAGE23_FORCE_SPLIT_OVERLAP_MS,
) -> dict[str, Any]:
    """Stage 23: push later segments when placement overlap is severe.

    Mutates ``merge_adjusted_start`` / ``start_time_ms`` / ``start_ms`` so both
    the mix builder and OpenDDF overlap diagnostics see a non-overlapping timeline.

    - Force-shift when overlap > ``overlap_trigger_ms`` (default 80, Stage 24).
    - Cap single shift at ``max_shift_ms`` (400); if residual still >
      ``force_clear_above_ms``, uncapped clear + mark longest offender for split
      or atempo clamp 0.92–1.12.
    - Optionally also clear residual overlaps above ``clear_all_above_ms``.
    """
    if not segments:
        return {
            "ripple_shifted": 0,
            "severe_shifted": 0,
            "overlap_after_ripple": 0,
            "overlap_count": 0,
            "atempo_marked": 0,
        }
    active = [
        (i, s)
        for i, s in enumerate(segments)
        if isinstance(s, dict) and s.get("merged_into") is None
    ]
    shifted = 0
    severe = 0
    force_split_marked = 0
    atempo_marked = 0

    def _start(seg: dict) -> int:
        return int(
            seg.get("merge_adjusted_start")
            or seg.get("start_ms")
            or seg.get("start_time_ms")
            or 0
        )

    def _dur(seg: dict) -> int:
        return int(
            seg.get("final_tts_duration_ms")
            or seg.get("tts_ms")
            or seg.get("playback_duration")
            or seg.get("actual_duration_ms")
            or 0
        )

    def _set_start(seg: dict, new_start: int, old: int) -> None:
        seg["merge_adjusted_start"] = int(new_start)
        if "start_ms" in seg or seg.get("start_ms") is not None:
            seg["start_ms"] = int(new_start)
        if "start_time_ms" in seg or seg.get("start_time_ms") is not None:
            seg["start_time_ms"] = int(new_start)
        seg["stage22_ripple_shift_ms"] = int(new_start - old)
        seg["stage23_ripple_shift_ms"] = int(new_start - old)
        seg["placement_overlap"] = False

    for pass_trigger in (overlap_trigger_ms, clear_all_above_ms):
        if pass_trigger is None:
            continue
        for k in range(len(active) - 1):
            _ia, a = active[k]
            _ib, b = active[k + 1]
            dur_a = _dur(a)
            if dur_a <= 0:
                continue
            start_a = _start(a)
            start_b = _start(b)
            ov = (start_a + dur_a) - start_b
            if ov <= int(pass_trigger):
                continue
            desired = start_a + dur_a + int(min_gap_ms)
            if desired <= start_b:
                continue
            shift = desired - start_b
            if (
                pass_trigger == overlap_trigger_ms
                and max_shift_ms > 0
                and shift > int(max_shift_ms)
            ):
                capped = start_b + int(max_shift_ms)
                _set_start(b, capped, start_b)
                shifted += 1
                severe += 1
                b["stage22_ripple_severe"] = True
                b["stage23_ripple_capped"] = True
                residual = (start_a + dur_a) - _start(b)
                if residual > int(force_clear_above_ms):
                    _set_start(b, desired, _start(b))
                    longer = a if _dur(a) >= _dur(b) else b
                    longer["needs_post_restore_split"] = True
                    longer["stage23_force_split_overlap"] = True
                    # Stage 24: also request atempo clamp when overflow still severe.
                    longer["atempo_clamp_min"] = STAGE24_ATEMPO_CLAMP_MIN
                    longer["atempo_clamp_max"] = STAGE24_ATEMPO_CLAMP_MAX
                    longer["needs_atempo_clamp"] = True
                    force_split_marked += 1
                    atempo_marked += 1
            else:
                _set_start(b, desired, start_b)
                shifted += 1
                if ov > overlap_trigger_ms:
                    severe += 1
                    b["stage22_ripple_severe"] = True

    # Count residual overlaps after ripple (for stage23/24 meta).
    overlap_after = 0
    for k in range(len(active) - 1):
        _ia, a = active[k]
        _ib, b = active[k + 1]
        dur_a = _dur(a)
        if dur_a <= 0:
            continue
        ov = (_start(a) + dur_a) - _start(b)
        if ov > OVERLAP_TOLERANCE_MS:
            overlap_after += 1
            b["overlap_after_ripple"] = 1
            a["overlap_after_ripple"] = int(a.get("overlap_after_ripple") or 0)
            # Never leave >80ms placement overlap without a shift (Stage 31).
            if ov > STAGE23_RIPPLE_OVERLAP_MS:
                desired = _start(a) + dur_a + int(min_gap_ms)
                if desired > _start(b):
                    _set_start(b, desired, _start(b))
                    shifted += 1
        else:
            b["overlap_after_ripple"] = 0

    # Re-count after late clear.
    overlap_after = 0
    for k in range(len(active) - 1):
        _ia, a = active[k]
        _ib, b = active[k + 1]
        dur_a = _dur(a)
        if dur_a <= 0:
            continue
        ov = (_start(a) + dur_a) - _start(b)
        if ov > OVERLAP_TOLERANCE_MS:
            overlap_after += 1
            b["overlap_after_ripple"] = 1
        else:
            b["overlap_after_ripple"] = 0

    return {
        "ripple_shifted": shifted,
        "severe_shifted": severe,
        "overlap_trigger_ms": overlap_trigger_ms,
        "max_shift_ms": max_shift_ms,
        "overlap_after_ripple": overlap_after,
        "overlap_count": overlap_after,
        "force_split_marked": force_split_marked,
        "atempo_marked": atempo_marked,
    }


def apply_resolver_to_fitted(
    fitted_placements: list[dict],
    fitted_for_mix: list[tuple],
    timing_map: list[Any],
    *,
    task_id: str | None = None,
    session_dir: str | Path | None = None,
) -> ConflictResolverResult:
    """
    Run resolver on fitted placements and update place_start in fitted_for_mix.
    fitted_for_mix: list of (path, place_start, fitted_ms)
    """
    segments = placements_from_fitted(fitted_placements, timing_map)
    if not segments:
        return resolve_conflicts([])

    for seg in segments:
        row = next((r for r in fitted_placements if int(r.get("idx", -1)) == seg.idx), None)
        if row:
            row.setdefault("original_start_ms", seg.original_start_ms)
            row.setdefault("slot_end_ms", seg.slot_end_ms)

    result = resolve_conflicts(segments)

    idx_to_start = {s.idx: s.place_start_ms for s in result.segments}
    idx_to_meta = {s.idx: s for s in result.segments}

    for i, row in enumerate(fitted_placements):
        idx = int(row.get("idx", i))
        if idx in idx_to_start:
            place = idx_to_start[idx]
            sid = str(row.get("segment_id") or "").strip()
            if sid:
                from engines.scheduler import update_time

                update_time([row], sid, place_start=place)
            else:
                # Intermediate mix row without identity — place_start only until
                # segment_id is bound; architecture tests allow this path via
                # conflict_resolver intermediate tables.
                row["place_start"] = place
            meta = idx_to_meta.get(idx)
            if meta:
                row["conflict_strategy"] = meta.strategy
                row["conflict_status"] = meta.status
                row["atempo"] = meta.atempo
                if meta.status == "overflow":
                    row["overflow_ms"] = max(
                        int(row.get("overflow_ms", 0)),
                        OVERLAP_TOLERANCE_MS + 1,
                    )

    if len(fitted_for_mix) == len(fitted_placements):
        sorted_rows = sorted(fitted_placements, key=lambda r: int(r.get("idx", 0)))
        updated: list[tuple] = []
        for j, row in enumerate(sorted_rows):
            path, _, fitted_ms = fitted_for_mix[j]
            updated.append((path, int(row["place_start"]), fitted_ms))
        fitted_for_mix[:] = updated

    if _debug_enabled() and (session_dir or task_id):
        write_resolver_report(result, task_id=task_id, session_dir=session_dir)

    if task_id:
        try:
            from engines.dubbing_engine.project_session import get_session

            sess = get_session(task_id)
            if sess is not None:
                sess.set("conflict_resolver", result.intervention_map)
                sess.set("conflict_resolver_profile", result.profile)
                sess.set("conflict_strategy_counts", result.strategy_counts)
        except ImportError:
            pass

    logger.info(
        "conflict_resolver task=%s resolved=%d intact=%.1f%% overflow=%.1f%% drift=%dms",
        task_id or "-",
        result.overlaps_resolved,
        result.intervention_map.get("intact_pct", 0),
        result.intervention_map.get("overflow_pct", 0),
        result.intervention_map.get("timeline_drift_ms", 0),
    )
    return result


def write_resolver_report(
    result: ConflictResolverResult,
    *,
    task_id: str | None = None,
    session_dir: str | Path | None = None,
    app_dir: Path | None = None,
) -> Path | None:
    """Write conflict_resolver_report.json for developer diagnostics."""
    base = Path(session_dir) if session_dir else None
    if base is None and task_id:
        try:
            from engines.dub_task_state import AUTO_TASKS, STATE_LOCK

            with STATE_LOCK:
                task = AUTO_TASKS.get(task_id)
                if task:
                    sd = (task.get("info") or {}).get("session_dir")
                    if sd:
                        base = Path(str(sd))
        except ImportError:
            pass
    if base is None:
        root = app_dir or Path(__file__).resolve().parent.parent
        base = root / "output" / "dev"
    base.mkdir(parents=True, exist_ok=True)
    path = base / "conflict_resolver_report.json"
    payload = result.to_report_dict()
    payload["task_id"] = task_id
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
