"""Universal Import — определяет тип файла и возвращает целевой режим UI."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request

APP_DIR = Path(__file__).parent.parent.resolve()
IMPORT_DIR = APP_DIR / "uploads" / "imports"
IMPORT_DIR.mkdir(parents=True, exist_ok=True)

bp = Blueprint("import_api", __name__)

VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".wmv", ".flv", ".m4v"}
AUDIO_EXT = {
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus",
    ".wma", ".aiff", ".aif", ".amr", ".ac3", ".ape", ".webm",
}
SUBS_EXT = {".srt", ".vtt", ".ass", ".ssa"}
TEXT_EXT = {".txt"}


def detect_import_target(filename: str) -> dict:
    ext = Path(filename).suffix.lower()
    if ext in VIDEO_EXT:
        return {"mode": "dub", "route": "/dub", "kind": "video"}
    if ext in AUDIO_EXT:
        return {"mode": "voice", "route": "/voice", "kind": "audio"}
    if ext in SUBS_EXT:
        return {"mode": "studio", "route": "/studio", "kind": "subtitles"}
    if ext in TEXT_EXT:
        return {"mode": "reader", "route": "/reader", "kind": "text"}
    return {"mode": "unknown", "route": "/", "kind": "unknown"}


@bp.post("/api/import/detect")
def api_import_detect():
    data = request.get_json(silent=True) or {}
    name = (data.get("filename") or "").strip()
    if not name:
        if "file" in request.files:
            name = request.files["file"].filename or ""
    if not name:
        return jsonify({"error": "filename required"}), 400
    target = detect_import_target(name)
    return jsonify({"ok": True, "filename": name, **target})


@bp.post("/api/import/upload")
def api_import_upload():
    """Upload file for universal import; returns import_id + target route."""
    if "file" not in request.files:
        return jsonify({"error": "Файл не передан"}), 400
    file = request.files["file"]
    name = file.filename or ""
    if not name:
        return jsonify({"error": "Имя файла пустое"}), 400

    target = detect_import_target(name)
    if target["kind"] == "unknown":
        return jsonify({"error": "Неподдерживаемый тип файла"}), 400

    import_id = uuid.uuid4().hex[:12]
    ext = Path(name).suffix.lower()
    save_name = f"{import_id}{ext}"
    save_path = IMPORT_DIR / save_name
    file.save(str(save_path))
    (IMPORT_DIR / f"{import_id}.meta.json").write_text(
        json.dumps({"original": name}, ensure_ascii=False),
        encoding="utf-8",
    )

    return jsonify(
        {
            "ok": True,
            "import_id": import_id,
            "filename": name,
            "saved_as": save_name,
            **target,
        }
    )


@bp.get("/api/import/load/<import_id>")
def api_import_load(import_id):
    """Load previously uploaded import file metadata/content."""
    safe = Path(import_id).name
    matches = list(IMPORT_DIR.glob(f"{safe}.*"))
    if not matches:
        return jsonify({"error": "Импорт не найден"}), 404

    path = matches[0]
    target = detect_import_target(path.name)
    ext = path.suffix.lower()

    payload = {
        "ok": True,
        "import_id": safe,
        "filename": path.name,
        "ext": ext,
        **target,
    }
    meta_path = IMPORT_DIR / f"{safe}.meta.json"
    if meta_path.exists():
        try:
            payload["original_filename"] = json.loads(meta_path.read_text(encoding="utf-8")).get("original")
        except (json.JSONDecodeError, OSError):
            pass

    if target["kind"] == "subtitles":
        from engines.subtitle_formats import parse_subtitles, segments_to_text, segments_to_timing_map

        raw = path.read_text(encoding="utf-8", errors="replace")
        segments = parse_subtitles(raw, ext)
        payload.update(
            {
                "segments": [s.to_dict() for s in segments],
                "text": segments_to_text(segments),
                "timing_map": segments_to_timing_map(segments),
            }
        )
    elif target["kind"] == "text":
        payload["text"] = path.read_text(encoding="utf-8", errors="replace")
    elif target["kind"] in ("audio", "video"):
        payload["upload_filename"] = path.name
        payload["path"] = f"uploads/imports/{path.name}"

    return jsonify(payload)
