"""Pipeline Platform API — developer-only."""

from __future__ import annotations

import os
from pathlib import Path

from flask import Blueprint, jsonify, request

APP_DIR = Path(__file__).parent.parent.resolve()
bp = Blueprint("pipeline_platform_api", __name__)


def _dev_mode() -> bool:
    from engines.module_registry.registry import is_developer_session

    return is_developer_session(request_headers=dict(request.headers))


@bp.get("/api/pipeline/platform/status")
def api_pipeline_platform_status():
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    from engines.pipeline_platform import platform_status

    return jsonify({"ok": True, **platform_status()})


@bp.get("/api/pipeline/orchestrator/status")
def api_pipeline_orchestrator_status():
    """Live AI Orchestrator status: agent states, resources, queues, stats."""
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    try:
        from core.orchestrator import get_orchestrator

        orch = get_orchestrator()
        if orch is None:
            return jsonify({"ok": True, "active": False, "status": None})
        return jsonify({"ok": True, "active": True, "status": orch.get_status()})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.get("/api/pipeline/llm_dispatcher/status")
def api_pipeline_llm_dispatcher_status():
    """LLM Dispatcher status: model registry, stats, active model, failover chain."""
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    try:
        from core.llm_dispatcher import get_dispatcher

        return jsonify({"ok": True, "status": get_dispatcher().get_status()})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.post("/api/pipeline/llm_dispatcher/model")
def api_pipeline_llm_dispatcher_set_model():
    """Hot-swap the active model mid-processing (TZ #3 §9)."""
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    body = request.get_json(silent=True) or {}
    model = body.get("model")
    try:
        from core.llm_dispatcher import get_dispatcher

        ok = get_dispatcher().set_active_model(model if model else None)
        return jsonify({"ok": ok, "active_model": get_dispatcher().active_model()})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.get("/api/pipeline/memory/status")
def api_pipeline_memory_status():
    """AI Memory status: dictionaries, cache stats (TZ #6)."""
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    try:
        from core.ai_memory import get_memory
        from core.semantic_cache import get_semantic_cache

        project_id = request.args.get("project_id", "")
        return jsonify({
            "ok": True,
            "memory": get_memory(project_id, app_dir=APP_DIR).get_status(),
            "cache": get_semantic_cache().to_dict(),
        })
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.get("/api/pipeline/performance/status")
def api_pipeline_performance_status():
    """Performance Optimizer status: hardware, benchmark, plan (TZ #7)."""
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    try:
        from core.performance_optimizer import get_performance_optimizer

        opt = get_performance_optimizer(app_dir=APP_DIR)
        if request.args.get("init") == "1":
            opt.initialize(quick=True)
        return jsonify({"ok": True, "status": opt.get_status()})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.post("/api/pipeline/performance/mode")
def api_pipeline_performance_set_mode():
    """Set user performance mode: max_quality | balanced | max_performance (TZ #7 §11)."""
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    body = request.get_json(silent=True) or {}
    mode = str(body.get("mode") or "").strip().lower()
    try:
        from core.performance_optimizer import (
            MODES,
            get_performance_optimizer,
            reset_performance_optimizer,
        )

        if mode not in MODES:
            return jsonify({"ok": False, "error": f"mode must be one of {MODES}"}), 400
        os.environ["VM_PERF_MODE"] = mode
        reset_performance_optimizer()
        plan = get_performance_optimizer(app_dir=APP_DIR).initialize(quick=True)
        return jsonify({"ok": True, "mode": mode, "tier": plan.tier})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.get("/api/pipeline/performance/monitor")
def api_pipeline_performance_monitor():
    """Live performance monitor: CPU/GPU/RAM/VRAM/queues (TZ #7 §12)."""
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    try:
        from core.performance_monitor import get_performance_monitor

        mon = get_performance_monitor()
        if request.args.get("sample") == "1":
            mon.sample()
        return jsonify({"ok": True, "status": mon.get_status()})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.post("/api/pipeline/performance/benchmark")
