"""AI Bus — central communication system (Master Spec v3.0 §2).

Wraps the in-process AI Network with Manifest/State, recovery routing, and memory hooks.
Agents MUST NOT call each other directly — only publish/subscribe through the Bus.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Callable

from engines.ai_core.ai_network.bus import AINetwork, get_network, reset_network
from engines.ai_core.ai_network.envelope import (
    EVENT_RECOVERY_ACTION,
    NetworkEnvelope,
)
from engines.ai_core.platform.project_state import freeze_manifest, merge_state_update
from engines.ai_core.platform.versions import AI_BUS_VERSION, platform_versions

logger = logging.getLogger("tubedub.ai_core.ai_bus")

EVENT_MANIFEST_PUBLISHED = "manifest_published"
EVENT_STATE_UPDATED = "state_updated"
EVENT_RECOVERY_ROUTED = "recovery_routed"
EVENT_MEMORY_UPDATED = "memory_updated"

_LOCK = threading.RLock()
_RECOVERY_HANDLERS: list[Callable[[dict[str, Any]], None]] = []


class AIBus:
    """Per-run AI Bus — manifest, state, events, recovery (§2)."""

    def __init__(self, run_id: str) -> None:
        self.run_id = str(run_id or "")
        self._net = get_network(self.run_id)
        self._manifest: dict[str, Any] = {}
        self._state: dict[str, Any] = {"run_id": self.run_id}
        self._memory: dict[str, Any] = {"segments": {}}
        self._recovery_queue: list[dict[str, Any]] = []

    @property
    def versions(self) -> dict[str, str]:
        return platform_versions()

    def publish_manifest(self, manifest: dict[str, Any], *, source: str = "planner") -> None:
        self._manifest = freeze_manifest(manifest)
        self._net.publish(
            EVENT_MANIFEST_PUBLISHED,
            source,
            {"manifest_version": manifest.get("pipeline_version"), "keys": list(manifest.keys())[:20]},
        )

    def get_manifest(self) -> dict[str, Any]:
        return freeze_manifest(self._manifest)

    def update_state(self, agent_id: str, patch: dict[str, Any], *, source: str | None = None) -> None:
        self._state = merge_state_update(self._state, agent_id, patch)
        self._net.publish(
            EVENT_STATE_UPDATED,
            source or agent_id,
            {"agent": agent_id, "keys": list(patch.keys())},
        )

    def get_state(self) -> dict[str, Any]:
        return dict(self._state)

    def publish(self, event_type: str, source: str, payload: dict[str, Any] | None = None) -> NetworkEnvelope:
        return self._net.publish(event_type, source, payload)

    def subscribe(self, event_type: str, handler: Callable[[NetworkEnvelope], None]) -> None:
        self._net.subscribe(event_type, handler)

    def route_recovery(
        self,
        *,
        from_agent: str,
        to_agent: str,
        segment_index: int,
        reason: str,
        priority: int = 5,
    ) -> dict[str, Any]:
        """Queue recovery action; Bus routes to registered handler (§23)."""
        action = {
            "from_agent": from_agent,
            "to_agent": to_agent,
            "segment_index": segment_index,
            "reason": reason,
            "priority": priority,
            "fix_hint": f"Re-run {to_agent} for segment {segment_index}",
            "assigned_agent": to_agent,
        }
        with _LOCK:
            self._recovery_queue.append(action)
        self._net.publish(EVENT_RECOVERY_ACTION, from_agent, action)
        self.publish(EVENT_RECOVERY_ROUTED, "ai_bus", action)
        for handler in list(_RECOVERY_HANDLERS):
            try:
                handler(action)
            except Exception as exc:
                logger.debug("recovery handler error: %s", exc)
        return action

    def remember_segment(self, segment_index: int, entry: dict[str, Any]) -> None:
        """AI Memory hook — per-segment history (§3, Stage 5)."""
        key = str(segment_index)
        hist = self._memory.setdefault("segments", {}).setdefault(key, [])
        hist.append(entry)
        if len(hist) > 50:
            self._memory["segments"][key] = hist[-50:]
        self._net.publish(EVENT_MEMORY_UPDATED, "ai_memory", {"segment_index": segment_index})

    def get_segment_memory(self, segment_index: int) -> list[dict[str, Any]]:
        return list(self._memory.get("segments", {}).get(str(segment_index), []))

    def journal(self) -> list[dict[str, Any]]:
        return self._net.journal()

    def snapshot(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "ai_bus_version": AI_BUS_VERSION,
            **platform_versions(),
            "manifest_keys": list(self._manifest.keys()) if self._manifest else [],
            "state_keys": list(self._state.keys()),
            "recovery_pending": len(self._recovery_queue),
            "memory_segments": len(self._memory.get("segments", {})),
            "events": len(self._journal()),
        }

    def _journal(self) -> list[dict[str, Any]]:
        return self._net.journal()


_BUSES: dict[str, AIBus] = {}


def get_bus(run_id: str) -> AIBus:
    rid = str(run_id or "").strip()
    with _LOCK:
        if rid not in _BUSES:
            _BUSES[rid] = AIBus(rid)
        return _BUSES[rid]


def reset_bus(run_id: str) -> None:
    rid = str(run_id or "").strip()
    with _LOCK:
        _BUSES.pop(rid, None)
    reset_network(rid)


def register_recovery_handler(handler: Callable[[dict[str, Any]], None]) -> None:
    with _LOCK:
        _RECOVERY_HANDLERS.append(handler)


def save_bus_snapshot(run_id: str, app_dir: Path | None = None) -> str | None:
    bus = _BUSES.get(str(run_id or ""))
    if not bus:
        return None
    root = app_dir or Path(__file__).resolve().parents[3]
    out = root / "output" / "diagnostics" / run_id / "ai_bus_snapshot.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "snapshot": bus.snapshot(),
        "recovery_queue": bus._recovery_queue,
        "journal": bus.journal(),
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(out)
