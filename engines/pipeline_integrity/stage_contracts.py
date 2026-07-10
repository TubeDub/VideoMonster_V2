"""Stage mutation whitelist — Contract Extensibility (TZ §4)."""

from __future__ import annotations

from typing import FrozenSet

# Fields any stage may read but not write unless listed for that stage.
CORE_IDENTITY_FIELDS: FrozenSet[str] = frozenset({"segment_id", "index"})

STAGE_ALLOWED_MUTATIONS: dict[str, frozenset[str]] = {
    "stt": frozenset({"text", "source_word_map"}),
    "translate": frozenset(
        {
            "text",
            "plain_text",
            "translation_text",
            "emotion",
            "tts_emotion",
            "intonation",
            "prosody",
            "timing_meta",
        }
    ),
    "timing_aware_translation": frozenset(
        {
            "text",
            "plain_text",
            "translation_text",
            "timing_meta",
        }
    ),
    "tts": frozenset(
        {
            "tts_text",
            "tts_file_path",
            "playback_duration",
            "status",
        }
    ),
    "slot_fit": frozenset(
        {
            "file",
            "fitted_file",
            "fitted_ms",
            "start_ms",
            "end_ms",
            "allow_atempo",
            "slot_overflow",
            "overflow_pct",
            "overflow_ms",
            "container_status",
            "timing_meta",
            "place_delay_ms",
            "lead_in_ms",
            "video_stretch_ratio",
            "video_adapt_mode",
            "block_merged_with_next",
            "merge_adjusted_start",
            "merge_adjusted_slot_ms",
            "slot_fit_attempts",
        }
    ),
    "timing": frozenset(
        {
            "place_start",
            "place_delay_ms",
            "lead_in_ms",
            "timing_meta",
            "conflict_strategy",
            "conflict_status",
        }
    ),
    "studio_handoff": frozenset({"container_status", "overflow_pct", "timing_meta"}),
}


def allowed_fields_for_stage(stage: str) -> frozenset[str]:
    base = set(CORE_IDENTITY_FIELDS)
    base.update(STAGE_ALLOWED_MUTATIONS.get(stage, frozenset()))
    return frozenset(base)
