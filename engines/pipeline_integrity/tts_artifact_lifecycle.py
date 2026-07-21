"""TTS artifact lifecycle — P3.1 full FSM.

CREATED → QUEUED → SYNTHESIZING → SYNTHESIZED → VERIFIED → STORED
  → SCHEDULED → MERGED → HANDOFF_READY → EXPORTED → RELEASED

Reverse transitions are forbidden. Legacy shortcuts (skip SYNTHESIZING / STORED /
HANDOFF_READY) remain allowed for older call sites.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from engines.pipeline_integrity.exceptions import PipelineIntegrityError
from engines.pipeline_integrity.tts_file_lifecycle import log_tts_lifecycle


class TTSLifecycleState(str, Enum):
    CREATED = "Created"
    QUEUED = "Queued"
    SYNTHESIZING = "Synthesizing"
    SYNTHESIZED = "Synthesized"
    VERIFIED = "Verified"
    STORED = "Stored"
    SCHEDULED = "Scheduled"
    MERGED = "Merged"
    HANDOFF_READY = "HandoffReady"
    EXPORTED = "Exported"
    RELEASED = "Released"


_ORDER: tuple[TTSLifecycleState, ...] = (
    TTSLifecycleState.CREATED,
    TTSLifecycleState.QUEUED,
    TTSLifecycleState.SYNTHESIZING,
    TTSLifecycleState.SYNTHESIZED,
    TTSLifecycleState.VERIFIED,
    TTSLifecycleState.STORED,
    TTSLifecycleState.SCHEDULED,
    TTSLifecycleState.MERGED,
    TTSLifecycleState.HANDOFF_READY,
    TTSLifecycleState.EXPORTED,
    TTSLifecycleState.RELEASED,
)
_INDEX = {s: i for i, s in enumerate(_ORDER)}

ALLOWED: dict[TTSLifecycleState, frozenset[TTSLifecycleState]] = {
    TTSLifecycleState.CREATED: frozenset({TTSLifecycleState.QUEUED}),
    TTSLifecycleState.QUEUED: frozenset(
        {TTSLifecycleState.SYNTHESIZING, TTSLifecycleState.SYNTHESIZED}
    ),
    TTSLifecycleState.SYNTHESIZING: frozenset({TTSLifecycleState.SYNTHESIZED}),
    TTSLifecycleState.SYNTHESIZED: frozenset({TTSLifecycleState.VERIFIED}),
    TTSLifecycleState.VERIFIED: frozenset(
        {TTSLifecycleState.STORED, TTSLifecycleState.SCHEDULED}
    ),
    TTSLifecycleState.STORED: frozenset({TTSLifecycleState.SCHEDULED}),
    TTSLifecycleState.SCHEDULED: frozenset({TTSLifecycleState.MERGED}),
    TTSLifecycleState.MERGED: frozenset(
        {TTSLifecycleState.HANDOFF_READY, TTSLifecycleState.RELEASED}
    ),
    TTSLifecycleState.HANDOFF_READY: frozenset({TTSLifecycleState.EXPORTED}),
    TTSLifecycleState.EXPORTED: frozenset({TTSLifecycleState.RELEASED}),
    TTSLifecycleState.RELEASED: frozenset(),
}

# States where cleanup of WAV is still forbidden.
CLEANUP_FORBIDDEN_STATES: frozenset[TTSLifecycleState] = frozenset(
    s for s in _ORDER if s != TTSLifecycleState.RELEASED
)

# Terminal export-finished states that may release ownership for cleanup.
EXPORT_FINISHED_STATES: frozenset[TTSLifecycleState] = frozenset(
    {TTSLifecycleState.EXPORTED, TTSLifecycleState.RELEASED}
)


class TTSLifecycleError(PipelineIntegrityError):
    code = "tts_lifecycle"


def get_tts_lifecycle(seg: dict[str, Any] | None) -> TTSLifecycleState:
    raw = str((seg or {}).get("tts_lifecycle") or "").strip()
    if not raw:
        return TTSLifecycleState.CREATED
    try:
        return TTSLifecycleState(raw)
    except ValueError:
        for s in TTSLifecycleState:
            if s.value.lower() == raw.lower() or s.name.lower() == raw.lower():
                return s
        return TTSLifecycleState.CREATED


def assert_tts_mutable(seg: dict[str, Any], *, action: str = "mutate") -> None:
    state = get_tts_lifecycle(seg)
    if state in (TTSLifecycleState.RELEASED, TTSLifecycleState.EXPORTED):
        raise TTSLifecycleError(
            f"TTS artifact {state.value} — {action} forbidden",
            stage="tts_lifecycle",
            details={
                "segment_id": seg.get("segment_id"),
                "tts_uuid": seg.get("tts_uuid"),
                "state": state.value,
            },
        )


def can_cleanup_wav(seg: dict[str, Any] | None) -> bool:
    """WAV cleanup allowed only after EXPORTED/RELEASED."""
    return get_tts_lifecycle(seg) in EXPORT_FINISHED_STATES


def advance_tts_lifecycle(
    seg: dict[str, Any],
    to_state: TTSLifecycleState | str,
    *,
    task_id: str | None = None,
    force: bool = False,
) -> TTSLifecycleState:
    if isinstance(to_state, TTSLifecycleState):
        dst = to_state
    else:
        raw = str(to_state)
        try:
            dst = TTSLifecycleState(raw)
        except ValueError:
            dst = next(
                (s for s in TTSLifecycleState if s.name.lower() == raw.lower()
                 or s.value.lower() == raw.lower()),
                TTSLifecycleState.CREATED,
            )
    cur = get_tts_lifecycle(seg)
    if cur == dst:
        return cur
    if cur == TTSLifecycleState.RELEASED and not force:
        raise TTSLifecycleError(
            f"cannot advance from Released to {dst.value}",
            stage="tts_lifecycle",
            details={"segment_id": seg.get("segment_id")},
        )
    if not force:
        if _INDEX[dst] < _INDEX[cur]:
            raise TTSLifecycleError(
                f"TTS lifecycle rollback forbidden: {cur.value} → {dst.value}",
                stage="tts_lifecycle",
            )
        if dst not in ALLOWED.get(cur, frozenset()):
            raise TTSLifecycleError(
                f"illegal TTS lifecycle transition: {cur.value} → {dst.value}",
                stage="tts_lifecycle",
                details={"allowed": sorted(s.value for s in ALLOWED.get(cur, frozenset()))},
            )
    seg["tts_lifecycle"] = dst.value
    log_tts_lifecycle(
        task_id,
        event=f"lifecycle_{dst.value.lower()}",
        segment_id=str(seg.get("segment_id") or ""),
        filename=seg.get("tts_file_path") or seg.get("file"),
        stage="tts_lifecycle",
        success=True,
        detail=f"{cur.value}->{dst.value}",
    )
    # History (P3.1 §16)
    hist = list(seg.get("runtime_history") or [])
    hist.append(
        {
            "event": "lifecycle",
            "from": cur.value,
            "to": dst.value,
            "task_id": task_id,
        }
    )
    seg["runtime_history"] = hist[-50:]
    return dst


def advance_toward(
    seg: dict[str, Any],
    target: TTSLifecycleState,
    *,
    task_id: str | None = None,
) -> TTSLifecycleState:
    """Advance along the shortest legal path to ``target`` (no rollback)."""
    cur = get_tts_lifecycle(seg)
    if cur == target:
        return cur
    if _INDEX[target] < _INDEX[cur]:
        raise TTSLifecycleError(
            f"cannot advance_toward earlier state {target.value} from {cur.value}",
            stage="tts_lifecycle",
        )
    # Prefer full path when possible
    preferred = [
        TTSLifecycleState.QUEUED,
        TTSLifecycleState.SYNTHESIZING,
        TTSLifecycleState.SYNTHESIZED,
        TTSLifecycleState.VERIFIED,
        TTSLifecycleState.STORED,
        TTSLifecycleState.SCHEDULED,
        TTSLifecycleState.MERGED,
        TTSLifecycleState.HANDOFF_READY,
        TTSLifecycleState.EXPORTED,
        TTSLifecycleState.RELEASED,
    ]
    for nxt in preferred:
        cur = get_tts_lifecycle(seg)
        if cur == target:
            break
        if _INDEX[nxt] <= _INDEX[cur]:
            continue
        if _INDEX[nxt] > _INDEX[target]:
            break
        if nxt in ALLOWED.get(cur, frozenset()):
            advance_tts_lifecycle(seg, nxt, task_id=task_id)
    cur = get_tts_lifecycle(seg)
    if cur != target and target in ALLOWED.get(cur, frozenset()):
        advance_tts_lifecycle(seg, target, task_id=task_id)
    return get_tts_lifecycle(seg)


def mark_created(seg: dict[str, Any], *, task_id: str | None = None) -> None:
    seg["tts_lifecycle"] = TTSLifecycleState.CREATED.value
    log_tts_lifecycle(
        task_id,
        event="lifecycle_created",
        segment_id=str(seg.get("segment_id") or ""),
        stage="tts_lifecycle",
        success=True,
    )
