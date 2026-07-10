"""DDF API — OpenDDF diagnostic report endpoint.

GET /api/ddf/<task_id>  →  JSON report for the given task.
"""

from __future__ import annotations

import logging
from pathlib import Path

from flask import Blueprint, jsonify

from engines.open_ddf import open_ddf

logger = logging.getLogger("tubedub.ddf_api")

bp = Blueprint("ddf_api", __name__)


@bp.get("/api/ddf/<task_id>")
def api_get_ddf(task_id: str):
    """Return the OpenDDF diagnostic report for task_id.

    Checks in-memory store first, then falls back to the persisted
    output/ddf_{task_id}.json file.

    Returns:
        200 + {ok, task_id, agents, segment_attention, summary, ...}
        404 + {ok:false, error} when no report exists for task_id
    """
    safe = Path(task_id).name
    if not safe or safe != task_id:
        return jsonify({"ok": False, "error": "invalid task_id"}), 400

    report = open_ddf.get_report(safe)
    if report.get("error") == "no_data":
        loaded = open_ddf.load(safe)
        if not loaded:
            return jsonify({"ok": False, "error": "DDF report not found", "task_id": safe}), 404
        report = loaded

    return jsonify({"ok": True, **report})
