"""P6 Translation Validation + P22/P23 Quality scores for Semantic V3."""

from __future__ import annotations

import re
from typing import Any

from engines.semantic_v3.types import SemanticSentence

_NUM = re.compile(r"\d[\d.,]*")


def validate_sentence(
    sent: SemanticSentence,
    *,
    meaning_loss_threshold: float = 0.25,
) -> dict[str, Any]:
    """P6 / P23 — scores only; return to validation on failure (no silent fix)."""
    src = sent.text
    tgt = sent.translated_text or ""
    entity_ok = 1.0
    ents = sent.locked_entities or sent.entities
    if ents and tgt:
        low = tgt.lower()
        entity_ok = sum(1 for e in ents if e.lower() in low) / max(1, len(ents))
    elif ents and not tgt:
        entity_ok = 0.0

    nums_src = set(_NUM.findall(src))
    nums_tgt = set(_NUM.findall(tgt)) if tgt else set()
    num_ok = 1.0 if not nums_src else len(nums_src & nums_tgt) / max(1, len(nums_src))

    completeness = 1.0
    if src and not tgt:
        completeness = 0.0
    elif src and tgt and len(tgt.split()) < max(1, int(len(src.split()) * 0.25)):
        completeness = 0.5

    meaning_score = round(100.0 * (0.5 * entity_ok + 0.3 * num_ok + 0.2 * completeness), 1)
    entity_score = round(100.0 * entity_ok, 1)
    sent.meaning_score = meaning_score
    sent.entity_score = entity_score

    meaning_loss = max(0.0, 1.0 - meaning_score / 100.0)
    ok = meaning_loss <= meaning_loss_threshold and entity_ok >= 0.999
    return {
        "ok": ok,
        "sentence_uuid": sent.sentence_uuid,
        "meaning_score": meaning_score,
        "entity_score": entity_score,
        "meaning_loss": round(meaning_loss, 3),
        "numbers_ok": num_ok >= 0.999,
        "completeness": completeness,
    }


def validate_all(sentences: list[SemanticSentence]) -> dict[str, Any]:
    rows = [validate_sentence(s) for s in sentences]
    failed = [r for r in rows if not r["ok"]]
    return {
        "ok": not failed,
        "checked": len(rows),
        "failed": len(failed),
        "sentences": rows,
    }


def review_payload(sentences: list[SemanticSentence]) -> list[dict[str, Any]]:
    """P22 — Studio-facing scores (no nonsense '43% English words')."""
    out = []
    for s in sentences:
        slot = max(1, s.slot_ms)
        timing = 100.0
        if s.predicted_tts_ms > 0:
            delta = abs(s.predicted_tts_ms - slot) / slot
            timing = max(0.0, 100.0 - delta * 200.0)
        s.timing_score = round(timing, 1)
        out.append(
            {
                "sentence_uuid": s.sentence_uuid,
                "text": s.text,
                "translated_text": s.translated_text,
                "meaning_score": s.meaning_score,
                "entity_score": s.entity_score,
                "timing_score": s.timing_score,
                "speech_score": round(min(100.0, 100.0 * (s.speech_rate and 1.0 or 1.0)), 1),
                "lipsync_score": None,  # filled by P15 when visemes present
                "naturalness_score": round(
                    100.0 if not s.overflow_ms else max(40.0, 100.0 - s.overflow_ms / 50.0),
                    1,
                ),
                "compression": None,
                "expansion": None,
                "tempo": s.speech_rate,
                "stretch": None,
                "borrow": None,
                "sentence_merge": "sentence_merge" in (s.recovery_plan or []),
                "overflow_ms": s.overflow_ms,
                "underflow_ms": s.underflow_ms,
                "recovery_plan": list(s.recovery_plan or []),
            }
        )
    return out
