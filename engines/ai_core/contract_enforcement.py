"""AI Core 4.0 — enforce single-responsibility field writes per agent."""

from __future__ import annotations

from typing import Any

# Fields each agent may write on segments (AGENT_CONTRACTS.md).
AGENT_SEGMENT_WRITES: dict[str, frozenset[str]] = {
    "director": frozenset({"creative_brief"}),
    "translation": frozenset({"translated_text", "confidence", "translation_meta"}),
    "semantic": frozenset({
        "semantic_text", "semantic_scores", "semantic_quality_passed",
        "semantic_retry_count", "semantic_model_used", "translation_fallback_reason",
    }),
    "timing": frozenset({"timing_text", "timing_meta", "slot_fit_score", "predicted_ms"}),
    "grammar": frozenset({"grammar_text", "grammar_scores", "grammar_score"}),
    "quality": frozenset({
        "quality_passed", "quality_scores", "quality_issues", "quality_flags",
        "final_text", "reviewer_approved",
    }),
    "reviewer": frozenset({
        "reviewer_approved", "final_text", "voice_input", "reviewer_scores",
    }),
    "voice_preparation": frozenset({
        "voice_input", "final_text", "emotion_tags_present", "voice_prep_emotion",
        "voice_prep_speed", "voice_prep_intensity",
    }),
    "voice": frozenset({"wav_path", "audio_path", "file", "tts_meta"}),
    "voice_verification": frozenset({
        "voice_verification_passed", "voice_verification_score", "voice_verification_issues",
    }),
    "mix": frozenset({"mix_output", "mix_ok"}),
}

# Shared segment keys all agents may touch for diagnostics (not content mutation).
_DIAGNOSTIC_KEYS = frozenset({
    "index", "start", "end", "speaker", "text", "merged_into",
})


def validate_segment_writes(
    agent: str,
    before: dict[str, Any],
    after: dict[str, Any],
) -> list[str]:
    """Return violation messages if agent wrote outside its contract."""
    allowed = AGENT_SEGMENT_WRITES.get(agent)
    if not allowed:
        return []

    violations: list[str] = []
    for key in after:
        if key in _DIAGNOSTIC_KEYS or key in before and before.get(key) == after.get(key):
            continue
        if key not in before or before.get(key) != after.get(key):
            if key not in allowed:
                violations.append(f"forbidden_write:{key}")
    return violations


def validate_all_segment_writes(
    agent: str,
    before_segments: list[dict[str, Any]],
    after_segments: list[dict[str, Any]],
) -> list[str]:
    """Validate contract for all segments after an agent run."""
    issues: list[str] = []
    for i, (b, a) in enumerate(zip(before_segments, after_segments)):
        for msg in validate_segment_writes(agent, b, a):
            issues.append(f"segment_{a.get('index', i)}:{msg}")
    return issues
