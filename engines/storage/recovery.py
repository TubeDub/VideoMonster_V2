"""Session recovery (Storage Manager §3).

Автоматически сохраняет состояние открытых проектов и предлагает
восстановление после аварийного завершения программы.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

from engines.storage.atomic import atomic_write_json, read_json
from engines.storage.paths import StoragePaths

logger = logging.getLogger("tubedub.storage.recovery")

_RECOVERY_VERSION = 1


def _recovery_payload(
    project_id: str,
    *,
    title: str = "",
    step: str = "",
    progress: float = 0.0,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "version": _RECOVERY_VERSION,
        "project_id": project_id,
        "title": title,
        "step": step,
        "progress": progress,
        "pid": os.getpid(),
        "saved_at": time.time(),
        "extra": dict(extra or {}),
    }


def save_recovery_state(
    paths: StoragePaths,
    project_id: str,
    *,
    title: str = "",
    step: str = "",
    progress: float = 0.0,
    extra: dict[str, Any] | None = None,
) -> None:
    """Persist recovery checkpoint for ``project_id`` (atomic)."""
    payload = _recovery_payload(
        project_id, title=title, step=step, progress=progress, extra=extra
    )
    atomic_write_json(paths.recovery_path, payload)


def clear_recovery_state(paths: StoragePaths) -> None:
    """Remove recovery checkpoint (normal session finish)."""
    try:
        paths.recovery_path.unlink(missing_ok=True)
    except OSError:
        pass


def load_recovery_state(paths: StoragePaths) -> dict[str, Any] | None:
    """Load recovery checkpoint if present."""
    data = read_json(paths.recovery_path)
    return data if isinstance(data, dict) and data.get("project_id") else None


def check_recovery(paths: StoragePaths) -> dict[str, Any] | None:
    """Return recovery info if a previous session ended abnormally.

    Recovery is offered when:
      * recovery file exists;
      * referenced project still exists on disk;
      * recovery is younger than 7 days.
    """
    try:
        data = load_recovery_state(paths)
    except Exception as exc:  # noqa: BLE001
        logger.warning("corrupt recovery state ignored: %s", exc)
        try:
            clear_recovery_state(paths)
        except Exception:
            pass
        return None
    if not data:
        return None

    project_id = Path(str(data.get("project_id") or "")).name
    if not project_id or project_id != str(data.get("project_id") or ""):
        clear_recovery_state(paths)
        return None

    try:
        saved_at = float(data.get("saved_at") or 0)
    except (TypeError, ValueError):
        clear_recovery_state(paths)
        return None
    if saved_at and (time.time() - saved_at) > 7 * 86400:
        clear_recovery_state(paths)
        return None

    # Project must still exist (active or trashed).
    try:
        active = paths.project_json(project_id, trashed=False)
        trashed = paths.project_json(project_id, trashed=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("recovery project path failed: %s", exc)
        clear_recovery_state(paths)
        return None
    if not active.is_file() and not trashed.is_file():
        clear_recovery_state(paths)
        return None

    return {
        "project_id": project_id,
        "title": data.get("title") or project_id,
        "step": data.get("step") or "",
        "progress": float(data.get("progress") or 0),
        "saved_at": saved_at,
        "trashed": trashed.is_file() and not active.is_file(),
        "extra": dict(data.get("extra") or {}),
    }
