"""Explain Engine — human-readable reject reasons."""

from __future__ import annotations

from engines.tqe.models import SegmentQualityDecision


def explain_decision(d: SegmentQualityDecision) -> str:
    lines = [
        f"Segment #{d.index + 1}",
        f"Status: {d.status.value}",
    ]
    if d.allowed_for_tts:
        lines.append("Allowed for TTS: yes")
        lines.append(f"Overall Confidence: {d.overall_confidence:.2f}")
        return "\n".join(lines)

    lines.append("Allowed for TTS: NO")
    lines.append(f"Overall Confidence: {d.overall_confidence:.2f}")
    entity_errs = []
    meaning_errs = []
    grammar_errs = []
    for r in d.reports:
        for e in r.errors:
            code = str(e.get("code") or "")
            detail = str(e.get("token") or e.get("detail") or "")
            if "entity" in code or "preserved" in code or "number" in code:
                entity_errs.append(detail or code)
            elif "meaning" in code or "event" in code or "negation" in code or "narrative" in code:
                meaning_errs.append(detail or code)
            elif "grammar" in code or "orphan" in code or "incomplete" in code:
                grammar_errs.append(detail or code)
    if entity_errs:
        lines.append("Missing Entity: " + ", ".join(entity_errs[:8]))
    if meaning_errs:
        lines.append("Meaning Loss: " + ", ".join(meaning_errs[:8]))
    if grammar_errs:
        lines.append("Sentence/Grammar: " + ", ".join(grammar_errs[:8]))
    if d.retry_strategy and d.retry_strategy != "none":
        lines.append(f"Retry: {d.retry_strategy}")
    if d.explanation:
        lines.append(f"Detail: {d.explanation}")
    return "\n".join(lines)
