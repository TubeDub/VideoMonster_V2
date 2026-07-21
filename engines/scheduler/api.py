"""Scheduler API — sole post-LOCK timing mutator (Freeze TZ P1).

Allowed:
  scheduler.update_time(segments, segment_id, start_ms=..., end_ms=...)
  scheduler.request_time(segments, segment_id, required_ms)

Forbidden:
  segment["start_ms"] = ...
  segment.start_time = ...
"""

from __future__ import annotations

from typing import Any

from engines.pipeline_integrity.pipeline_state import (
    PipelineState,
    advance_pipeline_state,
    get_pipeline_state,
)
from engines.pipeline_integrity.translation_lock import (
    FIELD_OWNERS,
    assert_owner_may_write,
    is_segment_locked,
)
from engines.scheduler.errors import SchedulerError

# Fields Scheduler is allowed to write (Single Owner = Scheduler).
SCHEDULER_TIMING_FIELDS: frozenset[str] = frozenset(
    {
        "start_time",
        "end_time",
        "start_ms",
        "end_ms",
        "place_start",
        "place_delay_ms",
        "lead_in_ms",
        "playback_rate",
        "silence_trim",
        "stretch_factor",
        "timing_meta",
        "overflow",
        "overflow_ms",
        "overflow_pct",
        "slot_overflow",
        "conflict_strategy",
        "conflict_status",
    }
)


def _find_segment(
    segments: list[dict[str, Any]],
    segment_id: str,
) -> dict[str, Any]:
    sid = str(segment_id or "").strip()
    if not sid:
        raise SchedulerError("segment_id is required", field="segment_id")
    for row in segments:
        if not isinstance(row, dict):
            continue
        if str(row.get("segment_id") or "") == sid:
            return row
    raise SchedulerError(
        f"segment not found: {sid}",
        segment_id=sid,
    )


