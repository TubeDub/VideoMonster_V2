"""Dub Studio API."""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

APP_DIR = Path(__file__).parent.parent.resolve()
bp = Blueprint("dub_studio_api", __name__)


def _guard():
    from engines.dub_studio.config import require_dub_studio

    try:
        require_dub_studio()
    except PermissionError as e:
        return str(e)
    return None


def _svc():
    from engines.dub_studio.service import get_dub_studio_service

    return get_dub_studio_service(APP_DIR)


@bp.get("/api/dub-studio/status")
def api_dub_studio_status():
    blocked = _guard()
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    return jsonify({"ok": True, **_svc().status()})


@bp.get("/api/dub-studio/plugins")
def api_dub_studio_plugins():
    blocked = _guard()
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    from engines.dub_studio.fx.registry import list_plugins

    return jsonify({"ok": True, "plugins": list_plugins()})


@bp.get("/api/dub-studio/projects")
def api_dub_studio_projects():
    blocked = _guard()
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    return jsonify({"ok": True, "projects": _svc().list_projects()})


@bp.post("/api/dub-studio/projects")
def api_dub_studio_create():
    blocked = _guard()
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    data = request.get_json(silent=True) or {}
    project = _svc().create_project(title=str(data.get("title") or "Dub Studio Project"))
    return jsonify({"ok": True, "project": project.to_dict()})


@bp.post("/api/dub-studio/projects/import")
def api_dub_studio_import():
    blocked = _guard()
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    data = request.get_json(silent=True) or {}
    rel = str(data.get("review_path") or data.get("path") or "").strip()
    if not rel:
        return jsonify({"ok": False, "error": "review_path required"}), 400
    path = Path(rel)
    if not path.is_absolute():
        path = APP_DIR / "output" / path.name
    if not path.is_file():
        path = APP_DIR / rel
    if not path.is_file():
        return jsonify({"ok": False, "error": "review file not found"}), 404
    project = _svc().import_review(path, title=str(data.get("title") or path.stem))
    return jsonify({"ok": True, "project": project.to_dict()})


@bp.get("/api/dub-studio/projects/<project_id>")
def api_dub_studio_get(project_id: str):
    blocked = _guard()
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    project = _svc().get_project(project_id)
    if not project:
        return jsonify({"ok": False, "error": "not found"}), 404
    return jsonify({"ok": True, "project": project.to_dict()})


@bp.post("/api/dub-studio/projects/<project_id>/tracks/<track_id>/solo")
def api_dub_studio_solo(project_id: str, track_id: str):
    blocked = _guard()
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    data = request.get_json(silent=True) or {}
    project = _svc().set_track_solo(project_id, track_id, bool(data.get("solo", True)))
    return jsonify({"ok": True, "project": project.to_dict()})


@bp.post("/api/dub-studio/projects/<project_id>/fx/reorder")
def api_dub_studio_fx_reorder(project_id: str):
    blocked = _guard()
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    data = request.get_json(silent=True) or {}
    project = _svc().reorder_fx(
        project_id,
        track_id=data.get("track_id"),
        from_idx=int(data.get("from_idx", 0)),
        to_idx=int(data.get("to_idx", 0)),
    )
    return jsonify({"ok": True, "project": project.to_dict()})


@bp.patch("/api/dub-studio/projects/<project_id>/segments/<segment_id>/emotion")
def api_dub_studio_emotion(project_id: str, segment_id: str):
    blocked = _guard()
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    data = request.get_json(silent=True) or {}
    emotion = str(data.get("emotion") or "NEUTRAL")
    seg = _svc().set_segment_emotion(
        project_id,
        segment_id,
        emotion,
        regenerate=bool(data.get("regenerate")),
    )
    return jsonify({"ok": True, "segment": seg.to_dict()})


