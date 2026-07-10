"""Peer validation loop — return segments to upstream before downstream runs."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from engines.ai_core.peer_validation import (
    MAX_PEER_RETURNS,
    PEER_UPSTREAM,
    PeerReturn,
    upstream_status_field,
    validate_upstream_batch,
)

logger = logging.getLogger("tubedub.ai_core.peer_validation")

_APP_DIR = Path(__file__).resolve().parents[2]


def write_peer_validation_log(
    task_id: str,
    entries: list[dict[str, Any]],
    *,
    app_dir: Path | None = None,
) -> Path:
    base = app_dir or _APP_DIR
    diag = base / "output" / "diagnostics" / task_id
    diag.mkdir(parents=True, exist_ok=True)
    path = diag / "peer_validation_log.json"
    payload = {
        "task_id": task_id,
        "engine": "Peer Validation Pipeline",
        "return_count": len(entries),
        "returns": entries,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _rerun_upstream_segment(
    agent_slug: str,
    seg: dict[str, Any],
    manifest: dict[str, Any],
    state: dict[str, Any],
    task_id: str,
    list_index: int,
) -> dict[str, Any]:
    """Re-run upstream agent for one segment via retry orchestrator."""
    from engines.ai_core.peer_validation import retry_agent_class_name
    from engines.ai_core.quality_agent.retry_orchestrator import rerun_agent_for_segment

    class_name = retry_agent_class_name(agent_slug)
    return rerun_agent_for_segment(class_name, list_index, manifest, state, task_id)


def resolve_peer_returns(
    agent: str,
    validation,
    manifest: dict[str, Any],
    state: dict[str, Any],
    task_id: str,
    *,
    log: list[dict[str, Any]],
) -> tuple[int, list[str]]:
    """
    Route failed segments to upstream agents. Returns (fixed_count, warnings).
    Each segment retried at most MAX_PEER_RETURNS times per validation round.
    """
    warnings: list[str] = []
    fixed = 0
    segments = state.get("segments") or []
    by_index = {int(s.get("index", i)): (i, s) for i, s in enumerate(segments)}

    seen: dict[tuple[int, str], int] = {}

    for pr in validation.returns:
        if pr.segment_index < 0:
            warnings.append(f"{agent}: {pr.error_code}")
            continue

        target = pr.receiver_agent
        key = (pr.segment_index, target)
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > MAX_PEER_RETURNS:
            warnings.append(
                f"segment_{pr.segment_index}:max_peer_returns_exceeded:{target}"
            )
            log.append({**pr.to_dict(), "action": "max_returns_exceeded"})
            continue

        entry = by_index.get(pr.segment_index)
        if not entry:
            continue
        list_index, seg = entry

        log.append({**pr.to_dict(), "action": "return_to_upstream", "attempt": seen[key]})

        try:
            updated = _rerun_upstream_segment(target, seg, manifest, state, task_id, list_index)
            if updated:
                by_index[pr.segment_index][1].update(updated)
                fixed += 1
        except Exception as exc:
            logger.warning("Peer return rerun failed seg=%s agent=%s: %s", pr.segment_index, target, exc)
            warnings.append(f"segment_{pr.segment_index}:rerun_failed:{exc}")
            log.append({**pr.to_dict(), "action": "rerun_failed", "error": str(exc)})

    state["segments"] = [pair[1] for pair in sorted(by_index.values(), key=lambda x: x[0])]
    return fixed, warnings


def run_peer_validation_gate(
    agent: str,
    manifest: dict[str, Any],
    state: dict[str, Any],
    task_id: str,
    *,
    max_rounds: int = 2,
    app_dir: Path | None = None,
) -> tuple[bool, list[str], list[dict[str, Any]]]:
    """
    Validate upstream contract before agent runs. On failure, return segments upstream.
    Returns (ok, warnings, log_entries).
    """
    log: list[dict[str, Any]] = state.setdefault("peer_validation_log", [])
    warnings: list[str] = []
    tgt = str(manifest.get("target_lang") or state.get("target_lang") or "uk")
    status_field = upstream_status_field(agent)
    upstream_status = str(state.get(status_field) or "success") if status_field else "success"

    for round_num in range(max_rounds):
        validation = validate_upstream_batch(
            agent,
            state.get("segments") or [],
            target_lang=tgt,
            manifest=manifest,
            upstream_status=upstream_status,
        )
        if validation.ok:
            warnings.extend(validation.warnings)
            return True, warnings, log

        log.append(
            {
                "round": round_num + 1,
                "agent": agent,
                "upstream": PEER_UPSTREAM.get(agent),
                "validation": validation.to_dict(),
            }
        )

        fixed, rerun_warnings = resolve_peer_returns(
            agent, validation, manifest, state, task_id, log=log
        )
        warnings.extend(rerun_warnings)
        warnings.extend(validation.warnings)

        if fixed == 0:
            break

        upstream_status = str(state.get(status_field) or "success") if status_field else "success"

    final = validate_upstream_batch(
        agent,
        state.get("segments") or [],
        target_lang=tgt,
        manifest=manifest,
        upstream_status=upstream_status,
    )
    if not final.ok:
        for pr in final.returns:
            log.append({**pr.to_dict(), "action": "unresolved_at_gate"})
        warnings.append(f"{agent}:peer_validation_unresolved:{len(final.returns)}_segments")

    write_peer_validation_log(task_id, log, app_dir=app_dir)
    return final.ok, warnings, log
