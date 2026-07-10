"""Unified AI diagnostics (TZ #1 §6) — single schema for run observability."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from engines.ai_core.architecture_validation import AI_CORE_VERSION

logger = logging.getLogger("tubedub.ai_core.unified_diagnostics")

_APP_DIR = Path(__file__).resolve().parents[2]


def _diag_dir(run_id: str, app_dir: Path | None = None) -> Path:
    root = app_dir or _APP_DIR
    return root / "output" / "diagnostics" / str(run_id)


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _resolve_active_model() -> dict[str, Any]:
    try:
        from engines.llm_adaptation_mode import detect_capabilities

        caps = detect_capabilities()
        model = str(caps.get("model") or "")
        provider = str(caps.get("provider") or "")
        display = model
        low = model.lower()
        if "deepseek" in low:
            display = "DeepSeek"
        elif "qwen" in low:
            display = "Qwen"
        elif "llama" in low:
            display = "Llama"
        elif "gemma" in low:
            display = "Gemma"
        elif provider == "openai" or "gpt" in low:
            display = "OpenAI GPT"
        return {
            "model": model,
            "provider": provider,
            "display_name": display or model or "—",
            "param_b": caps.get("model_param_b"),
            "adequate": caps.get("model_adequate"),
            "warning": caps.get("model_warning") or "",
            "available_models": caps.get("available_models") or [],
        }
    except Exception:
        return {"model": "", "provider": "", "display_name": "—"}


def _build_pipeline_route(arch: dict[str, Any], events: list[dict[str, Any]]) -> list[str]:
    agents = list(arch.get("active_agents") or [])
    if agents:
        return agents
    seen: list[str] = []
    for ev in events:
        agent = (ev.get("agent") or "").strip()
        if agent and agent not in seen and ev.get("event") == "Started":
            seen.append(agent)
    return seen


def _stages_from_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    started: dict[str, float] = {}
    stages: list[dict[str, Any]] = []
    for ev in events:
        agent = str(ev.get("agent") or "")
        if not agent:
            continue
        if ev.get("event") == "Started":
            started[agent] = float(ev.get("ts") or 0)
        elif ev.get("event") == "Finished" and agent in started:
            t0 = started.pop(agent)
            t1 = float(ev.get("ts") or 0)
            stages.append(
                {
                    "agent": agent,
                    "status": ev.get("status") or "success",
                    "ms": ev.get("ms") or round(max(0, (t1 - t0) * 1000), 1),
                    "model": ev.get("model"),
                }
            )
    return stages


def _cloud_profiles_safe() -> list[dict[str, str]]:
    try:
        from engines.llm_providers.transport import list_cloud_profiles

        return list_cloud_profiles()
    except Exception:
        return []


def build_unified_diagnostics(
    run_id: str,
    *,
    app_dir: Path | None = None,
    task_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble real diagnostics from persisted run artifacts (no stubs)."""
    root = app_dir or _APP_DIR
    rid = str(run_id or "")
    ddir = _diag_dir(rid, root)

    arch = _load_json(ddir / "architecture_validation.json")
    peer = _load_json(ddir / "peer_validation_log.json")
    streaming = _load_json(ddir / "streaming_pipeline_report.json")
    reviewer = _load_json(ddir / "reviewer_loop.json")
    network = _load_json(ddir / "ai_network_journal.json")

    from engines.ai_core.ai_event_log import load_ai_events

    events = load_ai_events(rid, root)
    info = task_info or {}

    route = _build_pipeline_route(arch, events)
    stages = _stages_from_events(events)
    if not stages and arch.get("agents"):
        stages = list(arch.get("agents") or [])

    errors: list[str] = list(info.get("errors") or [])
    warnings: list[str] = list(info.get("warnings") or [])
    if arch.get("contract_violations"):
        warnings.extend(str(v) for v in arch.get("contract_violations") or [])

    quality_score = None
    try:
        llm_eff = info.get("llm_effectiveness") or {}
        if llm_eff.get("avg_slot_fit") is not None:
            quality_score = round(float(llm_eff["avg_slot_fit"]) * 100, 1)
    except Exception:
        pass

    whisper = (
        info.get("whisper_model")
        or info.get("stt_model")
        or arch.get("whisper_model")
    )
    tts = info.get("tts_engine") or info.get("voice") or "edge-tts"
    pipeline_mode = info.get("pipeline_mode") or arch.get("pipeline_mode") or "batch"

    from engines.ai_core.development_lifecycle import load_lifecycle

    lifecycle = load_lifecycle(rid, root)

    from engines.ai_core.observability import load_observability
    from engines.ai_core.services.ai_memory import load_memory_snapshot
    from engines.ai_core.platform.feature_registry import list_platform_features

    observability = load_observability(rid, root)
    ai_memory = load_memory_snapshot(rid, root)
    bus_snap = _load_json(ddir / "ai_bus_snapshot.json")
    platform_features = list_platform_features()

    active_agent = observability.get("active_agent")
    hw = observability.get("hardware") or {}
    confidence = None
    timing_error = None
    retry_count = 0
    for agent_row in observability.get("agents") or []:
        retry_count += int(agent_row.get("retry_count") or 0)
        if agent_row.get("confidence") is not None:
            confidence = agent_row.get("confidence")
        if agent_row.get("average_timing_error_ms") is not None:
            timing_error = agent_row.get("average_timing_error_ms")

    voice_profile = None
    try:
        from engines.ai_core.services.voice_profile_manager import get_voice_profile_manager

        voice_profile = get_voice_profile_manager().resolve_for_task(info)
    except Exception:
        pass

    status = str(
        arch.get("pipeline_status")
        or info.get("status")
        or "unknown"
    )

    return {
        "schema": "tubedub.unified_diagnostics.v1",
        "ai_core_version": AI_CORE_VERSION,
        "run_id": rid,
        "status": status,
        "pipeline_mode": pipeline_mode,
        "pipeline_route": route,
        "pipeline_route_display": " → ".join(route) if route else "—",
        "whisper": whisper,
        "tts": tts,
        "active_model": _resolve_active_model(),
        "global_skill_version": info.get("global_skill_version"),
        "development_lifecycle": lifecycle,
        "cloud_profiles": _cloud_profiles_safe(),
        "agents": arch.get("agents") or stages,
        "stages": stages,
        "quality_score": quality_score,
        "confidence": confidence,
        "timing_error_ms": timing_error,
        "retry_count": retry_count,
        "active_agent": active_agent,
        "voice": voice_profile,
        "hardware": hw,
        "feature_flags": platform_features,
        "observability": observability if observability else None,
        "ai_memory": {
            "segment_count": ai_memory.get("segment_count"),
            "total_entries": ai_memory.get("total_entries"),
        }
        if ai_memory
        else None,
        "ai_bus": bus_snap.get("snapshot") if bus_snap else None,
        "errors": errors[:50],
        "warnings": warnings[:50],
        "peer_validation": peer.get("entries") or peer,
        "reviewer": reviewer,
        "streaming": streaming if streaming else None,
        "ai_network_event_count": len(network.get("events") or []),
        "total_execution_ms": arch.get("total_execution_time_ms"),
        "sources": {
            "architecture_validation": str(ddir / "architecture_validation.json"),
            "ai_events": str(ddir / "ai_events.jsonl"),
            "ai_network_journal": str(ddir / "ai_network_journal.json"),
        },
    }


def save_unified_diagnostics(
    run_id: str,
    *,
    app_dir: Path | None = None,
    task_info: dict[str, Any] | None = None,
) -> Path:
    payload = build_unified_diagnostics(run_id, app_dir=app_dir, task_info=task_info)
    out = _diag_dir(run_id, app_dir) / "unified_diagnostics.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def load_unified_diagnostics(run_id: str, app_dir: Path | None = None) -> dict[str, Any] | None:
    path = _diag_dir(run_id, app_dir) / "unified_diagnostics.json"
    if not path.is_file():
        return None
    data = _load_json(path)
    return data or None