@bp.post("/api/dub-studio/projects/<project_id>/segments/<segment_id>/analyze-emotion")
def api_dub_studio_analyze_emotion(project_id: str, segment_id: str):
    blocked = _guard()
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    emo = _svc().analyze_segment_emotion(project_id, segment_id)
    return jsonify({"ok": True, "emotion": emo})


@bp.post("/api/dub-studio/projects/<project_id>/segments/<segment_id>/versions")
def api_dub_studio_add_version(project_id: str, segment_id: str):
    blocked = _guard()
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    data = request.get_json(silent=True) or {}
    audio_path = str(data.get("audio_path") or "").strip()
    if not audio_path:
        return jsonify({"ok": False, "error": "audio_path required"}), 400
    ver = _svc().add_segment_version(
        project_id,
        segment_id,
        audio_path=audio_path,
        label=str(data.get("label") or ""),
        source=str(data.get("source") or "user"),
    )
    return jsonify({"ok": True, "version": ver.to_dict()})


@bp.post("/api/dub-studio/projects/<project_id>/segments/<segment_id>/versions/<version_id>/select")
def api_dub_studio_select_version(project_id: str, segment_id: str, version_id: str):
    blocked = _guard()
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    seg = _svc().select_version(project_id, segment_id, version_id)
    return jsonify({"ok": True, "segment": seg.to_dict()})


@bp.post("/api/dub-studio/projects/<project_id>/segments/<segment_id>/stretch")
def api_dub_studio_stretch(project_id: str, segment_id: str):
    blocked = _guard()
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    result = _svc().stretch_active_version(project_id, segment_id)
    return jsonify(result)


@bp.post("/api/dub-studio/projects/<project_id>/export")
def api_dub_studio_export(project_id: str):
    blocked = _guard()
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    data = request.get_json(silent=True) or {}
    fmt = str(data.get("format") or "wav").lower()
    try:
        out = _svc().export_project(project_id, fmt=fmt)
        rel = out.relative_to(APP_DIR).as_posix()
        return jsonify({"ok": True, "file": rel, "path": str(out)})
    except KeyError:
        return jsonify({"ok": False, "error": "not found"}), 404
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.post("/api/dub-studio/projects/<project_id>/analyze-emotions")
def api_dub_studio_analyze_all(project_id: str):
    blocked = _guard()
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    data = request.get_json(silent=True) or {}
    audio = str(data.get("original_audio") or "").strip()
    audio_path = Path(audio) if audio else None
    if audio_path and not audio_path.is_absolute():
        audio_path = APP_DIR / "output" / audio_path.name
    try:
        n = _svc().analyze_all_emotions(project_id, original_audio=audio_path)
        project = _svc().get_project(project_id)
        return jsonify({"ok": True, "analyzed": n, "project": project.to_dict() if project else None})
    except KeyError:
        return jsonify({"ok": False, "error": "not found"}), 404


@bp.get("/api/dub-studio/plugins/all")
def api_dub_studio_plugins_all():
    blocked = _guard()
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    return jsonify({"ok": True, "plugins": _svc().list_track_plugins()})


@bp.patch("/api/dub-studio/projects/<project_id>/tracks/<track_id>")
def api_dub_studio_track_update(project_id: str, track_id: str):
    blocked = _guard()
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    data = request.get_json(silent=True) or {}
    try:
        track = _svc().update_track(
            project_id,
            track_id,
            muted=data.get("muted"),
            solo=data.get("solo"),
            volume=data.get("volume"),
            pan=data.get("pan"),
            monitor=data.get("monitor"),
            record_enabled=data.get("record_enabled"),
        )
        return jsonify({"ok": True, "track": track.to_dict()})
    except KeyError:
        return jsonify({"ok": False, "error": "not found"}), 404


