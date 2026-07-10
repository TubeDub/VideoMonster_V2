"""Structured JSON brief via llm_gateway — NOT free chat."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger("tubedub.ai_core.director_agent.llm")

_STRUCTURED_FIELDS = (
    "emotion",
    "speech_style",
    "speaking_speed",
    "formality",
    "humor",
    "sarcasm",
    "aggression",
    "calmness",
    "emotional_intensity",
    "allowed_compression",
    "allowed_expansion",
    "adaptation_priority",
    "meaning_priority",
    "lip_sync_priority",
    "naturalness_priority",
    "utterance_goal",
    "literal_phrasing_importance",
    "deep_semantic_adaptation_needed",
)

_SYSTEM = (
    "You are a dubbing director. Analyze the segment and return ONLY valid JSON "
    "with these keys: "
    + ", ".join(_STRUCTURED_FIELDS)
    + ". "
    "emotion must be one of: Neutral, Happy, Sad, Angry, Fear, Excited, Calm. "
    "speech_style: conversational, formal, dramatic, narrative. "
    "speaking_speed: slow, normal, fast. "
    "utterance_goal: inform, question, command, exclaim. "
    "Float fields 0-1. deep_semantic_adaptation_needed: boolean. "
    "No markdown, no explanation."
)


def _extract_json(raw: str) -> dict[str, Any] | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def _build_prompt(
    text: str,
    *,
    language: str,
    context: dict[str, Any],
    slot_ms: int,
    speaker_id: str,
) -> str:
    prev = context.get("prev_text") or ""
    nxt = context.get("next_text") or ""
    return (
        f"Segment text ({language}): {text}\n"
        f"Speaker: {speaker_id}\n"
        f"Slot duration ms: {slot_ms}\n"
        f"Previous line: {prev}\n"
        f"Next line: {nxt}\n"
        "Return JSON brief fields only."
    )


def analyze_segment_llm(
    seg: dict[str, Any],
    *,
    context: dict[str, Any],
    language: str,
    task_id: str,
    segment_idx: int,
    timeout: float = 20.0,
) -> tuple[dict[str, Any] | None, bool]:
    """
    Structured LLM brief. Returns (partial_fields, llm_used).
    Never raises — returns (None, False) on failure.
    """
    from engines.ai_core import llm_gateway

    allowed, reason = llm_gateway.can_call_llm(task_id, segment_idx)
    if not allowed:
        return None, False

    text = str(seg.get("text") or "").strip()
    if not text:
        return None, False

    slot_ms = max(1, int(context.get("slot_ms") or 3000))
    speaker = str(seg.get("speaker") or seg.get("speaker_id") or "default")
    prompt = _build_prompt(
        text,
        language=language,
        context=context,
        slot_ms=slot_ms,
        speaker_id=speaker,
    )

    try:
        llm_gateway.set_context(segment=segment_idx, stage="director_brief")
        raw = llm_gateway.chat(
            prompt,
            task_id=task_id,
            segment_idx=segment_idx,
            system=_SYSTEM,
            max_tokens=384,
            temperature=0.1,
            timeout=timeout,
        )
    except Exception as exc:
        logger.debug("Director LLM failed: %s", exc)
        return None, False

    parsed = _extract_json(raw or "")
    if not parsed:
        return None, True

    out: dict[str, Any] = {}
    for key in _STRUCTURED_FIELDS:
        if key in parsed and parsed[key] is not None:
            out[key] = parsed[key]
    if out:
        out["decision_reasons"] = ["llm_structured_brief"]
    return (out if out else None), True


__all__ = ["analyze_segment_llm"]
