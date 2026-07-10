"""TTS artifact lifecycle logging — generation, write, verify, integrity handoff."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.tts_lifecycle")


def _basename(value: str | Path | None) -> str | None:
    if not value:
        return None
    return Path(str(value)).name


def log_tts_lifecycle(
    task_id: str | None,
    *,
    event: str,
    segment_id: str | None = None,
    segment_index: int | None = None,
    filename: str | Path | None = None,
    path: str | Path | None = None,
    stage: str | None = None,
    success: bool | None = None,
    exists: bool | None = None,
    detail: str | None = None,
) -> None:
    """Structured log line for each TTS file lifecycle transition."""
    parts = [
        f"task={task_id or '?'}",
        f"event={event}",
    ]
    if stage:
        parts.append(f"stage={stage}")
    if segment_id:
        parts.append(f"segment_id={segment_id}")
    if segment_index is not None:
        parts.append(f"idx={segment_index}")
    name = _basename(filename)
    if name:
        parts.append(f"file={name}")
    if path is not None:
        parts.append(f"path={path}")
    if success is not None:
        parts.append(f"success={success}")
    if exists is not None:
        parts.append(f"exists={exists}")
    if detail:
        parts.append(f"detail={detail}")
    logger.info("[TTS-LC] %s", " ".join(parts))


def verify_tts_file_on_disk(
    path: str | Path,
    *,
    task_id: str | None = None,
    segment_id: str | None = None,
    segment_index: int | None = None,
    filename: str | Path | None = None,
    stage: str | None = None,
    event: str = "verify_exists",
) -> bool:
    """Log file existence after write or before handoff."""
    p = Path(path)
    exists = p.is_file()
    log_tts_lifecycle(
        task_id,
        event=event,
        segment_id=segment_id,
        segment_index=segment_index,
        filename=filename or p.name,
        path=p,
        stage=stage,
        exists=exists,
        success=exists,
    )
    return exists


def safe_unlink_replaced_segment_audio(
    artifacts_dir: Path,
    seg: dict[str, Any],
    old_file: str,
    *,
    task_id: str | None = None,
    stage: str = "slot_fit",
) -> bool:
    """
    Remove a superseded working copy without deleting the immutable TTS-stage artifact.

    slot_fit may replace seg['file'] with a regen copy while tts_file_path keeps the
    original TTS filename for contract compliance.
    """
    old_name = Path(old_file).name
    canonical = seg.get("tts_file_path")
    canonical_name = Path(str(canonical)).name if canonical else None
    segment_id = str(seg.get("segment_id") or "")
    segment_index = seg.get("index")

    if canonical_name and old_name == canonical_name:
        log_tts_lifecycle(
            task_id,
            event="unlink_skipped",
            segment_id=segment_id or None,
            segment_index=segment_index,
            filename=old_name,
            stage=stage,
            detail="preserve canonical tts_file_path artifact",
        )
        return False

    target = artifacts_dir / old_name
    existed = target.is_file()
    target.unlink(missing_ok=True)
    log_tts_lifecycle(
        task_id,
        event="unlink",
        segment_id=segment_id or None,
        segment_index=segment_index,
        filename=old_name,
        path=target,
        stage=stage,
        success=True,
        exists=target.is_file(),
        detail=f"removed={existed}",
    )
    return existed
