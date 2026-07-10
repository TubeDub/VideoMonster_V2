"""
Smart Segment Optimizer V2 — safe pre-TTS segment shortening.

Pipeline slot: after Final Translation, before TTS.
Does NOT modify Translation Pipeline or related modules.

Disable: VM_SMART_SEGMENT_OPTIMIZER=0
"""

from engines.smart_segment_optimizer.config import is_enabled
from engines.smart_segment_optimizer.optimizer import (
    SegmentOptimizeResult,
    optimize_segment,
    optimize_segments,
    slot_ms_from_timing,
)

__all__ = [
    "is_enabled",
    "SegmentOptimizeResult",
    "optimize_segment",
    "optimize_segments",
    "slot_ms_from_timing",
]
