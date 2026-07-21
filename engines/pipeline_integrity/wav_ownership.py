"""WAV ownership — P3.1 §6.

Before MERGE → TTS Engine
After Merge → Merge Engine
After Export → Studio
Cleanup only after ownership transfer to a cleanup-eligible owner.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from engines.pipeline_integrity.exceptions import PipelineIntegrityError
from engines.pipeline_integrity.tts_artifact_lifecycle import (
    TTSLifecycleState,
    get_tts_lifecycle,
)


class WavOwner(str, Enum):
    TTS_ENGINE = "TTS Engine"
    MERGE_ENGINE = "Merge Engine"
    STUDIO = "Studio"
    CLEANUP = "Cleanup"


class InvalidOwnerError(PipelineIntegrityError):
    code = "invalid_owner"


class CleanupViolationError(PipelineIntegrityError):
    code = "cleanup_violation"


_OWNER_BY_STATE: dict[TTSLifecycleState, WavOwner] = {
    TTSLifecycleState.CREATED: WavOwner.TTS_ENGINE,
    TTSLifecycleState.QUEUED: WavOwner.TTS_ENGINE,
    TTSLifecycleState.SYNTHESIZING: WavOwner.TTS_ENGINE,
    TTSLifecycleState.SYNTHESIZED: WavOwner.TTS_ENGINE,
    TTSLifecycleState.VERIFIED: WavOwner.TTS_ENGINE,
    TTSLifecycleState.STORED: WavOwner.TTS_ENGINE,
    TTSLifecycleState.SCHEDULED: WavOwner.TTS_ENGINE,
    TTSLifecycleState.MERGED: WavOwner.MERGE_ENGINE,
    TTSLifecycleState.HANDOFF_READY: WavOwner.MERGE_ENGINE,
    TTSLifecycleState.EXPORTED: WavOwner.STUDIO,
    TTSLifecycleState.RELEASED: WavOwner.CLEANUP,
}


def owner_for_lifecycle(state: TTSLifecycleState) -> WavOwner:
    return _OWNER_BY_STATE.get(state, WavOwner.TTS_ENGINE)


def get_wav_owner(seg: dict[str, Any] | None) -> WavOwner:
    raw = str((seg or {}).get("wav_owner") or "").strip()
    if raw:
        for o in WavOwner:
            if o.value == raw or o.name.lower() == raw.lower():
                return o
    return owner_for_lifecycle(get_tts_lifecycle(seg))


def stamp_wav_owner(seg: dict[str, Any]) -> WavOwner:
    owner = owner_for_lifecycle(get_tts_lifecycle(seg))
    seg["wav_owner"] = owner.value
    return owner


def transfer_ownership(
    seg: dict[str, Any],
    to_owner: WavOwner | str,
    *,
    allow_cleanup: bool = False,
) -> WavOwner:
    dst = to_owner if isinstance(to_owner, WavOwner) else WavOwner(str(to_owner))
    cur = get_wav_owner(seg)
    if dst == WavOwner.CLEANUP and not allow_cleanup:
        state = get_tts_lifecycle(seg)
        if state not in (TTSLifecycleState.EXPORTED, TTSLifecycleState.RELEASED):
            raise CleanupViolationError(
                f"cleanup ownership forbidden in state {state.value}",
                stage="ownership",
                details={"segment_uuid": seg.get("segment_uuid"), "state": state.value},
            )
    order = [WavOwner.TTS_ENGINE, WavOwner.MERGE_ENGINE, WavOwner.STUDIO, WavOwner.CLEANUP]
    if order.index(dst) < order.index(cur):
        raise InvalidOwnerError(
            f"ownership rollback forbidden: {cur.value} → {dst.value}",
            stage="ownership",
        )
    seg["wav_owner"] = dst.value
    hist = list(seg.get("runtime_history") or [])
    hist.append({"event": "ownership", "from": cur.value, "to": dst.value})
    seg["runtime_history"] = hist[-50:]
    return dst


def assert_cleanup_allowed(seg: dict[str, Any]) -> None:
    owner = get_wav_owner(seg)
    state = get_tts_lifecycle(seg)
    if owner != WavOwner.CLEANUP and state not in (
        TTSLifecycleState.EXPORTED,
        TTSLifecycleState.RELEASED,
    ):
        raise CleanupViolationError(
            f"cleanup forbidden: owner={owner.value} state={state.value}",
            stage="cleanup",
            details={
                "segment_uuid": seg.get("segment_uuid"),
                "owner": owner.value,
                "state": state.value,
            },
        )
