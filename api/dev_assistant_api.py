"""Development Assistant API (TZ #10)."""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, request

APP_DIR = Path(__file__).parent.parent.resolve()
bp = Blueprint("dev_assistant_api", __name__)


def _dev_mode() -> bool:
    from engines.module_registry.registry import is_developer_session
    return is_developer_session(request_headers=dict(request.headers))


@bp.get("/api/assistant/status")
def api_assistant_status():
    try:
        from core.dev_assistant import get_dev_assistant
        return jsonify({"ok": True, "status": get_dev_assistant(app_dir=APP_DIR).get_status()})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.get("/api/assistant/analyze")
def api_assistant_analyze():
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    try:
        from core.dev_assistant import get_dev_assistant
        return jsonify({"ok": True, **get_dev_assistant(app_dir=APP_DIR).analyze()})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.post("/api/assistant/plan")
def api_assistant_plan():
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    body = request.get_json(silent=True) or {}
    task = str(body.get("task") or "")
    if not task:
        return jsonify({"ok": False, "error": "task required"}), 400
    try:
        from core.dev_assistant import get_dev_assistant
        return jsonify({"ok": True, **get_dev_assistant(app_dir=APP_DIR).plan(task)})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.post("/api/assistant/review")
def api_assistant_review():
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    body = request.get_json(silent=True) or {}
    files = body.get("files") or []
    try:
        from core.dev_assistant import get_dev_assistant
        return jsonify({
            "ok": True,
            **get_dev_assistant(app_dir=APP_DIR).review(files if files else None),
        })
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.get("/api/assistant/optimize")
def api_assistant_optimize():
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    try:
        from core.dev_assistant import get_dev_assistant
        return jsonify({"ok": True, **get_dev_assistant(app_dir=APP_DIR).optimize()})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.post("/api/assistant/document")
def api_assistant_document():
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    try:
        from core.dev_assistant import get_dev_assistant
        return jsonify({"ok": True, **get_dev_assistant(app_dir=APP_DIR).document(sync=True)})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.post("/api/assistant/pre-change")
def api_assistant_pre_change():
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    body = request.get_json(silent=True) or {}
    files = body.get("files") or []
    if not files:
        return jsonify({"ok": False, "error": "files required"}), 400
    try:
        from core.dev_assistant import get_dev_assistant
        return jsonify({
            "ok": True,
            **get_dev_assistant(app_dir=APP_DIR).pre_change(
                files, description=str(body.get("description") or ""),
            ),
        })
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.post("/api/assistant/explain")
def api_assistant_explain():
    body = request.get_json(silent=True) or {}
    topic = str(body.get("topic") or request.args.get("topic") or "")
    if not topic:
        return jsonify({"ok": False, "error": "topic required"}), 400
    try:
        from core.dev_assistant import get_dev_assistant
        return jsonify({"ok": True, **get_dev_assistant(app_dir=APP_DIR).explain(topic)})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.post("/api/assistant/estimate")
def api_assistant_estimate():
    body = request.get_json(silent=True) or {}
    task = str(body.get("task") or "")
    if not task:
        return jsonify({"ok": False, "error": "task required"}), 400
    try:
        from core.dev_assistant import get_dev_assistant
        return jsonify({"ok": True, **get_dev_assistant(app_dir=APP_DIR).estimate(task)})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.get("/api/assistant/brain/<filename>")
def api_assistant_brain_file(filename: str):
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    allowed = {
        "PROJECT.md", "ARCHITECTURE.md", "ROADMAP.md", "CODING_RULES.md",
        "UX_RULES.md", "PERFORMANCE.md", "CHANGELOG.md", "DECISIONS.md",
        "MEMORY.md", "KNOWN_ISSUES.md", "API_REFERENCE.md",
    }
    if filename not in allowed:
        return jsonify({"ok": False, "error": "unknown file"}), 404
    try:
        from core.architecture_engine import get_architecture_engine
        content = get_architecture_engine(app_dir=APP_DIR).read_brain(filename)
        return jsonify({"ok": True, "filename": filename, "content": content})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp.get("/api/assistant/debt")
def api_assistant_debt():
    if not _dev_mode():
        return jsonify({"ok": False, "error": "Developer mode required"}), 403
    try:
        from core.technical_debt import get_technical_debt_monitor
        return jsonify({"ok": True, **get_technical_debt_monitor(app_dir=APP_DIR).summary()})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 500
