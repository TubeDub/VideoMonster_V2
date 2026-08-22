"""Pipeline state machine — Master Spec Part 1 Foundations v6.0.

Canonical (Part 1):
  NEW → RECOGNIZED → SENTENCE_READY → TRANSLATED → VALIDATED → LOCKED
    → PLANNED → SPEECH_READY → SCHEDULED → MERGED → EXPORTED

Legacy aliases (normalized on parse/advance):
  TRANSCRIBED → RECOGNIZED
  TTS_READY   → SPEECH_READY
  OPTIMIZED   → PLANNED

Optional forward intermediate:
  MERGED → HANDOFF → EXPORTED

Rollback is forbidden.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from engines.pipeline_integrity.exceptions import PipelineStateError


class PipelineState(str, Enum):
    NEW = "NEW"
    RECOGNIZED = "RECOGNIZED"
    SENTENCE_READY = "SENTENCE_READY"
    TRANSLATED = "TRANSLATED"
    VALIDATED = "VALIDATED"
    LOCKED = "LOCKED"
    PLANNED = "PLANNED"
    SPEECH_READY = "SPEECH_READY"
    SCHEDULED = "SCHEDULED"
    MERGED = "MERGED"
    HANDOFF = "HANDOFF"
    EXPORTED = "EXPORTED"
    # Legacy names kept as enum members for isinstance / attribute access;
    # values match canonical targets so storage converges to Spec names.
    TRANSCRIBED = "RECOGNIZED"
    TTS_READY = "SPEECH_READY"
    OPTIMIZED = "PLANNED"


_STATE_ORDER: tuple[PipelineState, ...] = (
    PipelineState.NEW,
    PipelineState.RECOGNIZED,
    PipelineState.SENTENCE_READY,
    PipelineState.TRANSLATED,
    PipelineState.VALIDATED,
    PipelineState.LOCKED,
    PipelineState.PLANNED,
    PipelineState.SPEECH_READY,
    PipelineState.SCHEDULED,
    PipelineState.MERGED,
    PipelineState.HANDOFF,
    PipelineState.EXPORTED,
)

_ORDER_INDEX: dict[PipelineState, int] = {s: i for i, s in enumerate(_STATE_ORDER)}

# String aliases that are not enum member names with identical values
_STRING_ALIASES: dict[str, PipelineState] = {
    "TRANSCRIBED": PipelineState.RECOGNIZED,
    "TTS_READY": PipelineState.SPEECH_READY,
    "OPTIMIZED": PipelineState.PLANNED,
    "RECOGNIZED": PipelineState.RECOGNIZED,
    "SPEECH_READY": PipelineState.SPEECH_READY,
    "PLANNED": PipelineState.PLANNED,
}

ALLOWED_TRANSITIONS: dict[PipelineState, frozenset[PipelineState]] = {
    PipelineState.NEW: frozenset({PipelineState.RECOGNIZED}),
    PipelineState.RECOGNIZED: frozenset(
        {PipelineState.SENTENCE_READY, PipelineState.TRANSLATED}
    ),
    PipelineState.SENTENCE_READY: frozenset({PipelineState.TRANSLATED}),
    PipelineState.TRANSLATED: frozenset({PipelineState.VALIDATED}),
    PipelineState.VALIDATED: frozenset({PipelineState.LOCKED}),
    PipelineState.LOCKED: frozenset({PipelineState.PLANNED, PipelineState.SPEECH_READY}),
    PipelineState.PLANNED: frozenset({PipelineState.SPEECH_READY}),
    PipelineState.SPEECH_READY: frozenset({PipelineState.SCHEDULED}),
    PipelineState.SCHEDULED: frozenset({PipelineState.MERGED}),
    PipelineState.MERGED: frozenset({PipelineState.HANDOFF, PipelineState.EXPORTED}),
    PipelineState.HANDOFF: frozenset({PipelineState.EXPORTED}),
    PipelineState.EXPORTED: frozenset(),
}

# Spec Part 1 canonical path (no optional skips / HANDOFF)
PART1_CANONICAL_PATH: tuple[PipelineState, ...] = (
    PipelineState.NEW,
    PipelineState.RECOGNIZED,
    PipelineState.SENTENCE_READY,
    PipelineState.TRANSLATED,
    PipelineState.VALIDATED,
    PipelineState.LOCKED,
    PipelineState.PLANNED,
    PipelineState.SPEECH_READY,
    PipelineState.SCHEDULED,
    PipelineState.MERGED,
    PipelineState.EXPORTED,
)


def canonicalize_state(state: PipelineState) -> PipelineState:
    """Collapse legacy aliases onto Spec Part 1 members."""
    if state in (
        PipelineState.TRANSCRIBED,
        PipelineState.TTS_READY,
        PipelineState.OPTIMIZED,
    ):
        # Enum aliases share identity with canonical members when values match
        return PipelineState(state.value)
    return state


def parse_pipeline_state(value: Any) -> PipelineState | None:
    if value is None or value == "":
        return None
    if isinstance(value, PipelineState):
        return canonicalize_state(value)
    text = str(value).strip().upper()
    if text in _STRING_ALIASES:
        return _STRING_ALIASES[text]
    try:
        return canonicalize_state(PipelineState(text))
    except ValueError as exc:
        raise PipelineStateError(
            f"unknown pipeline state: {value!r}",
            from_state=str(value),
        ) from exc


def assert_transition(
    from_state: PipelineState | str | None,
    to_state: PipelineState | str,
) -> None:
    src = parse_pipeline_state(from_state) if from_state is not None else PipelineState.NEW
    if src is None:
        src = PipelineState.NEW
    dst = parse_pipeline_state(to_state)
    if dst is None:
        raise PipelineStateError(
            f"invalid target pipeline state: {to_state!r}",
            from_state=str(from_state or ""),
            to_state=str(to_state),
        )
    if src == dst:
        return
    # Compat walks (still forward-only)
    if src == PipelineState.MERGED and dst == PipelineState.EXPORTED:
        return
    if src == PipelineState.RECOGNIZED and dst == PipelineState.TRANSLATED:
        return
    if src == PipelineState.LOCKED and dst == PipelineState.SPEECH_READY:
        return
    if src == PipelineState.PLANNED and dst == PipelineState.SCHEDULED:
        return
    if _ORDER_INDEX[dst] < _ORDER_INDEX[src]:
        raise PipelineStateError(
            f"pipeline rollback forbidden: {src.value} → {dst.value}",
            from_state=src.value,
            to_state=dst.value,
        )
    allowed = ALLOWED_TRANSITIONS.get(src, frozenset())
    if dst not in allowed:
        raise PipelineStateError(
            f"illegal pipeline transition: {src.value} → {dst.value}",
            from_state=src.value,
            to_state=dst.value,
            details={"allowed": sorted(s.value for s in allowed)},
        )


def get_pipeline_state(container: dict[str, Any] | None) -> PipelineState:
    raw = (container or {}).get("pipeline_state")
    if raw is None or raw == "":
        return PipelineState.NEW
    parsed = parse_pipeline_state(raw)
    return parsed if parsed is not None else PipelineState.NEW


def speech_completed(container: dict[str, Any] | None) -> bool:
    """True after TTS produced speech (SPEECH_READY and later)."""
    state = get_pipeline_state(container)
    idx = _ORDER_INDEX.get(state)
    if idx is None:
        return False
    return idx >= _ORDER_INDEX[PipelineState.SPEECH_READY]


def assert_text_change_uses_revision(
    container: dict[str, Any] | None,
    seg: dict[str, Any],
    new_text: str,
    *,
    old_revision: str = "",
) -> None:
    """TTS_DONE (SPEECH_READY+) cannot change text without a new revision UUID."""
    if not speech_completed(container):
        return
    if not isinstance(seg, dict):
        return
    new = " ".join(str(new_text or "").split()).strip()
    old = " ".join(
        str(
            seg.get("plain_text")
            or seg.get("final_text")
            or seg.get("text")
            or ""
        ).split()
    ).strip()
    if not new or new == old:
        return
    rev = str(
        seg.get("adaptation_uuid")
        or seg.get("translation_uuid")
        or seg.get("text_revision_uuid")
        or ""
    ).strip()
    prior = str(old_revision or "").strip()
    if prior and rev == prior:
        raise PipelineStateError(
            "illegal text change after SPEECH_READY without new revision",
            from_state=get_pipeline_state(container).value,
            to_state=get_pipeline_state(container).value,
            details={
                "segment_id": str(seg.get("segment_id") or ""),
                "revision_id": rev,
            },
        )
    if not rev:
        raise PipelineStateError(
            "illegal text change after SPEECH_READY without revision uuid",
            from_state=get_pipeline_state(container).value,
            to_state=get_pipeline_state(container).value,
            details={"segment_id": str(seg.get("segment_id") or "")},
        )


def advance_pipeline_state(
    container: dict[str, Any],
    to_state: PipelineState | str,
    *,
    force: bool = False,
) -> PipelineState:
    dst = parse_pipeline_state(to_state)
    if dst is None:
        raise PipelineStateError(
            f"invalid target pipeline state: {to_state!r}",
            to_state=str(to_state),
        )
    current = get_pipeline_state(container)
    if current == dst:
        container["pipeline_state"] = dst.value
        return dst
    if not force:
        # Legacy: OPTIMIZED/PLANNED stamp after SPEECH_READY is a no-op (already past plan)
        if (
            dst == PipelineState.PLANNED
            and _ORDER_INDEX[current] > _ORDER_INDEX[PipelineState.PLANNED]
        ):
            return current
        # MERGED → HANDOFF → EXPORTED
        if current == PipelineState.MERGED and dst == PipelineState.EXPORTED:
            container["pipeline_state"] = PipelineState.HANDOFF.value
            current = PipelineState.HANDOFF
        # RECOGNIZED → SENTENCE_READY → TRANSLATED (legacy skip)
        if current == PipelineState.RECOGNIZED and dst == PipelineState.TRANSLATED:
            container["pipeline_state"] = PipelineState.SENTENCE_READY.value
            current = PipelineState.SENTENCE_READY
        # LOCKED → PLANNED → SPEECH_READY (legacy TTS without explicit plan stamp)
        if current == PipelineState.LOCKED and dst == PipelineState.SPEECH_READY:
            container["pipeline_state"] = PipelineState.PLANNED.value
            current = PipelineState.PLANNED
        # PLANNED → SPEECH_READY → SCHEDULED (legacy plan→schedule without speech stamp)
        if current == PipelineState.PLANNED and dst == PipelineState.SCHEDULED:
            container["pipeline_state"] = PipelineState.SPEECH_READY.value
            current = PipelineState.SPEECH_READY
        assert_transition(current, dst)
    container["pipeline_state"] = dst.value
    return dst


def is_at_or_after(container: dict[str, Any] | None, state: PipelineState | str) -> bool:
    current = get_pipeline_state(container)
    target = parse_pipeline_state(state)
    if target is None:
        return False
    return _ORDER_INDEX[current] >= _ORDER_INDEX[target]


def assert_no_rollback(from_state: Any, to_state: Any) -> None:
    """Part 1 — reverse transitions are architectural errors."""
    assert_transition(from_state, to_state)
