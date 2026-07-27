"""Universal Import — определяет тип файла и возвращает целевой режим UI."""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request

from engines.storage.atomic import atomic_write_json

logger = logging.getLogger("tubedub.api.import")

APP_DIR = Path(__file__).parent.parent.resolve()
IMPORT_DIR = APP_DIR / "uploads" / "imports"
IMPORT_DIR.mkdir(parents=True, exist_ok=True)

bp = Blueprint("import_api", __name__)

_IMPORT_ID_RE = re.compile(r"^[a-f0-9]{8,32}$", re.IGNORECASE)

VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".wmv", ".flv", ".m4v"}
AUDIO_EXT = {
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus",
    ".wma", ".aiff", ".aif", ".amr", ".ac3", ".ape",
}
SUBS_EXT = {".srt", ".vtt", ".ass", ".ssa"}
TEXT_EXT = {".txt"}
_MEDIA_EXTS = VIDEO_EXT | AUDIO_EXT | SUBS_EXT | TEXT_EXT


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


def _safe_import_id(import_id: str) -> str | None:
    safe = Path(str(import_id or "")).name.strip()
    if not safe or safe != str(import_id).strip() or not _IMPORT_ID_RE.match(safe):
        return None
    return safe.lower()


def _meta_path(import_id: str) -> Path:
    return IMPORT_DIR / f"{import_id}.meta.json"


def _find_import_file(import_id: str) -> Path | None:
    """Return media/text file for import_id; never prefer ``*.meta.json``."""
    meta_name = f"{import_id}.meta.json"
    candidates = [
        p
        for p in IMPORT_DIR.glob(f"{import_id}.*")
        if p.is_file() and p.name != meta_name
    ]
    if not candidates:
        return None
    # Prefer known media extensions, then newest mtime.
    ranked = sorted(
        candidates,
        key=lambda p: (p.suffix.lower() not in _MEDIA_EXTS, -p.stat().st_mtime),
    )
    return ranked[0]


def _write_meta(import_id: str, payload: dict) -> None:
    atomic_write_json(_meta_path(import_id), payload)


def _read_meta(import_id: str) -> dict:
    path = _meta_path(import_id)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("import meta unreadable %s: %s", path, exc)
        return {}


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
    return jsonify({"ok": True, "filename": Path(name).name, **target})


@bp.post("/api/import/upload")
def api_import_upload():
    """Upload file for universal import; returns import_id + target route."""
    if "file" not in request.files:
        return jsonify({"error": "Файл не передан"}), 400
    file = request.files["file"]
    name = Path(file.filename or "").name
    if not name:
        return jsonify({"error": "Имя файла пустое"}), 400

    target = detect_import_target(name)
    if target["kind"] == "unknown":
        return jsonify({"error": "Неподдерживаемый тип файла"}), 400

    import_id = uuid.uuid4().hex[:12]
    ext = Path(name).suffix.lower()
    if ext not in _MEDIA_EXTS:
        return jsonify({"error": "Неподдерживаемый тип файла"}), 400
    save_name = f"{import_id}{ext}"
    save_path = IMPORT_DIR / save_name
    try:
        file.save(str(save_path))
    except OSError as exc:
        logger.exception("import upload save failed")
        return jsonify({"error": f"Не удалось сохранить файл: {exc}"}), 500

    _write_meta(
        import_id,
        {
            "original": name,
            "saved_as": save_name,
            "kind": target["kind"],
            "mode": target["mode"],
            "route": target["route"],
            "created_at": time.time(),
            "size_bytes": save_path.stat().st_size if save_path.is_file() else 0,
        },
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


@bp.get("/api/import/list")
def api_import_list():
    """List recent imports (metadata only)."""
    items: list[dict] = []
    for meta_path in sorted(IMPORT_DIR.glob("*.meta.json"), key=lambda p: -p.stat().st_mtime):
        import_id = meta_path.name[: -len(".meta.json")]
        if not _safe_import_id(import_id):
            continue
        meta = _read_meta(import_id)
        media = _find_import_file(import_id)
        items.append(
            {
                "import_id": import_id,
                "original": meta.get("original") or (media.name if media else ""),
                "kind": meta.get("kind")
                or (detect_import_target(media.name)["kind"] if media else "unknown"),
                "route": meta.get("route") or "/",
                "created_at": meta.get("created_at"),
                "size_bytes": meta.get("size_bytes")
                or (media.stat().st_size if media and media.is_file() else 0),
                "exists": bool(media and media.is_file()),
            }
        )
    return jsonify({"ok": True, "imports": items, "count": len(items)})


@bp.get("/api/import/load/<import_id>")
def api_import_load(import_id):
    """Load previously uploaded import file metadata/content."""
    safe = _safe_import_id(import_id)
    if not safe:
        return jsonify({"error": "Некорректный import_id"}), 400

    path = _find_import_file(safe)
    if not path:
        return jsonify({"error": "Импорт не найден"}), 404

    meta = _read_meta(safe)
    original = meta.get("original") or path.name
    target = detect_import_target(original if Path(original).suffix else path.name)
    if target["kind"] == "unknown":
        target = detect_import_target(path.name)
    ext = path.suffix.lower()

    payload = {
        "ok": True,
        "import_id": safe,
        "filename": path.name,
        "original_filename": original,
        "ext": ext,
        **target,
    }

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
        # Forward-slash relative path for UI/Windows clients.
        payload["path"] = f"uploads/imports/{path.name}"

    return jsonify(payload)


@bp.delete("/api/import/<import_id>")
def api_import_delete(import_id):
    """Delete an import media file and its metadata."""
    safe = _safe_import_id(import_id)
    if not safe:
        return jsonify({"ok": False, "error": "Некорректный import_id"}), 400

    removed = 0
    media = _find_import_file(safe)
    if media and media.is_file():
        try:
            media.unlink()
            removed += 1
        except OSError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500
    meta = _meta_path(safe)
    if meta.is_file():
        try:
            meta.unlink()
            removed += 1
        except OSError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500
    if removed == 0:
        return jsonify({"ok": False, "error": "Импорт не найден"}), 404
    return jsonify({"ok": True, "removed": removed, "import_id": safe})
