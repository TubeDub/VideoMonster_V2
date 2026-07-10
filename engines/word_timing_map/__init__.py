"""Word Timing Map — per-word timestamps from STT through dub pipeline."""

from engines.word_timing_map.config import (
    collection_enabled,
    current_phase_label,
    is_enabled,
    optimizer_auto_apply,
    optimizer_enabled,
    sso_fallback_enabled,
    sync_mode,
    whisper_word_timestamps_enabled,
)
from engines.word_timing_map.models import (
    AlignedSegmentMap,
    SegmentWordMap,
    SemanticUnit,
    WordToken,
)
from engines.word_timing_map.alignment_engine import (
    AlignmentEngine,
    HeuristicAlignmentEngine,
    PassthroughAlignmentEngine,
    get_alignment_engine,
)
from engines.word_timing_map.extract import (
    build_segment_word_maps,
    extract_words_from_timing_map,
    proportional_word_split,
)
from engines.word_timing_map.merge import merge_word_maps_with_segments
from engines.word_timing_map.phase0 import (
    WtmCheckpointLog,
    format_dev_inspector_block,
)
from engines.word_timing_map.pipeline import (
    attach_word_maps_to_segments_data,
    build_merged_word_maps,
    build_raw_word_maps,
    persist_task_word_maps,
    save_word_timing_dev_report,
    sync_timing_map_words,
    word_maps_from_task_info,
)

__all__ = [
    "AlignedSegmentMap",
    "AlignmentEngine",
    "HeuristicAlignmentEngine",
    "PassthroughAlignmentEngine",
    "SegmentWordMap",
    "SemanticUnit",
    "WordToken",
    "WtmCheckpointLog",
    "attach_word_maps_to_segments_data",
    "build_merged_word_maps",
    "build_raw_word_maps",
    "build_segment_word_maps",
    "collection_enabled",
    "current_phase_label",
    "extract_words_from_timing_map",
    "format_dev_inspector_block",
    "get_alignment_engine",
    "is_enabled",
    "merge_word_maps_with_segments",
    "optimizer_auto_apply",
    "optimizer_enabled",
    "persist_task_word_maps",
    "proportional_word_split",
    "save_word_timing_dev_report",
    "sso_fallback_enabled",
    "sync_mode",
    "sync_timing_map_words",
    "whisper_word_timestamps_enabled",
    "word_maps_from_task_info",
]
