"""Voice API — upload audio, STT, voiceover pipeline."""

from __future__ import annotations

import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request

from engines.audio_formats import convert_to_mp3, is_supported_audio
from engines.stt_engine import transcribe
from data.languages import LANG_CODE_TO_NAME

APP_DIR = Path(__file__).parent.parent.resolve()
UPLOADS_DIR = APP_DIR / "uploads"
IMPORTS_DIR = UPLOADS_DIR / "imports"
OUTPUT_DIR = APP_DIR / "output"
UPLOADS_DIR.mkdir(exist_ok=True)
IMPORTS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

bp = Blueprint("voice_api", __name__)


def _resolve_audio_path(filename: str) -> Path | None:
    raw = (filename or "").strip()
    if not raw:
        return None
    safe = Path(raw).name
    candidates = [
        UPLOADS_DIR / safe,
        IMPORTS_DIR / safe,
        APP_DIR / raw,
        APP_DIR / "uploads" / "imports" / safe,
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


@bp.post("/api/voice/upload")
def api_voice_upload():
    if "file" not in request.files:
        return jsonify({"error": "Файл не передан"}), 400
    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "Имя файла пустое"}), 400

    ext = Path(file.filename).suffix.lower()
    audio_id = uuid.uuid4().hex[:10]
    filename = f"voice_{audio_id}{ext}"
    path = UPLOADS_DIR / filename
    file.save(str(path))

    if not is_supported_audio(path):
        path.unlink(missing_ok=True)
        return jsonify({"error": f"Неподдерживаемый аудиоформат: {ext}"}), 400

    return jsonify(
        {
            "ok": True,
            "audio_id": audio_id,
            "filename": filename,
            "original_name": file.filename,
        }
    )


@bp.post("/api/voice/transcribe")
def api_voice_transcribe():
    data = request.get_json(silent=True) or {}
    filename = (data.get("filename") or "").strip()
    if not filename:
        return jsonify({"error": "filename required"}), 400

    src = _resolve_audio_path(filename)
    if not src:
        return jsonify({"error": "Аудиофайл не найден"}), 404

    language = data.get("language") or None
    if language in ("auto", "Auto", ""):
        language = None
    model_size = data.get("model_size") or "tiny"

    try:
        audio_path = convert_to_mp3(src, OUTPUT_DIR)
        text, srt_content, timing_map, detected = transcribe(
            str(audio_path),
            language=language,
            model_size=model_size,
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if not text.strip():
        return jsonify({"error": "Распознавание вернуло пустой текст"}), 400

    return jsonify(
        {
            "ok": True,
            "text": text,
            "srt": srt_content,
            "timing_map": timing_map,
            "detected": detected,
            "detected_name": LANG_CODE_TO_NAME.get(detected, detected),
            "segments": len(timing_map),
        }
    )
