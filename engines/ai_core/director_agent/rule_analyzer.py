"""Rule-based emotion/style/speed heuristics from text + context."""

from __future__ import annotations

import re
from typing import Any

from engines.ai_core.director_agent.defaults import DEFAULT_BRIEF_VALUES
from engines.ai_core.director_agent.context_window import build_context_window


_HAPPY = re.compile(
    r"\b(great|awesome|wonderful|happy|love|joy|fun|lol|haha|ура|радост|счаст)\b",
    re.I,
)
_SAD = re.compile(
    r"\b(sad|sorry|cry|tears|grief|loss|печал|груст|жаль|сожал)\b",
    re.I,
)
_ANGRY = re.compile(
    r"\b(angry|mad|furious|hate|damn|shut up|злит|бесит|ненавиж)\b",
    re.I,
)
_FEAR = re.compile(
    r"\b(afraid|scared|fear|terror|panic|боюсь|страш|ужас)\b",
    re.I,
)
_EXCITED = re.compile(
    r"\b(wow|amazing|incredible|excited|ура|невероят|потряса)\b",
    re.I,
)
_CALM = re.compile(
    r"\b(calm|peace|quiet|relax|спокой|тишин|мирно)\b",
    re.I,
)
_HUMOR = re.compile(r"\b(joke|funny|lol|haha|смешн|шутк|ирони)\b", re.I)
_SARCASM = re.compile(r"\b(yeah right|sure|as if|конечно|ага|ну да)\b", re.I)
_FORMAL = re.compile(
    r"\b(therefore|furthermore|respectfully|уважаем|следовательно|господ)\b",
    re.I,
)
_NARRATIVE = re.compile(
    r"\b(once upon|years ago|meanwhile|однажды|много лет|тем временем)\b",
    re.I,
)
_DRAMATIC = re.compile(
    r"\b(never again|must|destiny|судьб|никогда|обязан)\b",
    re.I,
)
_COMMAND = re.compile(
    r"^(go|stop|run|listen|do|don't|let's|иди|стой|слушай|делай|не)\b",
    re.I,
)


def _slot_ms(seg: dict[str, Any]) -> int:
    start = seg.get("start")
    end = seg.get("end")
    if start is not None and end is not None:
        return max(1, int(end) - int(start))
    return max(1, int(seg.get("slot_ms") or 3000))


def _detect_emotion(text: str) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if _ANGRY.search(text):
        return "Angry", ["rule:angry_keywords"]
    if _FEAR.search(text):
        return "Fear", ["rule:fear_keywords"]
    if _SAD.search(text):
        return "Sad", ["rule:sad_keywords"]
    if _EXCITED.search(text) or text.count("!") >= 2:
        return "Excited", ["rule:excited_keywords"]
    if _HAPPY.search(text):
        return "Happy", ["rule:happy_keywords"]
    if _CALM.search(text):
        return "Calm", ["rule:calm_keywords"]
    if text.endswith("?"):
        return "Neutral", ["rule:question_neutral"]
    return "Neutral", reasons


def _detect_speech_style(text: str, context: dict[str, Any]) -> tuple[str, list[str]]:
    if _NARRATIVE.search(text):
        return "narrative", ["rule:narrative_markers"]
    if _DRAMATIC.search(text) or text.count("!") >= 1:
        return "dramatic", ["rule:dramatic_markers"]
    if _FORMAL.search(text) or len(text.split()) > 18:
        return "formal", ["rule:formal_markers"]
    if context.get("prev_text") and len(text.split()) <= 8:
        return "conversational", ["rule:short_dialogue"]
    return "conversational", ["rule:default_conversational"]


def _detect_speed(text: str, slot_ms: int) -> tuple[str, list[str]]:
    words = max(1, len(text.split()))
    wps = words / max(slot_ms / 1000.0, 0.1)
    if wps > 3.2 or len(text) > slot_ms / 8:
        return "fast", ["rule:high_word_density"]
    if wps < 1.5 or len(text) < 12:
        return "slow", ["rule:low_word_density"]
    return "normal", ["rule:default_speed"]


