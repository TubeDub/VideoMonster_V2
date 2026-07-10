"""Fusion Engine — pick best translation without new MT."""

from __future__ import annotations

from engines.enterprise_translation.entity_manager import EntityManager
from engines.enterprise_translation.exceptions import IntegrityException
from engines.enterprise_translation.types import FusionResult, TournamentCandidate


def fuse_candidates(
    candidates: list[TournamentCandidate],
    entity_manager: EntityManager,
    *,
    min_score: float = 15.0,
) -> FusionResult:
    """
    Select highest-scoring candidate with intact placeholders.
    No hallucination — if all bad, raise IntegrityException.
    """
    valid = [c for c in candidates if c.score >= min_score and c.placeholder_ok and c.text.strip()]
    if not valid:
        valid = [c for c in candidates if c.text.strip() and c.score > 0]

    if not valid:
        raise IntegrityException(
            "Fusion: all engine outputs failed quality/placeholder checks",
            stage="fusion",
            details={
                "candidates": [
                    {"engine": c.engine_id, "score": c.score, "error": c.error}
                    for c in candidates
                ]
            },
        )

    winner = valid[0]
    engine_id = winner.engine_id

    restored, restored_ids, warnings = entity_manager.restore_text(
        winner.text,
        engine_id=engine_id,
        stage="fusion_restore",
    )

    reason_parts = [
        f"winner={engine_id}",
        f"score={winner.score}",
    ]
    if len(valid) > 1:
        reason_parts.append(f"runner_up={valid[1].engine_id}:{valid[1].score}")
    if warnings:
        reason_parts.append(f"restore_warnings={len(warnings)}")

    return FusionResult(
        text=restored,
        winner_engine=engine_id,
        winner_score=winner.score,
        candidates=candidates,
        fusion_reason="; ".join(reason_parts),
        restored_entities=restored_ids,
    )
