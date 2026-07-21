"""P104 — Context Graph: enriches each MeaningUnit with full dialogue/scene awareness."""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("tubedub.semantic_v3.context_graph")

_DIALOGUE_MARKERS = re.compile(r"^[—–\-]\s*|[\"«»""'']", re.MULTILINE)
_FORMAL_WORDS = re.compile(
    r"\b(therefore|consequently|furthermore|moreover|однак|тому|відповідно|крім того)\b", re.I
)
_INFORMAL_WORDS = re.compile(
    r"\b(gonna|wanna|kinda|yeah|nah|yep|nope|ok|okay|ну|типу|короче|блін)\b", re.I
)


def build_context_graph(units: list) -> list:
    """Enrich each MeaningUnit with P104 context awareness.

    Accepts list of MeaningUnit objects (imported dynamically to avoid circular deps).
    """
    if not units:
        return units

    # Link prev/next
    for i, unit in enumerate(units):
        if i > 0:
            unit.prev_unit_uuid = units[i - 1].unit_uuid
        if i + 1 < len(units):
            unit.next_unit_uuid = units[i + 1].unit_uuid

    # Build character tracking across units
    all_characters: set[str] = set()
    for unit in units:
        if hasattr(unit, 'speaker') and unit.speaker:
            all_characters.add(unit.speaker)
        for sent in getattr(unit, 'sentences', []):
            if hasattr(sent, 'speaker') and sent.speaker:
                all_characters.add(sent.speaker)
            for ent in getattr(sent, 'entities', []):
                if isinstance(ent, str) and ent:
                    all_characters.add(ent)

    # Track dialogue history window (last 5 units)
    dialogue_window: list[str] = []

    for i, unit in enumerate(units):
        # Active characters: current speaker + entities from this and adjacent units
        active = set()
        if unit.speaker:
            active.add(unit.speaker)
        for sent in getattr(unit, 'sentences', []):
            for ent in getattr(sent, 'entities', []):
                if isinstance(ent, str):
                    active.add(ent)
        # Add speakers from adjacent units
        if i > 0 and units[i - 1].speaker:
            active.add(units[i - 1].speaker)
        if i + 1 < len(units) and units[i + 1].speaker:
            active.add(units[i + 1].speaker)
        unit.active_characters = sorted(active)

        # Speech style detection
        text = getattr(unit, 'text', '') or ''
        unit.speech_style = _detect_speech_style(unit, text)

        # Emotion propagation (inherit from sentences if not set)
        if unit.emotion == "neutral" and getattr(unit, 'sentences', []):
            emotions = [s.emotion for s in unit.sentences if getattr(s, 'emotion', '') and s.emotion != 'neutral']
            if emotions:
                unit.emotion = emotions[0]

        # Topic detection (simplified: first few content words)
        unit.topic = _detect_topic(unit, text)

        # Terminology extraction
        unit.terminology = _extract_terminology(unit)

        # Dialogue history (sliding window of previous texts)
        unit.dialogue_history = list(dialogue_window[-5:])
        snippet = text[:100] if text else ""
        if snippet:
            dialogue_window.append(f"[{unit.speaker or 'unknown'}]: {snippet}")

    logger.info("ContextGraph: enriched %d meaning units, %d characters", len(units), len(all_characters))
    return units


def _detect_speech_style(unit, text: str) -> str:
    """Detect speech style from text content and structure."""
    if not text:
        return ""

    is_dialogue = getattr(unit, 'sentences', []) and any(
        getattr(s, 'is_dialogue', False) or getattr(s, 'is_direct_speech', False)
        for s in unit.sentences
    )

    if is_dialogue:
        return "dialogue"
    if _FORMAL_WORDS.search(text):
        return "formal"
    if _INFORMAL_WORDS.search(text):
        return "informal"

    # Narrative detection: longer sentences without dialogue markers
    if len(text.split()) > 15 and not _DIALOGUE_MARKERS.search(text):
        return "narrative"

    # Single speaker, multiple sentences
    if len(getattr(unit, 'sentences', [])) > 1:
        return "monologue"

    return "narrative"


def _detect_topic(unit, text: str) -> str:
    """Extract a rough topic from content words."""
    if not text:
        return ""

    stop_words = {
        'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
        'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
        'should', 'may', 'might', 'must', 'shall', 'can', 'need', 'dare',
        'to', 'of', 'in', 'for', 'on', 'with', 'at', 'by', 'from', 'as',
        'into', 'through', 'during', 'before', 'after', 'above', 'below',
        'and', 'but', 'or', 'nor', 'not', 'so', 'yet', 'both', 'either',
        'that', 'this', 'it', 'he', 'she', 'they', 'we', 'i', 'you',
        'his', 'her', 'its', 'their', 'our', 'my', 'your',
    }

    words = re.findall(r'\b[a-zA-Zа-яА-ЯіІїЇєЄґҐ]{3,}\b', text.lower())
    content_words = [w for w in words if w not in stop_words][:5]
    return " ".join(content_words) if content_words else ""


def _extract_terminology(unit) -> list[str]:
    """Extract terminology from entities and capitalized multi-word terms."""
    terms: set[str] = set()
    for sent in getattr(unit, 'sentences', []):
        for ent in getattr(sent, 'entities', []):
            if isinstance(ent, str) and ent:
                terms.add(ent)
        for word in getattr(sent, 'words', []):
            if hasattr(word, 'entity_type') and word.entity_type:
                terms.add(word.text)
    return sorted(terms)


def validate_context_graph(units: list) -> dict[str, Any]:
    """P119 validation: ensure context graph is complete."""
    errors = []
    warnings = []

    for i, unit in enumerate(units):
        if i > 0 and not unit.prev_unit_uuid:
            errors.append(f"Unit {unit.unit_uuid}: missing prev link")
        if i + 1 < len(units) and not unit.next_unit_uuid:
            errors.append(f"Unit {unit.unit_uuid}: missing next link")
        if not unit.speech_style:
            warnings.append(f"Unit {unit.unit_uuid}: no speech style detected")

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "units_checked": len(units),
    }