def _detect_utterance_goal(text: str) -> tuple[str, list[str]]:
    stripped = text.strip()
    if stripped.endswith("?"):
        return "question", ["rule:question_mark"]
    if stripped.endswith("!"):
        return "exclaim", ["rule:exclamation_mark"]
    if _COMMAND.match(stripped):
        return "command", ["rule:imperative_opening"]
    return "inform", ["rule:default_inform"]


def analyze_segment_rules(
    seg: dict[str, Any],
    *,
    segments: list[dict[str, Any]],
    index: int,
    language: str,
    window: int = 2,
) -> dict[str, Any]:
    """Produce partial brief fields from heuristics."""
    text = str(seg.get("text") or "").strip()
    context = build_context_window(segments, index, window=window)
    slot_ms = _slot_ms(seg)
    speaker = str(seg.get("speaker") or seg.get("speaker_id") or "default")

    emotion, em_reasons = _detect_emotion(text)
    style, style_reasons = _detect_speech_style(text, context)
    speed, speed_reasons = _detect_speed(text, slot_ms)
    goal, goal_reasons = _detect_utterance_goal(text)

    humor = 0.7 if _HUMOR.search(text) else DEFAULT_BRIEF_VALUES["humor"]
    sarcasm = 0.65 if _SARCASM.search(text) else DEFAULT_BRIEF_VALUES["sarcasm"]
    aggression = 0.75 if emotion == "Angry" else DEFAULT_BRIEF_VALUES["aggression"]
    calmness = 0.8 if emotion == "Calm" else DEFAULT_BRIEF_VALUES["calmness"]
    formality = 0.75 if style == "formal" else (0.35 if style == "conversational" else 0.55)
    intensity = 0.8 if emotion in ("Angry", "Excited", "Fear") else 0.35
    if text.count("!") >= 2:
        intensity = min(1.0, intensity + 0.15)

    literal = 0.7 if style == "formal" else 0.4
    deep_adapt = style != "formal" and goal != "inform"
    compression = 0.5 if speed == "fast" else DEFAULT_BRIEF_VALUES["allowed_compression"]
    expansion = 0.4 if speed == "slow" else DEFAULT_BRIEF_VALUES["allowed_expansion"]
    lip_sync = 0.85 if style == "dramatic" else DEFAULT_BRIEF_VALUES["lip_sync_priority"]

    reasons = ["rule_engine"] + em_reasons + style_reasons + speed_reasons + goal_reasons
    if context.get("prev_text"):
        reasons.append("rule:has_prev_context")
    if context.get("next_text"):
        reasons.append("rule:has_next_context")

    preferred_ms = int(slot_ms * (0.92 if speed == "fast" else (1.05 if speed == "slow" else 1.0)))

    return {
        "segment_id": int(seg.get("index", index)),
        "speaker_id": speaker,
        "language": language,
        "emotion": emotion,
        "speech_style": style,
        "speaking_speed": speed,
        "formality": formality,
        "humor": humor,
        "sarcasm": sarcasm,
        "aggression": aggression,
        "calmness": calmness,
        "emotional_intensity": intensity,
        "maximum_duration_ms": slot_ms,
        "preferred_duration_ms": preferred_ms,
        "allowed_compression": compression,
        "allowed_expansion": expansion,
        "adaptation_priority": DEFAULT_BRIEF_VALUES["adaptation_priority"],
        "meaning_priority": DEFAULT_BRIEF_VALUES["meaning_priority"],
        "lip_sync_priority": lip_sync,
        "naturalness_priority": DEFAULT_BRIEF_VALUES["naturalness_priority"],
        "utterance_goal": goal,
        "literal_phrasing_importance": literal,
        "deep_semantic_adaptation_needed": deep_adapt,
        "decision_reasons": reasons,
        "_context_used": bool(context.get("prev_text") or context.get("next_text")),
    }


__all__ = ["analyze_segment_rules"]
