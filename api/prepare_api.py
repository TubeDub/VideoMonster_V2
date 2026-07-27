"""Prepare components API — user-facing «Подготовка компонентов»."""

from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path
from typing import Any

from flask import Blueprint, Response, jsonify, request, stream_with_context

APP_DIR = Path(__file__).parent.parent.resolve()
bp = Blueprint("prepare_api", __name__)

_JOBS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()


def _job_snapshot(job_id: str) -> dict | None:
    with _LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job else None


@bp.post("/api/prepare/check")
def api_prepare_check():
    from engines.model_manager.bundled import is_bundled_pair
    from engines.model_manager.config import needs_storage_wizard
    from engines.model_manager.estimate import estimate_profile_download_mb
    from engines.model_manager import get_storage_status, is_profile_ready, plan_route
    from engines.model_manager.labels import label

    data = request.get_json(silent=True) or {}
    src = data.get("source_lang") or "en"
    tgt = data.get("target_lang") or "ru"
    whisper = data.get("whisper_size") or "tiny"
    ocr = bool(data.get("ocr_enabled"))
    feature = str(data.get("feature") or "dub").strip().lower()
    ready = is_profile_ready(
        APP_DIR, src, tgt, whisper_size=whisper, ocr_enabled=ocr, feature=feature
    )
    route_plan = plan_route(APP_DIR, src, tgt).to_public_dict()
    storage = get_storage_status(APP_DIR)
    lru = storage.get("lru_candidates") or []
    est_mb = estimate_profile_download_mb(
        APP_DIR, src, tgt, whisper_size=whisper, ocr_enabled=ocr, feature=feature
    )
    disk_free = float(storage.get("disk_free_gb", -1))
    need_download = not ready
    disk_warning = need_download and disk_free >= 0 and disk_free * 1024 < est_mb + 500
    public_lru = [
        {
            "id": c.get("id"),
            "variant": c.get("variant"),
            "label": c.get("label") or label(c.get("id", "")),
            "size_mb": c.get("size_mb", 0),
        }
        for c in lru
    ]
    return jsonify(
        {
            "ready": ready,
            "prepare": not ready,
            "bundled_pair": is_bundled_pair(APP_DIR, src, tgt),
            "needs_storage_wizard": needs_storage_wizard(APP_DIR),
            "storage_over_limit": bool(lru),
            "storage_needs_confirm": bool(lru) and storage.get("require_confirm_lru", True),
            "storage_total_gb": storage.get("total_gb", 0),
            "storage_max_gb": storage.get("max_storage_gb", 10),
            "disk_free_gb": disk_free,
            "estimated_download_mb": est_mb,
            "disk_warning": disk_warning,
            "disk_warning_message": (
                f"Свободно {disk_free:.1f} ГБ — для загрузки нужно около {est_mb:.0f} МБ"
                if disk_warning
                else ""
            ),
            "lru_candidates": public_lru,
            "route_plan": route_plan,
            "primary_route": route_plan.get("primary_route", ""),
        }
    )


@bp.post("/api/prepare/lru")
def api_prepare_lru():
    from engines.model_manager import apply_lru_if_allowed

    data = request.get_json(silent=True) or {}
    confirmed = bool(data.get("confirmed", False))
    result = apply_lru_if_allowed(APP_DIR, confirmed=confirmed)
    status = 200 if result.get("ok") or result.get("needs_confirm") else 400
    return jsonify(result), status


@bp.get("/api/prepare/storage/drives")
def api_prepare_drives():
    from engines.model_manager import list_drives

    return jsonify({"drives": list_drives()})


@bp.get("/api/prepare/storage")
def api_prepare_storage_status():
    from engines.model_manager import get_storage_status, list_drives
    from engines.model_manager.config import needs_storage_wizard

    st = get_storage_status(APP_DIR)
    return jsonify(
        {
            "needs_wizard": needs_storage_wizard(APP_DIR),
            "storage_root": st.get("storage_root", ""),
            "total_gb": st.get("total_gb", 0),
            "disk_free_gb": st.get("disk_free_gb", -1),
            "drives": list_drives(),
        }
    )


@bp.post("/api/prepare/storage/skip")
def api_prepare_storage_skip():
    from engines.model_manager.config import mark_storage_wizard_done

    mark_storage_wizard_done(APP_DIR)
    return jsonify({"ok": True})


