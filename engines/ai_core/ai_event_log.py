"""Unified AI event logging (TZ #1 §8).

Every AI stage records Started/Finished/Approved/Rejected to JSONL + logger.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.ai_core.events")

_LOCK = threading.RLock()
_APP_DIR = Path(__file__).resolve().parents[2]


def _events_path(run_id: str, app_dir: Path | None = None) -> Path:
    root = app_dir or _APP_DIR
    return root / "output" / "diagnostics" / str(run_id) / "ai_events.jsonl"


def log_ai_event(
    run_id: str,
    event: str,
    *,
    agent: str = "",
    model: str = "",
    provider: str = "",
    status: str = "",
    ms: float | None = None,
    extra: dict[str, Any] | None = None,
    app_dir: Path | None = None,
) -> None:
    """Append one structured AI event (Translation Started, Reviewer Approved, …)."""
    row = {
        "ts": time.time(),
        "run_id": str(run_id or ""),
        "event": str(event),
        "agent": agent or None,
        "model": model or None,
        "provider": provider or None,
        "status": status or None,
        "ms": round(ms, 1) if ms is not None else None,
    }
    if extra:
        row.update(extra)
    row = {k: v for k, v in row.items() if v is not None}

    label = event
    if agent:
        label = f"{agent} {event}"
    detail = f" model={model}" if model else ""
    logger.info("[AI Event] %s%s run=%s", label, detail, run_id)

    path = _events_path(run_id, app_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_ai_events(run_id: str, app_dir: Path | None = None) -> list[dict[str, Any]]:
    path = _events_path(run_id, app_dir)
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    except Exception:
        pass
    return out


def agent_started(run_id: str, agent: str, **kwargs: Any) -> None:
    log_ai_event(run_id, "Started", agent=agent, **kwargs)


def agent_finished(
    run_id: str,
    agent: str,
    *,
    status: str = "success",
    ms: float | None = None,
    **kwargs: Any,
) -> None:
    log_ai_event(run_id, "Finished", agent=agent, status=status, ms=ms, **kwargs)


def reviewer_approved(run_id: str, agent: str, **kwargs: Any) -> None:
    log_ai_event(run_id, "Approved", agent=agent, status="approved", **kwargs)


def reviewer_rejected(run_id: str, agent: str, reason: str = "", **kwargs: Any) -> None:
    log_ai_event(
        run_id,
        "Rejected",
        agent=agent,
        status="rejected",
        extra={"reason": reason} if reason else None,
        **kwargs,
    )
