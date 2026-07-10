"""Timing helpers for Smart Segment Optimizer."""

from __future__ import annotations

from typing import Any

from engines.smart_segment_optimizer.config import SLOT_MARGIN_MS


def parse_timing_ms(timing: Any) -> tuple[int, int]:
    if isinstance(timing, dict):
        return int(timing.get("start", 0)), int(timing.get("end", 0))
    if isinstance(timing, (list, tuple)) and len(timing) >= 2:
        return int(timing[0]), int(timing[1])
    return 0, 0


def segment_duration_ms(timing: Any) -> int:
    """Full original segment duration (TZ: 5 s slot → target 4.8–5.0 s TTS)."""
    start, end = parse_timing_ms(timing)
    return max(200, end - start)


def allowed_speech_ms(timing: Any, *, margin_ms: int = SLOT_MARGIN_MS) -> int:
    """Hard upper bound for TTS (small safety margin before next segment)."""
    return max(200, segment_duration_ms(timing) - margin_ms)