@bp.post("/api/prepare/storage/root")
def api_prepare_storage_root():
    from engines.model_manager import set_storage_root
    from engines.model_manager.config import load_config, mark_storage_wizard_done, save_config
    from engines.request_guards import is_local_request

    if not is_local_request(request):
        return jsonify({"error": "localhost_only", "ok": False}), 403

    cfg = load_config(APP_DIR)
    data = request.get_json(silent=True) or {}
    path = str(data.get("path", "")).strip()
    if not path:
        return jsonify({"error": "path required"}), 400
    base = Path(path)
    # Must be an absolute existing directory (drive / folder chosen in UI wizard).
    if not base.is_absolute() or not base.exists() or not base.is_dir():
        return jsonify({"error": "path must be an existing absolute directory"}), 400
    sub = data.get("subdir") or "VideoMonster/models"
    # Prevent path traversal via subdir (e.g. ../../Windows).
    sub_parts = Path(str(sub)).parts
    if any(p in ("..", "") for p in sub_parts) or Path(str(sub)).is_absolute():
        return jsonify({"error": "invalid subdir"}), 400
    target = base / sub if sub else base
    try:
        target_resolved = target.resolve()
        if not str(target_resolved).startswith(str(base.resolve())):
            return jsonify({"error": "invalid storage target"}), 400
    except OSError as exc:
        return jsonify({"error": str(exc)}), 400
    result = set_storage_root(APP_DIR, target_resolved)
    if result.get("ok"):
        cfg = load_config(APP_DIR)
        cfg["storage_wizard_done"] = True
        save_config(APP_DIR, cfg)
    return jsonify(result)


@bp.post("/api/prepare/start")
def api_prepare_start():
    from engines.model_manager import PrepareProgress, ensure_profile

    data = request.get_json(silent=True) or {}
    src = data.get("source_lang") or "en"
    tgt = data.get("target_lang") or "ru"
    whisper = data.get("whisper_size") or "tiny"
    ocr = bool(data.get("ocr_enabled"))
    feature = str(data.get("feature") or "dub").strip().lower()
    ui_lang = (data.get("ui_lang") or "ru").split("-")[0]

    job_id = uuid.uuid4().hex[:12]
    with _LOCK:
        _JOBS[job_id] = {
            "id": job_id,
            "status": "running",
            "percent": 0.0,
            "components": [],
            "events": [],
            "ready": False,
            "error": "",
            "error_code": "",
            "started_at": time.time(),
            "current_label": "",
            "current_phase": "",
            "current_detail": "",
        }

    def _run():
        def _cb(p: PrepareProgress) -> None:
            with _LOCK:
                j = _JOBS.get(job_id)
                if not j:
                    return
                evt = {
                    "type": "progress" if p.phase != "done" else "done",
                    "percent": p.percent,
                    "label": p.label,
                    "component_id": p.component_id,
                    "phase": p.phase,
                    "detail": p.detail or "",
                }
                j["events"].append(evt)
                j["percent"] = p.percent
                j["current_label"] = p.label
                j["current_phase"] = p.phase
                j["current_detail"] = p.detail or ""
                if p.component_id:
                    found = next((c for c in j["components"] if c["id"] == p.component_id), None)
                    st = "ready" if p.phase in ("ready", "verify", "done") else "working"
                    if found:
                        found["status"] = st
                    else:
                        j["components"].append({"id": p.component_id, "label": p.label, "status": st})

        result = ensure_profile(
            APP_DIR,
            src,
            tgt,
            whisper_size=whisper,
            ocr_enabled=ocr,
            feature=feature,
            ui_lang=ui_lang,
            progress_cb=_cb,
            job_id=job_id,
        )
        with _LOCK:
            j = _JOBS.get(job_id)
            if j:
                j["status"] = "done" if result.ready else "error"
                j["ready"] = result.ready
                j["error"] = result.error
                j["error_code"] = result.error_code
                j["percent"] = 100.0 if result.ready else j.get("percent", 0)
                j["events"].append({"type": "done" if result.ready else "error", "ready": result.ready, "error": result.error, "error_code": result.error_code})

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"job_id": job_id, "status": "running"})


@bp.get("/api/prepare/status/<job_id>")
def api_prepare_status(job_id: str):
    job = _job_snapshot(job_id)
    if not job:
        return jsonify({"error": "not_found"}), 404
    return jsonify(
        {
            "job_id": job_id,
            "status": job.get("status"),
            "percent": job.get("percent", 0),
            "ready": job.get("ready", False),
            "components": job.get("components", []),
            "error": job.get("error", ""),
            "error_code": job.get("error_code", ""),
            "current_label": job.get("current_label", ""),
            "current_phase": job.get("current_phase", ""),
            "current_detail": job.get("current_detail", ""),
            "elapsed_sec": max(0, int(time.time() - float(job.get("started_at", time.time())))),
        }
    )


@bp.get("/api/prepare/stream/<job_id>")
def api_prepare_stream(job_id: str):
    import json
    import time

    def generate():
        sent = 0
        for _ in range(600):
            with _LOCK:
                job = _JOBS.get(job_id)
                if not job:
                    yield f"data: {json.dumps({'type': 'error', 'error': 'not_found'})}\n\n"
                    return
                events = job.get("events", [])[sent:]
                status = job.get("status")
            for evt in events:
                sent += 1
                yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
            if status in ("done", "error"):
                return
            time.sleep(0.4)

    return Response(stream_with_context(generate()), mimetype="text/event-stream")
