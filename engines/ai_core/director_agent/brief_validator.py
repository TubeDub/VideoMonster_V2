"""Validate CreativeBrief instances — no empty required fields, no conflicts."""

from __future__ import annotations

from typing import Any

from engines.ai_core.director_agent.creative_brief import CreativeBrief
from engines.ai_core.director_agent.defaults import (
    DEFAULT_BRIEF_VALUES,
    VALID_EMOTIONS,
    VALID_SPEECH_STYLES,
    VALID_SPEAKING_SPEEDS,
    VALID_UTTERANCE_GOALS,
)

_REQUIRED_STRING_FIELDS = ("emotion", "speech_style", "speaking_speed", "utterance_goal", "language", "speaker_id")


def apply_defaults(partial: dict[str, Any]) -> dict[str, Any]:
    """Fill missing brief fields with safe defaults."""
    out = dict(DEFAULT_BRIEF_VALUES)
    out.update({k: v for k, v in partial.items() if v is not None and not str(k).startswith("_")})
    return out


def merge_rule_and_llm(
    rule_fields: dict[str, Any],
    llm_fields: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge rule + LLM; LLM overrides where it returned confident values."""
    merged = apply_defaults(rule_fields)
    if not llm_fields:
        return merged
    for key, value in llm_fields.items():
        if key == "decision_reasons":
            continue
        if value is None:
            continue
        merged[key] = value
    reasons = list(rule_fields.get("decision_reasons") or [])
    if llm_fields:
        reasons.extend(llm_fields.get("decision_reasons") or ["llm_merge"])
    merged["decision_reasons"] = reasons
    return merged


def validate_brief(brief: CreativeBrief | dict[str, Any]) -> tuple[bool, list[str]]:
    """Return (ok, issues). Fixes enum drift inline when given dict."""
    if isinstance(brief, dict):
        b = CreativeBrief.from_dict(brief)
    else:
        b = brief

    issues: list[str] = []
    for field in _REQUIRED_STRING_FIELDS:
        if not str(getattr(b, field, "") or "").strip():
            issues.append(f"empty_required:{field}")

    issues.extend(b.validate_enums())

    if b.preferred_duration_ms > b.maximum_duration_ms:
        issues.append("conflict:preferred_exceeds_maximum")

    if b.allowed_compression + b.allowed_expansion > 1.5:
        issues.append("conflict:compression_expansion_sum_high")

    if b.meaning_priority < 0.5:
        issues.append("conflict:meaning_priority_too_low")

    if b.emotion == "Calm" and b.aggression > 0.6:
        issues.append("conflict:calm_with_high_aggression")

    if b.emotion == "Angry" and b.calmness > 0.7:
        issues.append("conflict:angry_with_high_calmness")

    return len(issues) == 0, issues


def repair_brief(data: dict[str, Any]) -> dict[str, Any]:
    """Apply defaults and clamp invalid enums."""
    merged = apply_defaults(data)
    if merged.get("emotion") not in VALID_EMOTIONS:
        merged["emotion"] = DEFAULT_BRIEF_VALUES["emotion"]
        merged.setdefault("decision_reasons", []).append("repair:emotion")
    if merged.get("speech_style") not in VALID_SPEECH_STYLES:
        merged["speech_style"] = DEFAULT_BRIEF_VALUES["speech_style"]
        merged.setdefault("decision_reasons", []).append("repair:speech_style")
    if merged.get("speaking_speed") not in VALID_SPEAKING_SPEEDS:
        merged["speaking_speed"] = DEFAULT_BRIEF_VALUES["speaking_speed"]
        merged.setdefault("decision_reasons", []).append("repair:speaking_speed")
    if merged.get("utterance_goal") not in VALID_UTTERANCE_GOALS:
        merged["utterance_goal"] = DEFAULT_BRIEF_VALUES["utterance_goal"]
        merged.setdefault("decision_reasons", []).append("repair:utterance_goal")

    max_ms = max(1, int(merged.get("maximum_duration_ms") or 1000))
    pref_ms = max(1, int(merged.get("preferred_duration_ms") or max_ms))
    if pref_ms > max_ms:
        pref_ms = max_ms
        merged.setdefault("decision_reasons", []).append("repair:preferred_duration")
    merged["maximum_duration_ms"] = max_ms
    merged["preferred_duration_ms"] = pref_ms

    ok, issues = validate_brief(merged)
    if not ok:
        merged.setdefault("decision_reasons", []).extend([f"validated:{i}" for i in issues])
    return merged


def validate_all_briefs(briefs: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    """Validate every segment brief."""
    all_ok = True
    all_issues: list[str] = []
    for i, brief in enumerate(briefs):
        repaired = repair_brief(brief)
        briefs[i] = repaired
        ok, issues = validate_brief(repaired)
        if not ok:
            all_ok = False
            all_issues.extend([f"segment_{i}:{issue}" for issue in issues])
    return all_ok, all_issues


__all__ = [
    "apply_defaults",
    "merge_rule_and_llm",
    "repair_brief",
    "validate_all_briefs",
    "validate_brief",
]
