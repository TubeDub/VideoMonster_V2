"""Crash Safety / checkpoint resume — Dub Engine Stabilization TZ v2.0 P14.

After any crash, resume from the last successful pipeline state.
Never restart a finished stage from scratch when a checkpoint exists.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from engines.pipeline_integrity.pipeline_state import (
    PipelineState,
    get_pipeline_state,
)

logger = logging.getLogger("tubedub.crash_recovery")

CHECKPOINT_FILENAME = "pipeline_checkpoint.json"


def checkpoint_path(session_dir: Path | str) -> Path:
    return Path(session_dir) / CHECKPOINT_FILENAME


def save_checkpoint(
    session_dir: Path | str,
    info: dict[str, Any],
    *,
    stage: str = "",
) -> Path:
    path = checkpoint_path(session_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "saved_at": time.time(),
        "stage": stage or str(info.get("pipeline_state") or ""),
        "pipeline_state": str(info.get("pipeline_state") or PipelineState.NEW.value),
        "task_id": info.get("task_id"),
        "translation_locked": bool(info.get("translation_locked")),
        "segment_count": len(info.get("segments_data") or []),
        "contract_versions": {
            k: info.get(k)
            for k in (
                "translation_contract_version",
                "dub_contract_version",
                "scheduler_contract_version",
                "studio_contract_version",
                "tts_contract_version",
            )
        },
        # Persist minimal segment identity + audio refs for resume
        "segments": [
            {
                "segment_id": s.get("segment_id"),
                "segment_uuid": s.get("segment_uuid"),
                "tts_uuid": s.get("tts_uuid"),
                "file": s.get("file"),
                "tts_file_path": s.get("tts_file_path"),
                "start_ms": s.get("start_ms"),
                "end_ms": s.get("end_ms"),
                "slot_ms": s.get("slot_ms"),
                "translation_locked": s.get("translation_locked"),
                "tts_lifecycle": s.get("tts_lifecycle"),
                "status": s.get("status"),
            }
            for s in (info.get("segments_data") or [])
            if isinstance(s, dict)
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    info["pipeline_checkpoint"] = str(path)
    logger.info("checkpoint saved: %s state=%s", path, payload["pipeline_state"])
    return path


def load_checkpoint(session_dir: Path | str) -> dict[str, Any] | None:
    path = checkpoint_path(session_dir)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("checkpoint load failed: %s", exc)
        return None


def resume_stage_from_checkpoint(info: dict[str, Any], session_dir: Path | str) -> str:
    """
    Return the next stage to run after crash.

    Does not rewind completed states.
    """
    cp = load_checkpoint(session_dir)
    if not cp:
        return "start"
    state = str(cp.get("pipeline_state") or info.get("pipeline_state") or "NEW")
    info["pipeline_state"] = state
    info["resumed_from_checkpoint"] = True
    info["checkpoint_stage"] = cp.get("stage")
    # Map state → resume entrypoint
    mapping = {
        "NEW": "stt",
        "TRANSCRIBED": "translate",
        "TRANSLATED": "validate",
        "VALIDATED": "lock",
        "LOCKED": "tts",
        "TTS_READY": "schedule",
        "SCHEDULED": "merge",
        "MERGED": "handoff",
        "HANDOFF": "export",
        "EXPORTED": "done",
    }
    nxt = mapping.get(state.upper(), "start")
    logger.info("crash resume: state=%s → next=%s", state, nxt)
    return nxt


def merge_checkpoint_into_segments(
    info: dict[str, Any],
    checkpoint: dict[str, Any],
) -> int:
    """Re-attach audio refs / timing from checkpoint onto current segments_data."""
    by_id = {
        str(s.get("segment_id") or ""): s
        for s in (checkpoint.get("segments") or [])
        if isinstance(s, dict) and s.get("segment_id")
    }
    restored = 0
    for seg in info.get("segments_data") or []:
        if not isinstance(seg, dict):
            continue
        sid = str(seg.get("segment_id") or "")
        src = by_id.get(sid)
        if not src:
            continue
        for key in (
            "file",
            "tts_file_path",
            "start_ms",
            "end_ms",
            "slot_ms",
            "tts_uuid",
            "tts_lifecycle",
            "translation_locked",
        ):
            if src.get(key) is not None and seg.get(key) in (None, "", 0):
                seg[key] = src[key]
                restored += 1
    return restored
