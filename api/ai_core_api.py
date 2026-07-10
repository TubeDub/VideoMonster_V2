"""TubeDub AI Core API — reports and unified diagnostics (TZ #1)."""

from __future__ import annotations

import logging
from pathlib import Path

from flask import Blueprint, jsonify, request

from engines.ai_core.report import build_ai_core_report, load_ai_core_report, save_ai_core_report

logger = logging.getLogger("tubedub.ai_core_api")

bp = Blueprint("ai_core_api", __name__)
APP_DIR = Path(__file__).resolve().parents[1]


@bp.get("/api/ai_core/report/<task_id>")
def api_ai_core_report(task_id: str):
    """Return aggregated AI Core report for task_id."""
    safe = Path(task_id).name
    if not safe or safe != task_id:
        return jsonify({"ok": False, "error": "invalid task_id"}), 400

    report = load_ai_core_report(safe)
    if not report:
        report = build_ai_core_report(safe)
        if report.get("agents") or report.get("agent_timeline"):
            save_ai_core_report(safe)
        elif not report.get("ddf_summary"):
            return jsonify({"ok": False, "error": "report not found", "task_id": safe}), 404

    return jsonify({"ok": True, **report})


@bp.get("/api/ai_core/diagnostics/<run_id>")
def api_ai_core_diagnostics(run_id: str):
    """Unified diagnostics: pipeline route, agents, model, stages, errors (TZ #1 §6)."""
    safe = Path(run_id).name
    if not safe or safe != run_id:
        return jsonify({"ok": False, "error": "invalid run_id"}), 400

    from engines.ai_core.unified_diagnostics import (
        build_unified_diagnostics,
        load_unified_diagnostics,
        save_unified_diagnostics,
    )

    payload = load_unified_diagnostics(safe, APP_DIR)
    if not payload:
        payload = build_unified_diagnostics(safe, app_dir=APP_DIR)
        if payload.get("pipeline_route") or payload.get("stages"):
            save_unified_diagnostics(safe, app_dir=APP_DIR)
    return jsonify({"ok": True, **payload})


@bp.get("/api/ai_core/global-skill")
def api_global_skill():
    """Return Global Skill rules (TZ #1 §1)."""
    from engines.ai_core.global_skill import to_dict

    return jsonify({"ok": True, **to_dict()})


@bp.get("/api/ai_core/events/<run_id>")
def api_ai_core_events(run_id: str):
    """AI event journal for a run (TZ #1 §8)."""
    safe = Path(run_id).name
    if not safe or safe != run_id:
        return jsonify({"ok": False, "error": "invalid run_id"}), 400

    from engines.ai_core.ai_event_log import load_ai_events

    events = load_ai_events(safe, APP_DIR)
    return jsonify({"ok": True, "run_id": safe, "events": events, "count": len(events)})


@bp.get("/api/ai_core/platform/versions")
def api_platform_versions():
    """Master Spec v3.0 §21 — protocol, manifest, bus versions."""
    from engines.ai_core.platform import platform_versions
    from engines.ai_core.architecture_validation import AI_CORE_VERSION

    return jsonify(
        {
            "ok": True,
            "implementation_version": AI_CORE_VERSION,
            **platform_versions(),
        }
    )


@bp.get("/api/ai_core/platform/capabilities")
def api_platform_capabilities():
    """Master Spec v3.0 §4 — AI Capability Registry."""
    from engines.ai_core.platform.capability_registry import build_registry

    return jsonify({"ok": True, **build_registry()})


@bp.get("/api/ai_core/platform/features")
def api_platform_features():
    """Master Spec v3.0 §18 — platform feature flags."""
    from engines.ai_core.platform.feature_registry import list_platform_features

    return jsonify({"ok": True, "features": list_platform_features()})


@bp.get("/api/ai_core/platform/bus/<run_id>")
def api_platform_bus(run_id: str):
    """AI Bus snapshot for a run (§2)."""
    safe = Path(run_id).name
    if not safe or safe != run_id:
        return jsonify({"ok": False, "error": "invalid run_id"}), 400

    from engines.ai_core.platform import get_bus

    bus = get_bus(safe)
    return jsonify({"ok": True, **bus.snapshot(), "journal_count": len(bus.journal())})


@bp.get("/api/ai_core/platform/memory/<run_id>")
def api_platform_memory(run_id: str):
    """AI Memory snapshot for a run (TZ Stage 3)."""
    safe = Path(run_id).name
    if not safe or safe != run_id:
        return jsonify({"ok": False, "error": "invalid run_id"}), 400

    from engines.ai_core.services.ai_memory import load_memory_snapshot

    data = load_memory_snapshot(safe, APP_DIR)
    return jsonify({"ok": True, "run_id": safe, **data})


@bp.get("/api/ai_core/platform/observability/<run_id>")
def api_platform_observability(run_id: str):
    """Agent observability metrics (TZ Stage 16)."""
    safe = Path(run_id).name
    if not safe or safe != run_id:
        return jsonify({"ok": False, "error": "invalid run_id"}), 400

    from engines.ai_core.observability import load_observability

    data = load_observability(safe, APP_DIR)
    return jsonify({"ok": True, **data})


@bp.get("/api/ai_core/platform/voice-profiles")
def api_voice_profiles():
    """Voice Profile Manager catalog (TZ Stage 5)."""
    from engines.ai_core.services.voice_profile_manager import get_voice_profile_manager

    mgr = get_voice_profile_manager()
    lang = request.args.get("lang", "ru")
    return jsonify(
        {
            "ok": True,
            "lang": lang,
            "voices": mgr.list_voices(lang),
            "default_profile": mgr.get_profile("", lang),
        }
    )


@bp.get("/api/ai_core/platform/dag")
def api_platform_dag():
    """AI Network DAG (TZ Stage 10)."""
    from engines.ai_core.ai_network.dag import dag_snapshot, validate_dag

    snap = dag_snapshot()
    return jsonify({"ok": True, "valid": validate_dag(), **snap})
