"""AI Network — centralized communication bus for AI agents (TZ #1 §3).

Agents must not call each other directly. The orchestrator publishes events;
subscribers (Reviewer, diagnostics, event log) react asynchronously in-process.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import Any, Callable

from engines.ai_core.ai_network.envelope import NetworkEnvelope

logger = logging.getLogger("tubedub.ai_core.ai_network")

Handler = Callable[[NetworkEnvelope], None]

_LOCK = threading.RLock()
_RUN_BUSES: dict[str, "AINetwork"] = {}


class AINetwork:
    """Per-run in-process event bus."""

    def __init__(self, run_id: str) -> None:
        self.run_id = str(run_id or "")
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._journal: list[dict[str, Any]] = []

    def subscribe(self, event_type: str, handler: Handler) -> None:
        with _LOCK:
            self._handlers[str(event_type)].append(handler)

    def publish(
        self,
        event_type: str,
        source: str,
        payload: dict[str, Any] | None = None,
    ) -> NetworkEnvelope:
        env = NetworkEnvelope(
            event=str(event_type),
            source=str(source),
            run_id=self.run_id,
            payload=dict(payload or {}),
        )
        with _LOCK:
            self._journal.append(env.to_dict())
            handlers = list(self._handlers.get(env.event, []))
            handlers.extend(self._handlers.get("*", []))
        for handler in handlers:
            try:
                handler(env)
            except Exception as exc:
                logger.debug("AI Network handler error %s: %s", event_type, exc)
        return env

    def journal(self) -> list[dict[str, Any]]:
        with _LOCK:
            return list(self._journal)

    def project_state_snapshot(self) -> dict[str, Any]:
        """Latest project-state hints from bus journal."""
        state: dict[str, Any] = {"run_id": self.run_id, "events": len(self._journal)}
        for entry in reversed(self._journal):
            if entry.get("event") == "agent_finished":
                agent = (entry.get("payload") or {}).get("agent")
                if agent:
                    state[f"{agent}_status"] = (entry.get("payload") or {}).get("status")
        return state


def get_network(run_id: str) -> AINetwork:
    rid = str(run_id or "").strip()
    with _LOCK:
        if rid not in _RUN_BUSES:
            _RUN_BUSES[rid] = AINetwork(rid)
        return _RUN_BUSES[rid]


def reset_network(run_id: str) -> None:
    rid = str(run_id or "").strip()
    with _LOCK:
        _RUN_BUSES.pop(rid, None)


def save_network_journal(run_id: str, app_dir: Any = None) -> str | None:
    """Persist bus journal for diagnostics."""
    from pathlib import Path

    net = _RUN_BUSES.get(str(run_id or ""))
    if not net:
        return None
    root = Path(app_dir) if app_dir else Path(__file__).resolve().parents[3]
    out = root / "output" / "diagnostics" / run_id / "ai_network_journal.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    import json

    out.write_text(
        json.dumps({"run_id": run_id, "events": net.journal()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(out)
