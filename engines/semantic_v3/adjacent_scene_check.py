"""ЭТАП 9 — Adjacent Scene Revalidation.

When a single MeaningUnit is re-adapted (either because ЭТАП 7 marked
it or because Meaning Fit changed it), the surrounding scene budget can
break: the previous replica may now overlap the current one, or the
next one may be squeezed. This module snapshots the neighbourhood
before the change, runs after the change, and reverts the mutation if
any neighbor's fit *degraded* — the classic "helping one slot damages
another" regression from the TZ.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from typing import Any

from engines.semantic_v3.types import SemanticSentence

logger = logging.getLogger("tubedub.semantic_v3.adjacent_scene_check")


@dataclass
class NeighborSnapshot:
    """Immutable neighborhood record for a single index."""

    index: int
    sentence_uuid: str
    start_ms: int
    end_ms: int
    predicted_tts_ms: int
    translated_text: str
    overflow_ms: int
    underflow_ms: int

    @classmethod
    def capture(cls, index: int, s: SemanticSentence) -> "NeighborSnapshot":
        return cls(
            index=index,
            sentence_uuid=s.sentence_uuid,
            start_ms=s.start_ms,
            end_ms=s.end_ms,
            predicted_tts_ms=s.predicted_tts_ms,
            translated_text=s.translated_text or s.text,
            overflow_ms=s.overflow_ms,
            underflow_ms=s.underflow_ms,
        )


@dataclass
class AdjacentSceneReport:
    changed_index: int
    reverted: bool = False
    reason: str = ""
    prev_snapshot: NeighborSnapshot | None = None
    next_snapshot: NeighborSnapshot | None = None
    scene_budget_before_ms: int = 0
    scene_budget_after_ms: int = 0
    details: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "changed_index": self.changed_index,
            "reverted": self.reverted,
            "reason": self.reason,
            "prev_snapshot": self.prev_snapshot.__dict__ if self.prev_snapshot else None,
            "next_snapshot": self.next_snapshot.__dict__ if self.next_snapshot else None,
            "scene_budget_before_ms": self.scene_budget_before_ms,
            "scene_budget_after_ms": self.scene_budget_after_ms,
            "details": list(self.details),
        }


def snapshot_neighbors(
    sentences: list[SemanticSentence], index: int
) -> tuple[NeighborSnapshot | None, NeighborSnapshot | None, int]:
    """Capture the previous / next neighbor + scene budget before mutation."""
    prev = (
        NeighborSnapshot.capture(index - 1, sentences[index - 1])
        if index > 0
        else None
    )
    nxt = (
        NeighborSnapshot.capture(index + 1, sentences[index + 1])
        if index < len(sentences) - 1
        else None
    )
    lo = prev.start_ms if prev else sentences[index].start_ms
    hi = nxt.end_ms if nxt else sentences[index].end_ms
    scene_budget = max(0, hi - lo)
    return prev, nxt, scene_budget


def _fit_degraded(before: NeighborSnapshot, after: SemanticSentence) -> bool:
    """A neighbor "degraded" if its overflow grew or its slot shrank
    below its predicted TTS duration."""
    if after.overflow_ms > before.overflow_ms + 40:
        return True
    if after.underflow_ms > before.underflow_ms + 200:
        return True
    if before.predicted_tts_ms > 0 and after.slot_ms > 0:
        # Slot shrinkage relative to predicted TTS
        headroom_before = before.end_ms - before.start_ms - before.predicted_tts_ms
        headroom_after = after.slot_ms - after.predicted_tts_ms
        if headroom_before >= 0 and headroom_after < -80:
            return True
    return False


def revalidate_neighbors_or_revert(
    sentences: list[SemanticSentence],
    *,
    changed_index: int,
    original_state: dict[str, Any],
    prev_snapshot: NeighborSnapshot | None,
    next_snapshot: NeighborSnapshot | None,
    scene_budget_before_ms: int,
) -> AdjacentSceneReport:
    """ЭТАП 9 — verify the previous/next slot after re-adaptation.

    If either neighbor's fit degraded, the change at ``changed_index``
    is reverted from ``original_state`` (a dict captured with
    :func:`snapshot_sentence_state`) and the report explains why.
    """
    report = AdjacentSceneReport(
        changed_index=changed_index,
        prev_snapshot=prev_snapshot,
        next_snapshot=next_snapshot,
        scene_budget_before_ms=scene_budget_before_ms,
    )

    lo = prev_snapshot.start_ms if prev_snapshot else sentences[changed_index].start_ms
    hi = next_snapshot.end_ms if next_snapshot else sentences[changed_index].end_ms
    report.scene_budget_after_ms = max(0, hi - lo)

    prev_degraded = False
    next_degraded = False
    if prev_snapshot is not None:
        after = sentences[prev_snapshot.index]
        prev_degraded = _fit_degraded(prev_snapshot, after)
        report.details.append({"neighbor": "prev", "degraded": prev_degraded})
    if next_snapshot is not None:
        after = sentences[next_snapshot.index]
        next_degraded = _fit_degraded(next_snapshot, after)
        report.details.append({"neighbor": "next", "degraded": next_degraded})

    budget_shrunk = (
        scene_budget_before_ms > 0
        and report.scene_budget_after_ms < scene_budget_before_ms - 80
    )
    if budget_shrunk:
        report.details.append(
            {
                "neighbor": "scene_budget",
                "before_ms": scene_budget_before_ms,
                "after_ms": report.scene_budget_after_ms,
                "degraded": True,
            }
        )

    if prev_degraded or next_degraded or budget_shrunk:
        _restore_sentence_state(sentences[changed_index], original_state)
        report.reverted = True
        reasons = []
        if prev_degraded:
            reasons.append("prev_fit_degraded")
        if next_degraded:
            reasons.append("next_fit_degraded")
        if budget_shrunk:
            reasons.append("scene_budget_shrunk")
        report.reason = ",".join(reasons)
        logger.warning(
            "adjacent_scene_check: reverted change at %d because %s",
            changed_index,
            report.reason,
        )
    else:
        report.reason = "adjacent_fit_preserved"
        logger.info(
            "adjacent_scene_check: change at %d preserved (budget %d→%d ms)",
            changed_index,
            scene_budget_before_ms,
            report.scene_budget_after_ms,
        )
    return report


_SNAPSHOT_FIELDS = (
    "translated_text",
    "predicted_tts_ms",
    "overflow_ms",
    "underflow_ms",
    "recovery_plan",
    "text",
    "start_ms",
    "end_ms",
)


def snapshot_sentence_state(sent: SemanticSentence) -> dict[str, Any]:
    """Capture the writable subset of a sentence so it can be reverted."""
    snap: dict[str, Any] = {}
    for field_name in _SNAPSHOT_FIELDS:
        snap[field_name] = copy.copy(getattr(sent, field_name, None))
    snap["target_duration"] = copy.copy(getattr(sent, "target_duration", None))
    snap["adaptation_variants"] = copy.copy(
        getattr(sent, "adaptation_variants", None)
    )
    snap["selected_variant_id"] = getattr(sent, "selected_variant_id", "")
    return snap


def _restore_sentence_state(sent: SemanticSentence, state: dict[str, Any]) -> None:
    for k, v in state.items():
        try:
            setattr(sent, k, v)
        except AttributeError:
            continue
