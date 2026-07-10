"""TubeDub first-run preparation API — component progress + install log."""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify

from engines.install_log import read_tail

logger = logging.getLogger("tubedub.api.system_prepare")

bp = Blueprint("system_prepare_api", __name__)


@bp.get("/api/system/prepare-status")
def api_prepare_status():
    from engines.system_prepare import get_prepare_status, start_background_prepare

    start_background_prepare()
    return jsonify(get_prepare_status())


@bp.get("/api/system/prepare-log")
def api_prepare_log():
    return jsonify({"lines": read_tail(100)})


@bp.post("/api/system/prepare-done")
def api_prepare_done():
    from engines.system_prepare import mark_prepared

    mark_prepared()
    return jsonify({"ok": True})


@bp.post("/api/system/prepare-retry")
def api_prepare_retry():
    from engines.system_prepare import reset_prepared, start_background_prepare

    reset_prepared()
    start_background_prepare(force=True)
    return jsonify({"ok": True})
