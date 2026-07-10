"""TubeDub — Beta support API: отчёты, отзывы, обновления, диагностика."""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

from engines.app_version import version_info
from engines.beta_support import build_error_report, run_diagnostics, save_feedback
from engines.update_checker import apply_update, check_for_update
from engines.update_state import (
    load_update_state,
    record_apply_started,
    record_check_result,
)

APP_DIR = Path(__file__).parent.parent.resolve()
bp = Blueprint("beta_api", __name__)


@bp.get("/api/system/version")
def api_version():
    return jsonify(version_info())


@bp.get("/api/system/update-state")
def api_update_state():
    """Кэш последней проверки — без обращения к серверу обновлений."""
    state = load_update_state(APP_DIR)
    state["installed_version"] = state.get("installed_version") or version_info()["version"]
    return jsonify(state)


@bp.get("/api/system/check-updates")
def api_check_updates_cached():
    """Совместимость: отдаёт только сохранённое состояние."""
    return api_update_state()


@bp.post("/api/system/check-updates")
def api_check_updates_manual():
    """Ручная проверка — только по запросу пользователя."""
    result = check_for_update(APP_DIR)
    state = record_check_result(APP_DIR, result)
    return jsonify({**result, "state": state})


@bp.post("/api/system/apply-update")
def api_apply_update():
    data = request.get_json(silent=True) or {}
    url = (data.get("download_url") or "").strip()
    if not url:
        state = load_update_state(APP_DIR)
        url = (state.get("download_url") or "").strip()
    if not url:
        return jsonify({"ok": False, "error": "Нет URL обновления. Сначала проверьте обновления."}), 400
    record_apply_started(APP_DIR)
    result = apply_update(APP_DIR, url)
    status = 200 if result.get("ok") else 500
    return jsonify(result), status


@bp.get("/api/system/diagnostics")
def api_diagnostics():
    return jsonify(run_diagnostics(APP_DIR))


@bp.post("/api/support/error-report")
def api_error_report():
    data = request.get_json(silent=True) or {}
    result = build_error_report(
        APP_DIR,
        task_id=(data.get("task_id") or "").strip() or None,
        error_message=(data.get("error_message") or "").strip(),
        user_comment=(data.get("comment") or data.get("user_comment") or "").strip(),
        page=(data.get("page") or "").strip(),
        diagnostic=(data.get("diagnostic") or "").strip(),
    )
    if not result.get("ok"):
        return jsonify(result), 500
    return jsonify(
        {
            **result,
            "download_url": f"/api/support/download-report/{result['filename']}",
        }
    )


@bp.get("/api/support/download-report/<filename>")
def api_download_report(filename: str):
    safe = Path(filename).name
    path = APP_DIR / "output" / "reports" / safe
    if not path.is_file():
        return jsonify({"error": "not found"}), 404
    return send_file(path, as_attachment=True, download_name=safe)


@bp.post("/api/beta/feedback")
def api_feedback():
    data = request.get_json(silent=True) or {}
    stars = int(data.get("stars") or 0)
    if stars < 1:
        return jsonify({"ok": False, "error": "Укажите оценку от 1 до 5"}), 400
    result = save_feedback(
        APP_DIR,
        stars=stars,
        liked=(data.get("liked") or "").strip(),
        improve=(data.get("improve") or "").strip(),
        task_id=(data.get("task_id") or "").strip() or None,
    )
    return jsonify(result)
