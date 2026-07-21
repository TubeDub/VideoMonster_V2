"""P315 Timeline Planner + P316 Conflict Detector (scene-level, read-only)."""

from __future__ import annotations

from typing import Any

from engines.semantic_v3.types import SemanticSentence


def plan_timeline(sentences: list[SemanticSentence]) -> dict[str, Any]:
    """Plan whole scene/dialogue sequence — not a single line in isolation."""
    by_scene: dict[str, list[str]] = {}
    by_dialogue: dict[str, list[str]] = {}
    total_slot = 0
    total_pred = 0
    for s in sentences:
        sc = getattr(s, "scene_uuid", "") or "scene_default"
        by_scene.setdefault(sc, []).append(s.sentence_uuid)
        dlg = getattr(s, "dialogue_id", "") or ""
        if dlg:
            by_dialogue.setdefault(dlg, []).append(s.sentence_uuid)
        total_slot += s.slot_ms
        total_pred += int(s.predicted_tts_ms or s.estimated_duration or s.slot_ms)
    return {
        "sentence_count": len(sentences),
        "scenes": {k: list(v) for k, v in by_scene.items()},
        "dialogues": {k: list(v) for k, v in by_dialogue.items()},
        "total_slot_ms": total_slot,
        "total_predicted_ms": total_pred,
        "scene_pressure": round(total_pred / max(1, total_slot), 3) if total_slot else 0.0,
    }


def detect_conflicts(sentences: list[SemanticSentence]) -> list[dict[str, Any]]:
    """Overlap / tail spill / merge / scheduler conflict risks — no mutation."""
    conflicts: list[dict[str, Any]] = []
    ordered = sorted(sentences, key=lambda s: s.start_ms)
    for i in range(len(ordered) - 1):
        a, b = ordered[i], ordered[i + 1]
        if a.end_ms > b.start_ms + 40:
            conflicts.append(
                {
                    "type": "overlap",
                    "a": a.sentence_uuid,
                    "b": b.sentence_uuid,
                    "overlap_ms": a.end_ms - b.start_ms,
                }
            )
        # Tail spill risk: words beyond sentence end
        for w in a.words or []:
            if w.end_ms > a.end_ms + 5:
                conflicts.append(
                    {
                        "type": "tail_spill",
                        "sentence_uuid": a.sentence_uuid,
                        "word": w.text,
                    }
                )
                break
        # Neighbor intersection with predicted overflow
        pred_a = int(a.predicted_tts_ms or a.slot_ms)
        if a.start_ms + pred_a > b.start_ms + 40:
            conflicts.append(
                {
                    "type": "neighbor_intersect",
                    "a": a.sentence_uuid,
                    "b": b.sentence_uuid,
                }
            )
        # Merge conflict: different dialogues
        if a.dialogue_id and b.dialogue_id and a.dialogue_id != b.dialogue_id:
            if b.start_ms - a.end_ms < 200:
                conflicts.append(
                    {
                        "type": "merge_conflict",
                        "a": a.sentence_uuid,
                        "b": b.sentence_uuid,
                    }
                )
    return conflicts
