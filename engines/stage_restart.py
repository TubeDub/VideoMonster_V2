"""Spec v3 per-stage restart via OpenDDF lineage.

Persists a compact snapshot of each pipeline stage (STT / diarization /
translation / TTS) so a failed run can resume from the last completed stage
instead of restarting from source video.

Storage layout::

    <session_dir>/openddf_stages/<stage>.json
    <session_dir>/openddf_stages/manifest.json   ← ordered stage log

Snapshots are JSON-only. Heavy binaries (audio/models) already live under the
session dir and are referenced by absolute path — never inlined.

Public API::

    save_stage(session_dir, stage, payload, *, run_id="")
    load_stage(session_dir, stage) -> dict | None
    resume_from(session_dir) -> str | None           # next stage id to run
    list_stages(session_dir) -> list[dict]           # manifest entries
    reset_from(session_dir, stage) -> int            # drop stage + everything after
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.stage_restart")

STAGES: tuple[str, ...] = (
    "extract",
    "source_separation",
    "stt",
    "diarization",
    "translate",
    "semantic_gate",
    "timing",
    "tts",
    "post_tts_qa",
    "mux",
)

MANIFEST_NAME = "manifest.json"
DIR_NAME = "openddf_stages"


@dataclass
class StageRecord:
    stage: str
    ok: bool
    ts: float = field(default_factory=lambda: time.time())
    run_id: str = ""
    duration_ms: int = 0
    error: str | None = None
    payload_file: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _stages_dir(session_dir: str | Path) -> Path:
    p = Path(session_dir) / DIR_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def _read_manifest(session_dir: str | Path) -> list[dict[str, Any]]:
    mf = _stages_dir(session_dir) / MANIFEST_NAME
    if not mf.is_file():
        return []
    try:
        data = json.loads(mf.read_text(encoding="utf-8"))
        return list(data) if isinstance(data, list) else []
    except Exception as exc:
        logger.debug("stage_restart: manifest read failed: %s", exc)
        return []


def _write_manifest(session_dir: str | Path, entries: list[dict[str, Any]]) -> None:
    mf = _stages_dir(session_dir) / MANIFEST_NAME
    try:
        mf.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.debug("stage_restart: manifest write failed: %s", exc)


def save_stage(
    session_dir: str | Path,
    stage: str,
    payload: dict[str, Any] | None = None,
    *,
    run_id: str = "",
    ok: bool = True,
    duration_ms: int = 0,
    error: str | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> StageRecord:
    """Persist stage payload + append manifest entry. Idempotent per stage."""
    stage = str(stage or "").strip().lower()
    if not stage:
        raise ValueError("stage_restart.save_stage: stage id required")

    d = _stages_dir(session_dir)
    payload_path: str | None = None
    if payload is not None:
        pf = d / f"{stage}.json"
        try:
            pf.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            payload_path = str(pf.resolve())
        except Exception as exc:
            logger.debug("stage_restart: %s payload write failed: %s", stage, exc)

    rec = StageRecord(
        stage=stage,
        ok=bool(ok),
        run_id=str(run_id or ""),
        duration_ms=int(duration_ms or 0),
        error=error,
        payload_file=payload_path,
        diagnostics=dict(diagnostics or {}),
    )
    manifest = _read_manifest(session_dir)
    # Replace an existing entry for this stage (idempotent restart).
    manifest = [m for m in manifest if str(m.get("stage") or "").lower() != stage]
    manifest.append(rec.to_dict())
    manifest.sort(key=lambda m: STAGES.index(m["stage"]) if m.get("stage") in STAGES else 999)
    _write_manifest(session_dir, manifest)
    logger.info(
        "stage_restart: saved stage=%s ok=%s payload=%s",
        stage,
        rec.ok,
        payload_path,
    )
    return rec


def load_stage(session_dir: str | Path, stage: str) -> dict[str, Any] | None:
    """Return the persisted payload for ``stage`` or None."""
    d = _stages_dir(session_dir)
    pf = d / f"{stage.strip().lower()}.json"
    if not pf.is_file():
        return None
    try:
        return json.loads(pf.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.debug("stage_restart: load %s failed: %s", stage, exc)
        return None


def list_stages(session_dir: str | Path) -> list[dict[str, Any]]:
    return _read_manifest(session_dir)


def last_completed_stage(session_dir: str | Path) -> str | None:
    """Return the id of the last stage marked ok=True (or None)."""
    completed = [m for m in _read_manifest(session_dir) if m.get("ok")]
    if not completed:
        return None
    completed.sort(
        key=lambda m: STAGES.index(m["stage"]) if m.get("stage") in STAGES else -1
    )
    return str(completed[-1]["stage"]) if completed else None


def resume_from(session_dir: str | Path) -> str | None:
    """Return the id of the next stage to execute, or None if fresh run."""
    last = last_completed_stage(session_dir)
    if not last or last not in STAGES:
        return None
    idx = STAGES.index(last)
    if idx + 1 >= len(STAGES):
        return None
    return STAGES[idx + 1]


def reset_from(session_dir: str | Path, stage: str) -> int:
    """Drop stage payload and all subsequent stages so they re-run.

    Returns number of stages removed. The session directory itself is preserved.
    """
    stage = str(stage or "").strip().lower()
    if stage not in STAGES:
        return 0
    start = STAGES.index(stage)
    dropped = 0
    d = _stages_dir(session_dir)
    manifest = _read_manifest(session_dir)
    keep: list[dict[str, Any]] = []
    for m in manifest:
        sid = str(m.get("stage") or "").lower()
        if sid in STAGES and STAGES.index(sid) >= start:
            pf = d / f"{sid}.json"
            if pf.is_file():
                try:
                    pf.unlink()
                    dropped += 1
                except OSError:
                    pass
        else:
            keep.append(m)
    _write_manifest(session_dir, keep)
    logger.info("stage_restart: reset from %s → dropped=%s", stage, dropped)
    return dropped


def stage_index(stage: str) -> int:
    """Return numeric position (0-based) or -1 if unknown."""
    s = str(stage or "").strip().lower()
    return STAGES.index(s) if s in STAGES else -1


def is_stage_complete(session_dir: str | Path, stage: str) -> bool:
    for m in _read_manifest(session_dir):
        if str(m.get("stage") or "").lower() == stage.lower() and m.get("ok"):
            return True
    return False
