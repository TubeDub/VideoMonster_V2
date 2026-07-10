import json
import uuid
from pathlib import Path

from flask import Blueprint, request, jsonify, send_file, abort

APP_DIR = Path(__file__).parent.parent.resolve()
OUTPUT_DIR = APP_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

DEFAULT_VOICE = "ru-RU-DmitryNeural"
VMR_VERSION = "2.0"

bp = Blueprint("files_api", __name__)


@bp.post("/api/save_vmr")
def api_save_vmr():
    """
    Сохраняет документ в формате VMR (VideoMonster Reader).
    Один файл = весь проект: тексты, тайминг, закладки, заметки, настройки.
    """
    data = request.get_json(silent=True) or {}

    document = {
        "format": "VMR",
        "version": VMR_VERSION,
        # ── Тексты ────────────────────────────────────────────────────
        "source_text": data.get("source_text", ""),
        "translated_text": data.get("translated_text", ""),
        "processed_text": data.get("processed_text", ""),
        # ── Тайминг ───────────────────────────────────────────────────
        "timing_map": data.get("timing_map", []),
        "review_items": data.get("review_items", []),
        # ── Reader ────────────────────────────────────────────────────
        "reading_position": data.get("reading_position", 0),
        "bookmarks": data.get("bookmarks", []),
        "reader_settings": {
            "font_size": data.get("reader_font_size", 17),
            "show_source": data.get("show_source", True),
            "show_translated": data.get("show_translated", True),
            "pane_mode": data.get("pane_mode", "translated"),
        },
        # ── TTS-настройки ─────────────────────────────────────────────
        "tts_settings": {
            "voice": data.get("voice", DEFAULT_VOICE),
            "speed": data.get("speed", 0),
            "volume": data.get("volume", 0),
            "timing_mode": data.get("timing_mode", "exact"),
            "total_duration": data.get("total_duration", ""),
            "use_timing": data.get("use_timing", False),
        },
        # ── Заметки пользователя ─────────────────────────────────────
        "notes": data.get("notes", ""),
        # ── Дубляж ───────────────────────────────────────────────────
        "dub_settings": {
            "video_path": data.get("video_path", ""),
            "output_format": data.get("output_format", "mp4"),
            "replace_audio": data.get("replace_audio", True),
            "keep_original_audio": data.get("keep_original_audio", False),
            "audio_offset_ms": data.get("audio_offset_ms", 0),
        },
        # ── Мета ─────────────────────────────────────────────────────
        "reader": {
            "standalone_supported": True,
            "future_export_exe": True,
        },
    }

    filename = f"doc_{uuid.uuid4().hex[:8]}.vmr"
    path = OUTPUT_DIR / filename

    with open(path, "w", encoding="utf-8") as f:
        json.dump(document, f, ensure_ascii=False, indent=2)

    return jsonify({"download": f"/api/download_vmr/{filename}", "filename": filename})


@bp.get("/api/download_vmr/<filename>")
def api_download_vmr(filename):
    """Скачивает VMR-документ."""
    safe = Path(filename).name
    path = OUTPUT_DIR / safe

    if not path.exists() or not safe.endswith(".vmr"):
        abort(404)

    return send_file(
        str(path),
        as_attachment=True,
        download_name=safe,
        mimetype="application/json",
    )


@bp.post("/api/save_txt")
def api_save_txt():
    """Сохраняет текст как TXT-файл."""
    data = request.get_json(silent=True) or {}
    text = data.get("text", "")
    name = data.get("name", "document")

    filename = f"{name}_{uuid.uuid4().hex[:6]}.txt"
    path = OUTPUT_DIR / filename

    with open(path, "w", encoding="utf-8") as f:
        f.write(text)

    return jsonify({"download": f"/api/dl_txt/{filename}"})


@bp.get("/api/dl_txt/<filename>")
def api_dl_txt(filename):
    """Скачивает TXT-файл."""
    safe = Path(filename).name
    path = OUTPUT_DIR / safe

    if not path.exists() or not safe.endswith(".txt"):
        abort(404)

    return send_file(
        str(path),
        as_attachment=True,
        download_name=safe,
        mimetype="text/plain",
    )