def api_pipeline_performance_benchmark():
    """Run hardware benchmark (<60s) and rebuild plan (TZ #7 §2)."""
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    try:
        from core.performance_optimizer import get_performance_optimizer, reset_performance_optimizer

        quick = request.args.get("quick") != "0"
        reset_performance_optimizer()
        plan = get_performance_optimizer(app_dir=APP_DIR).initialize(
            force_benchmark=True, quick=quick,
        )
        return jsonify({"ok": True, "tier": plan.tier, "plan": plan.to_dict()})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


# ── Monitoring Center (TZ #8) ─────────────────────────────────────────


@bp.get("/api/monitor/dashboard")
def api_monitor_dashboard():
    """Live dashboard — user mode by default, developer=1 for full data (TZ #8 §2, §15)."""
    developer = request.args.get("developer") == "1"
    if developer and not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    try:
        from core.monitoring_center import get_monitor

        project_id = request.args.get("project_id", "")
        mon = get_monitor(app_dir=APP_DIR)
        if project_id:
            mon.set_project(project_id)
        return jsonify({"ok": True, "dashboard": mon.get_dashboard(developer=developer)})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.get("/api/monitor/pipeline")
def api_monitor_pipeline():
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    try:
        from core.monitoring_center import get_monitor

        return jsonify({"ok": True, "pipeline": get_monitor(app_dir=APP_DIR).get_pipeline()})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.get("/api/monitor/agents")
def api_monitor_agents():
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    try:
        from core.monitoring_center import get_monitor

        return jsonify({"ok": True, **get_monitor(app_dir=APP_DIR).get_agents()})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.get("/api/monitor/resources")
def api_monitor_resources():
    try:
        from core.monitoring_center import get_monitor

        return jsonify({"ok": True, "resources": get_monitor(app_dir=APP_DIR).get_resources()})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.get("/api/monitor/models")
def api_monitor_models():
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    try:
        from core.monitoring_center import get_monitor

        return jsonify({"ok": True, **get_monitor(app_dir=APP_DIR).get_models()})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.get("/api/monitor/queues")
def api_monitor_queues():
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    try:
        from core.monitoring_center import get_monitor

        return jsonify({"ok": True, **get_monitor(app_dir=APP_DIR).get_queues()})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.get("/api/monitor/statistics")
def api_monitor_statistics():
    try:
        from core.monitoring_center import get_monitor

        return jsonify({"ok": True, "statistics": get_monitor(app_dir=APP_DIR).get_statistics()})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.get("/api/monitor/timeline")
def api_monitor_timeline():
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    try:
        from core.monitoring_center import get_monitor

        limit = int(request.args.get("limit", 200))
        return jsonify({
            "ok": True,
            "timeline": get_monitor(app_dir=APP_DIR).get_timeline(limit=limit),
        })
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.get("/api/monitor/history")
def api_monitor_history():
    try:
        from core.monitoring_center import get_monitor

        limit = int(request.args.get("limit", 50))
        project_id = request.args.get("project_id", "")
        return jsonify({
            "ok": True,
            "history": get_monitor(app_dir=APP_DIR).get_history(limit=limit, project_id=project_id),
        })
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.get("/api/monitor/diagnostics")
def api_monitor_diagnostics():
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    try:
        from core.monitoring_center import get_monitor

        mon = get_monitor(app_dir=APP_DIR)
        return jsonify({
            "ok": True,
            "diagnostics": mon.get_diagnostics(),
            "bottleneck": mon.get_bottleneck(),
        })
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.get("/api/monitor/developer")
def api_monitor_developer():
    """Full developer event stream (TZ #8 §14)."""
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    try:
        from core.monitoring_center import get_monitor

        return jsonify({"ok": True, "events": get_monitor(app_dir=APP_DIR).get_developer_events()})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.post("/api/monitor/export")
def api_monitor_export():
    """Export diagnostic report ZIP/JSON/HTML/PDF (TZ #8 §13)."""
    body = request.get_json(silent=True) or {}
    fmt = str(body.get("format") or request.args.get("format") or "zip").lower()
    if fmt not in ("zip", "json", "html", "pdf"):
        return jsonify({"ok": False, "error": "format must be zip|json|html|pdf"}), 400
    try:
        from core.monitoring_center import get_monitor

        mon = get_monitor(app_dir=APP_DIR)
        if fmt in ("json", "html", "pdf"):
            data = mon.export_report_bytes(fmt=fmt)
            from flask import Response

            mime = {
                "json": "application/json",
                "html": "text/html",
                "pdf": "application/pdf",
            }[fmt]
            return Response(data, mimetype=mime)
        result = mon.export_report(fmt=fmt, output_dir=APP_DIR / "data" / "reports")
        return jsonify({"ok": True, **result})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.get("/api/pipeline/recovery/status")