@bp.post("/api/dub-studio/projects/<project_id>/tracks/<track_id>/plugins")
def api_dub_studio_track_add_plugin(project_id: str, track_id: str):
    blocked = _guard()
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    data = request.get_json(silent=True) or {}
    plugin_id = str(data.get("plugin_id") or "").strip()
    if not plugin_id:
        return jsonify({"ok": False, "error": "plugin_id required"}), 400
    try:
        track = _svc().add_track_plugin(project_id, track_id, plugin_id)
        return jsonify({"ok": True, "track": track.to_dict()})
    except KeyError:
        return jsonify({"ok": False, "error": "not found"}), 404


@bp.delete("/api/dub-studio/projects/<project_id>/tracks/<track_id>/plugins/<int:index>")
def api_dub_studio_track_remove_plugin(project_id: str, track_id: str, index: int):
    blocked = _guard()
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    try:
        track = _svc().remove_track_plugin(project_id, track_id, index)
        return jsonify({"ok": True, "track": track.to_dict()})
    except KeyError:
        return jsonify({"ok": False, "error": "not found"}), 404
    except IndexError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@bp.patch("/api/dub-studio/projects/<project_id>/tracks/<track_id>/plugins/<int:index>")
def api_dub_studio_track_patch_plugin(project_id: str, track_id: str, index: int):
    blocked = _guard()
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    data = request.get_json(silent=True) or {}
    if "enabled" not in data:
        return jsonify({"ok": False, "error": "enabled required"}), 400
    try:
        track = _svc().set_fx_enabled(
            project_id, track_id, index, enabled=bool(data.get("enabled"))
        )
        return jsonify({"ok": True, "track": track.to_dict()})
    except KeyError:
        return jsonify({"ok": False, "error": "not found"}), 404
    except IndexError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@bp.get("/api/dub-studio/download/<path:relpath>")
def api_dub_studio_download(relpath: str):
    blocked = _guard()
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    path = (APP_DIR / relpath).resolve()
    if not str(path).startswith(str((APP_DIR / "output" / "dub_studio").resolve())):
        return jsonify({"ok": False, "error": "forbidden"}), 403
    if not path.is_file():
        return jsonify({"ok": False, "error": "not found"}), 404
    return send_file(path, as_attachment=True, download_name=path.name)


@bp.post("/api/dub-studio/projects/<project_id>/record/punch-roll")
def api_dub_studio_punch_roll(project_id: str):
    blocked = _guard()
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    data = request.get_json(silent=True) or {}
    segment_id = str(data.get("segment_id") or "")
    anchor_ms = int(data.get("anchor_ms") or 0)
    pre_roll = int(data.get("pre_roll_ms") or 500)
    sess = _svc().recording.start_punch_roll(
        project_id=project_id,
        segment_id=segment_id,
        anchor_ms=anchor_ms,
        pre_roll_ms=pre_roll,
    )
    return jsonify({"ok": True, "session": sess.to_dict()})


@bp.post("/api/dub-studio/projects/<project_id>/preview-fx")
def api_dub_studio_preview_fx(project_id: str):
    blocked = _guard()
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    data = request.get_json(silent=True) or {}
    raw_path = str(data.get("input_path") or "").strip()
    input_path = Path(raw_path) if raw_path else None
    try:
        job_id = _svc().preview_fx(
            project_id,
            input_path=input_path,
            fx_slots=data.get("fx_chain"),
            track_id=data.get("track_id"),
            segment_id=data.get("segment_id"),
        )
        return jsonify({"ok": True, "job_id": job_id})
    except KeyError:
        return jsonify({"ok": False, "error": "not found"}), 404
    except FileNotFoundError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.get("/api/dub-studio/preview/<job_id>")
def api_dub_studio_preview_get(job_id: str):
    blocked = _guard()
    if blocked:
        return jsonify({"ok": False, "error": blocked}), 403
    buf = _svc().get_preview_buffer(job_id)
    if not buf:
        return jsonify({"ok": False, "status": "processing"}), 202
    from io import BytesIO

    return send_file(BytesIO(buf), mimetype="audio/wav", download_name=f"preview_{job_id}.wav")
