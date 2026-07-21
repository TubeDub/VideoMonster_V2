"""Adaptive Segmentation 2.0 — dub-oriented reshaping after Whisper, before MT."""

from engines.adaptive_segmentation.config import AdaptiveSegConfig, load_adaptive_seg_config
from engines.adaptive_segmentation.core import (
    AdaptiveSegResult,
    adapt_source_segments,
    estimate_expected_tts_ms,
    segment_recommendation,
)

__all__ = [
    "AdaptiveSegConfig",
    "AdaptiveSegResult",
    "adapt_source_segments",
    "estimate_expected_tts_ms",
    "load_adaptive_seg_config",
    "segment_recommendation",
]