def api_pipeline_recovery_status():
    """Recovery Manager status: stats, parking queue, recent events (TZ #5)."""
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    try:
        from core.recovery_manager import get_recovery_manager

        return jsonify({"ok": True, "status": get_recovery_manager().get_status()})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.get("/api/pipeline/engine/status")
def api_pipeline_engine_status():
    """Adaptive chunk conveyor status (TZ #4)."""
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    try:
        from core.pipeline_engine import get_pipeline_engine

        eng = get_pipeline_engine()
        if eng is None:
            return jsonify({"ok": True, "active": False, "status": None})
        return jsonify({"ok": True, "active": True, "status": eng.get_status()})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.post("/api/pipeline/engine/pause")
def api_pipeline_engine_pause():
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    try:
        from core.pipeline_engine import get_pipeline_engine

        eng = get_pipeline_engine()
        if eng is None:
            return jsonify({"ok": False, "error": "no active engine"}), 404
        eng.pause()
        return jsonify({"ok": True, "paused": True})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.post("/api/pipeline/engine/resume")
def api_pipeline_engine_resume():
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    try:
        from core.pipeline_engine import get_pipeline_engine

        eng = get_pipeline_engine()
        if eng is None:
            return jsonify({"ok": False, "error": "no active engine"}), 404
        eng.resume()
        return jsonify({"ok": True, "paused": False})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.post("/api/pipeline/platform/trace")
def api_pipeline_platform_trace():
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    body = request.get_json(silent=True) or {}
    info = body.get("info") or {}
    task_id = str(body.get("task_id") or info.get("task_id") or "")
    from engines.pipeline_platform import build_platform_trace

    trace = build_platform_trace(info, task_id=task_id, app_dir=str(APP_DIR))
    return jsonify({"ok": True, "trace": trace})


@bp.post("/api/pipeline/platform/dev-view")
def api_pipeline_dev_view():
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    body = request.get_json(silent=True) or {}
    info = body.get("info") or {}
    task_id = str(body.get("task_id") or info.get("task_id") or "")
    from engines.pipeline_platform.dev_view import build_dev_pipeline_view

    view = build_dev_pipeline_view(info, task_id=task_id, app_dir=str(APP_DIR))
    return jsonify({"ok": True, "view": view})


@bp.get("/api/pipeline/platform/task/<task_id>")
def api_pipeline_task_trace(task_id: str):
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    info = _load_task_info(task_id)
    if info is None:
        return jsonify({"ok": False, "error": "task not found"}), 404
    from engines.pipeline_platform.dev_view import build_dev_pipeline_view

    view = build_dev_pipeline_view(info, task_id=task_id, app_dir=str(APP_DIR))
    return jsonify({"ok": True, "view": view})


@bp.post("/api/pipeline/platform/test-segment")
def api_pipeline_test_segment():
    """Run single segment through all stages independently (dev test)."""
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    body = request.get_json(silent=True) or {}
    text = str(body.get("text") or "")
    info = {
        "segments_data": [{"index": 0, "text": text, "source_text": text}],
        "source_lang": body.get("src_lang", "en"),
        "target_lang": body.get("tgt_lang", "uk"),
        "translation_audits": [
            {
                "index": 0,
                "source_text": text,
                "raw_translation": body.get("raw_translation", text),
                "final_text": body.get("final_text", text),
            }
        ],
    }
    from engines.pipeline_platform.orchestrator import build_context_from_info, run_segment_trace

    ctx = build_context_from_info(info, app_dir=str(APP_DIR))
    trace = run_segment_trace(ctx, 0)
    return jsonify({"ok": True, "trace": trace.to_dict()})


def _load_task_info(task_id: str) -> dict | None:
    try:
        from api.auto_dub_api import AUTO_TASKS, STATE_LOCK

        with STATE_LOCK:
            task = AUTO_TASKS.get(task_id)
            if task:
                return dict(task.get("info") or {})
    except Exception:
        pass
    return None