def _coerce_ms(value: Any, *, field: str, segment_id: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise SchedulerError(
            f"invalid {field}: {value!r}",
            segment_id=segment_id,
            field=field,
        ) from exc


class Scheduler:
    """Authoritative timing owner after TRANSLATION LOCK."""

    OWNER = "Scheduler"

    def __init__(self, *, info: dict[str, Any] | None = None) -> None:
        self.info = info
        self.iterations: int = 0

    def update_time(
        self,
        segments: list[dict[str, Any]],
        segment_id: str,
        *,
        start_ms: int | None = None,
        end_ms: int | None = None,
        start_time: float | None = None,
        end_time: float | None = None,
        place_start: int | None = None,
        place_delay_ms: int | None = None,
        lead_in_ms: int | None = None,
        playback_rate: float | None = None,
        silence_trim: float | None = None,
        stretch_factor: float | None = None,
        timing_meta: dict[str, Any] | None = None,
        overflow: bool | None = None,
        overflow_ms: int | None = None,
        overflow_pct: float | None = None,
        slot_overflow: bool | None = None,
        conflict_strategy: str | None = None,
        conflict_status: str | None = None,
    ) -> dict[str, Any]:
        """
        Update timing fields for one segment.

        Only Scheduler-owned fields are accepted. Text fields are rejected.
        """
        seg = _find_segment(segments, segment_id)
        updates: dict[str, Any] = {}

        if start_ms is not None:
            updates["start_ms"] = _coerce_ms(start_ms, field="start_ms", segment_id=segment_id)
        if end_ms is not None:
            updates["end_ms"] = _coerce_ms(end_ms, field="end_ms", segment_id=segment_id)
        if start_time is not None:
            updates["start_time"] = float(start_time)
            if "start_ms" not in updates:
                updates["start_ms"] = int(round(float(start_time) * 1000.0))
        if end_time is not None:
            updates["end_time"] = float(end_time)
            if "end_ms" not in updates:
                updates["end_ms"] = int(round(float(end_time) * 1000.0))
        if place_start is not None:
            updates["place_start"] = _coerce_ms(
                place_start, field="place_start", segment_id=segment_id
            )
        if place_delay_ms is not None:
            updates["place_delay_ms"] = _coerce_ms(
                place_delay_ms, field="place_delay_ms", segment_id=segment_id
            )
        if lead_in_ms is not None:
            updates["lead_in_ms"] = _coerce_ms(
                lead_in_ms, field="lead_in_ms", segment_id=segment_id
            )
        if playback_rate is not None:
            updates["playback_rate"] = float(playback_rate)
        if silence_trim is not None:
            updates["silence_trim"] = float(silence_trim)
        if stretch_factor is not None:
            updates["stretch_factor"] = float(stretch_factor)
        if timing_meta is not None:
            existing = dict(seg.get("timing_meta") or {})
            existing.update(dict(timing_meta))
            updates["timing_meta"] = existing
        if overflow is not None:
            updates["overflow"] = bool(overflow)
        if overflow_ms is not None:
            updates["overflow_ms"] = _coerce_ms(
                overflow_ms, field="overflow_ms", segment_id=segment_id
            )
        if overflow_pct is not None:
            updates["overflow_pct"] = float(overflow_pct)
        if slot_overflow is not None:
            updates["slot_overflow"] = bool(slot_overflow)
        if conflict_strategy is not None:
            updates["conflict_strategy"] = str(conflict_strategy)
        if conflict_status is not None:
            updates["conflict_status"] = str(conflict_status)

        if not updates:
            raise SchedulerError(
                "update_time requires at least one timing field",
                segment_id=segment_id,
            )

        start_v = updates.get("start_ms", seg.get("start_ms"))
        end_v = updates.get("end_ms", seg.get("end_ms"))
        if start_v is not None and end_v is not None:
            if int(end_v) < int(start_v):
                raise SchedulerError(
                    f"end_ms ({end_v}) < start_ms ({start_v})",
                    segment_id=segment_id,
                    field="end_ms",
                )

        for field, value in updates.items():
            if field not in SCHEDULER_TIMING_FIELDS:
                raise SchedulerError(
                    f"field {field!r} is not a Scheduler timing field",
                    segment_id=segment_id,
                    field=field,
                )
            if field in FIELD_OWNERS:
                assert_owner_may_write(field, self.OWNER)
            seg[field] = value

        self.iterations += 1
        self._maybe_mark_scheduled()
        return {
            "segment_id": segment_id,
            "updated": sorted(updates.keys()),
            "start_ms": seg.get("start_ms"),
            "end_ms": seg.get("end_ms"),
            "locked": is_segment_locked(seg),
            "iterations": self.iterations,
        }

    def request_time(
        self,
        segments: list[dict[str, Any]],
        segment_id: str,
        required_ms: int,
        *,
        anchor: str = "start",
    ) -> dict[str, Any]:
        """
        Request a slot of ``required_ms`` for the segment.

        Keeps start (default) or end fixed and adjusts the other edge.
        Does not change text. May mark overflow if the requested window
        cannot be satisfied without colliding (collision resolve is P2).
        """
        seg = _find_segment(segments, segment_id)
        need = _coerce_ms(required_ms, field="required_ms", segment_id=segment_id)
        if need <= 0:
            raise SchedulerError(
                f"required_ms must be > 0, got {need}",
                segment_id=segment_id,
                field="required_ms",
            )

        start = int(seg.get("start_ms") or 0)
        end = int(seg.get("end_ms") or (start + need))
        current = max(0, end - start)

        if anchor == "end":
            new_start = max(0, end - need)
            new_end = end
        else:
            new_start = start
            new_end = start + need

        overflow = need > current and current > 0 and need > current * 1.15
        result = self.update_time(
            segments,
            segment_id,
            start_ms=new_start,
            end_ms=new_end,
            overflow=overflow or None,
            overflow_ms=(need - current) if need > current else 0,
            slot_overflow=overflow or None,
        )
        result["required_ms"] = need
        result["previous_slot_ms"] = current
        result["anchor"] = anchor
        return result

    def _maybe_mark_scheduled(self) -> None:
        if not self.info:
            return
        state = get_pipeline_state(self.info)
        if state == PipelineState.TTS_READY:
            advance_pipeline_state(self.info, PipelineState.SCHEDULED)
        elif state == PipelineState.LOCKED:
            # Allow LOCKED → TTS_READY → SCHEDULED walk when scheduling early
            advance_pipeline_state(self.info, PipelineState.TTS_READY)
            advance_pipeline_state(self.info, PipelineState.SCHEDULED)


_DEFAULT: Scheduler | None = None


def get_scheduler(info: dict[str, Any] | None = None) -> Scheduler:
    """Return a Scheduler bound to optional task info."""
    global _DEFAULT
    if info is not None:
        return Scheduler(info=info)
    if _DEFAULT is None:
        _DEFAULT = Scheduler()
    return _DEFAULT


def update_time(
    segments: list[dict[str, Any]],
    segment_id: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Module-level convenience wrapper."""
    return get_scheduler(kwargs.pop("info", None)).update_time(
        segments, segment_id, **kwargs
    )


def request_time(
    segments: list[dict[str, Any]],
    segment_id: str,
    required_ms: int,
    **kwargs: Any,
) -> dict[str, Any]:
    """Module-level convenience wrapper."""
    return get_scheduler(kwargs.pop("info", None)).request_time(
        segments, segment_id, required_ms, **kwargs
    )
