"""P308 Quality Estimator + P309 Decision Score + P310 Confidence."""

from __future__ import annotations

from typing import Any

from engines.decision_policy.config_loader import get_score_weights
from engines.decision_policy.types import StrategyCandidate
from engines.semantic_v3.types import SemanticSentence


def collect_confidences(sent: SemanticSentence) -> dict[str, float]:
    """P310 — aggregate module confidences (read-only)."""
    return {
        "sentence": float(getattr(sent, "sentence_confidence", 1.0) or 1.0),
        "translation": float(
            ((sent.context or {}).get("translation_report") or {}).get("confidence")
            or (1.0 if sent.translated_text else 0.7)
        ),
        "prediction": float(getattr(sent, "prediction_confidence", 0.75) or 0.75),
        "alignment": float(
            0.85
            if any(w.phonemes for w in (sent.words or []))
            else 0.6
        ),
    }


def estimate_quality(
    sent: SemanticSentence,
    candidate: StrategyCandidate,
    *,
    profile: dict[str, Any],
    cfg: dict[str, Any],
) -> dict[str, float]:
    """Score strategy without mutating sentence/audio."""
    slot = max(1, sent.slot_ms)
    expected = int(sent.predicted_tts_ms or sent.estimated_duration or slot)
    overflow = max(0, expected - slot)

    # Simulate fit gain from steps (deterministic heuristics)
    remain = float(overflow)
    tempo_budget = float(profile.get("max_tempo", 1.12) or 1.12)
    for step in candidate.steps:
        if step == "trim_silence":
            remain *= 0.97
        elif step == "pause_optimization":
            remain *= 0.96
        elif step == "prosody":
            remain *= 0.98
        elif step == "tempo":
            remain /= min(tempo_budget, 1.12)
        elif step == "stretch":
            remain *= 0.97
        elif step == "borrow_time":
            remain = max(0.0, remain - min(400.0, remain * 0.5))
        elif step == "sentence_merge":
            remain *= 0.85
        elif step == "semantic_rewrite":
            remain *= 0.75
        elif step == "manual_review":
            remain = 0.0
    candidate.expected_fit = remain <= slot * 0.08

    timing = 100.0 if candidate.expected_fit else max(20.0, 100.0 - remain / max(1.0, slot) * 100)
    meaning = 100.0
    if "semantic_rewrite" in candidate.steps:
        meaning = 92.0 if not sent.semantic_locked else 40.0
    natural = 95.0
    if "stretch" in candidate.steps:
        natural -= 8.0
    if "tempo" in candidate.steps:
        natural -= 3.0
    lip = 90.0 if any(w.visemes for w in (sent.words or [])) else 70.0
    if "stretch" in candidate.steps:
        lip -= 10.0
    context = 90.0 if sent.context_links else 75.0
    emotion = 88.0 if sent.emotion else 80.0
    style = 90.0 if getattr(sent, "style", "") else 80.0
    prosody = 92.0 if "prosody" in candidate.steps or "pause_optimization" in candidate.steps else 85.0
    speech_flow = 93.0 if candidate.expected_fit else 70.0
    # runtime_cost: invert cost (lower cost → higher score)
    max_cost = 100.0
    runtime_cost = max(0.0, 100.0 - (candidate.cost / max_cost) * 100.0)
    confs = collect_confidences(sent)
    pred_conf = 100.0 * sum(confs.values()) / max(1, len(confs))

    return {
        "meaning": round(meaning, 1),
        "naturalness": round(natural, 1),
        "timing": round(timing, 1),
        "lip_sync": round(lip, 1),
        "context": round(context, 1),
        "emotion": round(emotion, 1),
        "style": round(style, 1),
        "prosody": round(prosody, 1),
        "speech_flow": round(speech_flow, 1),
        "runtime_cost": round(runtime_cost, 1),
        "prediction_confidence": round(pred_conf, 1),
    }


def decision_score(
    scores: dict[str, float],
    *,
    cfg: dict[str, Any],
    profile: dict[str, Any],
) -> float:
    weights = get_score_weights(cfg)
    boost = float(profile.get("timing_weight_boost") or 0.0)
    if boost and "timing" in weights:
        weights = dict(weights)
        weights["timing"] = weights["timing"] + boost
        # renormalize lightly
        total = sum(weights.values()) or 1.0
        weights = {k: v / total for k, v in weights.items()}
    total_w = sum(weights.values()) or 1.0
    acc = 0.0
    for k, w in weights.items():
        acc += float(scores.get(k, 50.0)) * (w / total_w)
    return round(acc, 3)
