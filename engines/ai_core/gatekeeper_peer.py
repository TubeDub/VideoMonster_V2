"""AI Core 4.0 — minimal shared gatekeeper (manifest + upstream status only).

Per-segment contract validation is handled by Peer Validation in the orchestrator.
Agents must not duplicate those checks here.
"""

from __future__ import annotations

from typing import Any

from engines.ai_core.peer_validation import upstream_status_field


def check_upstream_gate(
    agent: str,
    manifest: dict[str, Any],
    state: dict[str, Any],
) -> tuple[bool, list[str], list[str]]:
    """
    Minimal gate before agent work.
    Returns (ok, errors, warnings).
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not manifest.get("project_uuid"):
        return False, ["planner_not_complete:missing_project_uuid"], warnings

    segments = state.get("segments") or []
    if agent in (
        "semantic", "timing", "grammar", "quality", "reviewer",
        "voice_preparation", "voice", "voice_verification",
    ) and not segments:
        return False, [f"{agent}_gate:no_segments"], warnings

    status_field = upstream_status_field(agent)
    if status_field:
        up_status = str(state.get(status_field) or "success")
        if up_status == "error":
            upstream = status_field.replace("_agent_status", "")
            return False, [f"{upstream}_agent_failed"], warnings
        if up_status == "warning":
            warnings.append(f"{status_field}=warning")

    return True, errors, warnings
