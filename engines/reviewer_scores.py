"""Reviewer score helpers — slot fit and grammar scores on segment rows."""

from __future__ import annotations

from typing import Any


def compute_slot_fit_for_segment(seg: dict[str, Any], *, tgt_lang: str) -> float:
    """Return slot fit score 0..1 from segment fields or live prediction."""
    stored = seg.get("slot_fit_score")
    if stored is not None:
        try:
            return float(stored)
        except (TypeError, ValueError):
            pass

    slot_ms = seg.get("timing_slot_ms")
    start = seg.get("start")
    end = seg.get("end")
    if slot_ms is None and start is not None and end is not None:
        try:
            slot_ms = int((float(end) - float(start)) * 1000)
        except (TypeError, ValueError):
            slot_ms = None

    text = str(
        seg.get("timing_text")
        or seg.get("grammar_text")
        or seg.get("semantic_text")
        or seg.get("translated_text")
        or ""
    ).strip()
    if not text or not slot_ms:
        return 1.0

    try:
        from engines.ai_core.timing_agent.duration_predictor import predict_duration_ms
        from engines.ai_core.timing_agent.validators.slot_fit_validator import (
            validate_slot_fit,
        )

        predicted = predict_duration_ms(text, tgt_lang)
        return float(validate_slot_fit(predicted, int(slot_ms)).score)
    except Exception:
        return 1.0


def grammar_score_for_segment(seg: dict[str, Any]) -> float:
    scores = seg.get("grammar_scores") or seg.get("quality_scores") or {}
    if isinstance(scores, dict) and scores.get("grammar") is not None:
        try:
            return float(scores["grammar"])
        except (TypeError, ValueError):
            pass
    if seg.get("grammar_score") is not None:
        try:
            return float(seg["grammar_score"])
        except (TypeError, ValueError):
            pass
    return 1.0


def enrich_segment_scores(seg: dict[str, Any], *, tgt_lang: str) -> None:
    """Ensure reviewer audit can read slot_fit / grammar scores from segment row."""
    fit = compute_slot_fit_for_segment(seg, tgt_lang=tgt_lang)
    seg["slot_fit_score"] = round(fit, 4)
    seg["grammar_score"] = round(grammar_score_for_segment(seg), 4)
