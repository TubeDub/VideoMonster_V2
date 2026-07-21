"""RASM R1+ — per-segment sync metrics (source-of-truth timings).

Original = Whisper / reference speech window (start_ms/end_ms).
Dub = post-Scheduler placement + fitted audio duration (not raw pre-fit TTS).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from engines.rasm.config import RasmSettings, default_settings


@dataclass
class SegmentSyncMetrics:
    segment_id: str
    index: int
    original_start_ms: int
    original_end_ms: int
    original_duration_ms: int
    dub_start_ms: int
    dub_end_ms: int
    dub_duration_ms: int
    reserve_ms: int
    overflow_ms: int
    early_ms: int
    late_ms: int
    gap_to_next_ms: int | None
    overlap_with_next: bool
    duration_overflow_ms: int
    placement_overflow_ms: int
    status: str  # green | yellow | red
    flags: list[str] = field(default_factory=list)
    fitted_file_ok: bool = False
    sync_qc: str | None = None  # SYNC_WARNING | SYNC_FAIL | None
    # CATP / RASM: why overflow happened (text vs scheduler/placement)
    overflow_cause: str | None = None  # text | scheduler | placement | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _as_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _segment_id(seg: dict[str, Any], index: int) -> str:
    for key in ("segment_id", "id", "uid"):
        val = seg.get(key)
        if val is not None and str(val).strip():
            return str(val)
    return f"seg_{index}"


def _fitted_ok(seg: dict[str, Any]) -> bool:
    fitted_ms = _as_int(seg.get("fitted_ms") or 0)
    if fitted_ms > 0:
        return True
    if seg.get("fitted_file") or seg.get("file"):
        # File present but duration unknown — still treat as fitted surface
        return True
    return False


def _dub_window(seg: dict[str, Any]) -> tuple[int, int, int, bool]:
    """Return (dub_start, dub_end, dub_duration, fitted_ok)."""
    orig_start = _as_int(seg.get("start_ms") or seg.get("start") or 0)
    place_delay = _as_int(seg.get("place_delay_ms") or seg.get("lead_in_ms") or 0)
    dub_start = orig_start + place_delay

    fitted_ms = _as_int(seg.get("fitted_ms") or 0)
    tts_ms = _as_int(seg.get("tts_ms") or 0)
    # Prefer fitted duration; never use pre-fit TTS alone when fitted exists
    dub_dur = fitted_ms if fitted_ms > 0 else 0
    fitted_ok = _fitted_ok(seg)

    if dub_dur <= 0 and fitted_ok:
        # File exists but fitted_ms missing — fall back to slot length as placement end
        orig_end = _as_int(seg.get("end_ms") or seg.get("end") or orig_start)
        dub_dur = max(0, orig_end - orig_start)

    if dub_dur <= 0 and tts_ms > 0 and not fitted_ok:
        # Explicit: no fitted → NO_FITTED; duration left 0 for red flag
        dub_dur = 0

    dub_end = dub_start + dub_dur
    return dub_start, dub_end, dub_dur, fitted_ok


def compute_segment_metrics(
    seg: dict[str, Any],
    *,
    index: int = 0,
    next_seg: dict[str, Any] | None = None,
    settings: RasmSettings | None = None,
) -> SegmentSyncMetrics:
    cfg = (settings or default_settings()).clamp()
    sid = _segment_id(seg, index)

    original_start = _as_int(seg.get("start_ms") or seg.get("start") or 0)
    original_end = _as_int(seg.get("end_ms") or seg.get("end") or original_start)
    if original_end < original_start:
        original_end = original_start
    original_dur = original_end - original_start

    dub_start, dub_end, dub_dur, fitted_ok = _dub_window(seg)

    reserve_ms = original_end - dub_end
    raw_overflow = max(0, dub_end - original_end)
    # threshold 0 → any positive overflow counts
    overflow_ms = raw_overflow if raw_overflow > cfg.overflow_threshold_ms else 0

    early_ms = max(0, original_start - dub_start)
    late_ms = max(0, dub_start - original_start)
    duration_overflow_ms = max(0, dub_dur - original_dur) if original_dur > 0 else max(0, dub_dur)
    placement_overflow_ms = raw_overflow

    gap_to_next: int | None = None
    overlap = False
    if next_seg is not None:
        n_start, _, _, _ = _dub_window(next_seg)
        gap_to_next = n_start - dub_end
        overlap = dub_end > (n_start + cfg.overlap_epsilon_ms)

    flags: list[str] = []
    if not fitted_ok:
        flags.append("no_fitted")
    if overflow_ms > 0:
        flags.append("overflow")
    if early_ms > cfg.early_start_threshold_ms:
        flags.append("early_start")
    if late_ms > cfg.late_start_threshold_ms:
        flags.append("late_start")
    if gap_to_next is not None and gap_to_next > cfg.gap_threshold_ms:
        flags.append("gap")
    if overlap:
        flags.append("overlap")

    hard_early = early_ms > cfg.early_start_threshold_ms * 2
    hard_late = late_ms > cfg.late_start_threshold_ms * 2
    is_red = (not fitted_ok) or (overflow_ms > 0) or overlap or hard_early or hard_late
    if is_red:
        status = "red"
    elif reserve_ms < cfg.yellow_reserve_ms and reserve_ms >= 0:
        status = "yellow"
        flags.append("tight_reserve")
    elif "early_start" in flags or "late_start" in flags or "gap" in flags:
        status = "yellow"
    else:
        status = "green"

    sync_qc = None
    if status == "red" and (overflow_ms > 250 or overlap or not fitted_ok):
        sync_qc = "SYNC_FAIL"
    elif status in ("red", "yellow") and (
        overflow_ms > 0
        or early_ms > cfg.early_start_threshold_ms
        or late_ms > cfg.late_start_threshold_ms
    ):
        sync_qc = "SYNC_WARNING"

    overflow_cause: str | None = None
    if overflow_ms > 0 or duration_overflow_ms > 0:
        overflow_cause = _attribute_overflow_cause(
            seg,
            original_dur=original_dur,
            dub_dur=dub_dur,
            place_delay=_as_int(seg.get("place_delay_ms") or seg.get("lead_in_ms") or 0),
            overflow_ms=overflow_ms,
        )
        if overflow_cause == "text" and "text_overflow" not in flags:
            flags.append("text_overflow")
        elif overflow_cause == "scheduler" and "scheduler_overflow" not in flags:
            flags.append("scheduler_overflow")

    return SegmentSyncMetrics(
        segment_id=sid,
        index=index,
        original_start_ms=original_start,
        original_end_ms=original_end,
        original_duration_ms=original_dur,
        dub_start_ms=dub_start,
        dub_end_ms=dub_end,
        dub_duration_ms=dub_dur,
        reserve_ms=reserve_ms,
        overflow_ms=overflow_ms,
        early_ms=early_ms,
        late_ms=late_ms,
        gap_to_next_ms=gap_to_next,
        overlap_with_next=overlap,
        duration_overflow_ms=duration_overflow_ms,
        placement_overflow_ms=placement_overflow_ms,
        status=status,
        flags=flags,
        fitted_file_ok=fitted_ok,
        sync_qc=sync_qc,
        overflow_cause=overflow_cause,
    )


def _attribute_overflow_cause(
    seg: dict[str, Any],
    *,
    original_dur: int,
    dub_dur: int,
    place_delay: int,
    overflow_ms: int,
) -> str:
    """Distinguish text-too-long overflow from scheduler/placement issues (CATP/RASM)."""
    text = str(
        seg.get("approved_text")
        or seg.get("final_text")
        or seg.get("text")
        or seg.get("naturalized_text")
        or ""
    )
    tgt = str(seg.get("tgt_lang") or seg.get("target_lang") or "uk")
    est_ms = 0
    if text.strip():
        try:
            from engines.semantic_adaptation import estimate_tts_duration_ms

            est_ms = int(estimate_tts_duration_ms(text, tgt) or 0)
        except Exception:
            est_ms = 0

    # Predicted speech longer than original window → text problem (not Scheduler)
    if original_dur > 0 and est_ms > original_dur + 40:
        return "text"
    catp = seg.get("catp") if isinstance(seg.get("catp"), dict) else {}
    if catp.get("handoff_to_dsal") and overflow_ms > 0:
        return "text"
    if original_dur > 0 and dub_dur > original_dur + 40 and place_delay <= 40:
        # Fitted audio longer than slot with negligible delay → text/TTS length
        return "text"

    # Large place delay pushing past end → scheduler
    if place_delay > 80 and overflow_ms > 0:
        return "scheduler"

    return "placement"


def analyze_segments(
    segments: list[dict[str, Any]],
    *,
    settings: RasmSettings | None = None,
) -> list[SegmentSyncMetrics]:
    cfg = settings or default_settings()
    out: list[SegmentSyncMetrics] = []
    n = len(segments or [])
    for i, seg in enumerate(segments or []):
        if not isinstance(seg, dict):
            continue
        nxt = segments[i + 1] if i + 1 < n and isinstance(segments[i + 1], dict) else None
        out.append(compute_segment_metrics(seg, index=i, next_seg=nxt, settings=cfg))
    return out


def compute_stats(rows: list[SegmentSyncMetrics]) -> dict[str, Any]:
    total = len(rows)
    green = sum(1 for r in rows if r.status == "green")
    yellow = sum(1 for r in rows if r.status == "yellow")
    red = sum(1 for r in rows if r.status == "red")
    reserves = [r.reserve_ms for r in rows]
    overflows = [r.overflow_ms for r in rows if r.overflow_ms > 0]
    earlys = [r.early_ms for r in rows]
    dub_durs = [r.dub_duration_ms for r in rows]
    orig_durs = [r.original_duration_ms for r in rows]

    def _avg(xs: list[int]) -> float:
        return round(sum(xs) / len(xs), 1) if xs else 0.0

    return {
        "segments_total": total,
        "green": green,
        "yellow": yellow,
        "red": red,
        "avg_reserve_ms": _avg(reserves),
        "avg_overflow_ms": _avg(overflows) if overflows else 0.0,
        "max_overflow_ms": max(overflows) if overflows else 0,
        "max_early_ms": max(earlys) if earlys else 0,
        "avg_dub_duration_ms": _avg(dub_durs),
        "avg_original_duration_ms": _avg(orig_durs),
        "sync_fail_count": sum(1 for r in rows if r.sync_qc == "SYNC_FAIL"),
        "sync_warning_count": sum(1 for r in rows if r.sync_qc == "SYNC_WARNING"),
    }
