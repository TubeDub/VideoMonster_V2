"""Voice API — upload audio, STT, cloning, assignment, voiceover pipeline."""

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
VOICE_MEM_DIR = OUTPUT_DIR / "voice_memory"
UPLOADS_DIR.mkdir(exist_ok=True)
IMPORTS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
VOICE_MEM_DIR.mkdir(parents=True, exist_ok=True)

# Soft cap for uploaded reference / voice samples (bytes).
_MAX_VOICE_UPLOAD_BYTES = 50 * 1024 * 1024

bp = Blueprint("voice_api", __name__)


def _resolve_audio_path(filename: str) -> Path | None:
    from engines.path_safety import resolve_under_roots

    raw = (filename or "").strip()
    if not raw:
        return None
    return resolve_under_roots(
        raw,
        [UPLOADS_DIR, IMPORTS_DIR, OUTPUT_DIR],
        basename_fallback=True,
    )


def _memory_path(project_id: str) -> Path:
    safe = "".join(c for c in (project_id or "default") if c.isalnum() or c in "-_")[:64]
    return VOICE_MEM_DIR / f"{safe or 'default'}.json"


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

    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    if size <= 0:
        path.unlink(missing_ok=True)
        return jsonify({"error": "Пустой аудиофайл"}), 400
    if size > _MAX_VOICE_UPLOAD_BYTES:
        path.unlink(missing_ok=True)
        return jsonify({"error": "Файл слишком большой (лимит 50 МБ)"}), 400

    if not is_supported_audio(path):
        path.unlink(missing_ok=True)
        return jsonify({"error": f"Неподдерживаемый аудиоформат: {ext}"}), 400

    return jsonify(
        {
            "ok": True,
            "audio_id": audio_id,
            "filename": filename,
            "original_name": file.filename,
            "size_bytes": size,
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


@bp.get("/api/voice/clone/status")
def api_voice_clone_status():
    """Report whether a real cloning adapter is available (vs NullClone)."""
    try:
        from engines.voice_platform.cloning import clone_readiness

        ready = clone_readiness()
        # Prefer RU message as primary `message` / `error` for UI.
        return jsonify(
            {
                **ready,
                "message_ru": ready.get("message"),
                "message_en": (
                    None
                    if ready.get("available")
                    else "Voice cloning unavailable — install xtts/coqui, openvoice, "
                    "fishspeech, or cosyvoice"
                ),
            }
        )
    except Exception as e:
        return jsonify({"ok": False, "available": False, "error": str(e)}), 500


@bp.post("/api/voice/clone")
def api_voice_clone():
    """Clone-synthesize text from a reference WAV via Voice Platform adapter."""
    data = request.get_json(silent=True) or {}
    text = str(data.get("text") or "").strip()
    reference = (data.get("reference") or data.get("filename") or "").strip()
    language = data.get("language") or None

    if not text:
        return jsonify({"error": "text required"}), 400
    if not reference:
        return jsonify({"error": "reference (filename) required"}), 400

    ref_path = _resolve_audio_path(reference)
    if not ref_path or not ref_path.is_file():
        return jsonify({"error": "Reference audio not found"}), 404

    try:
        from engines.voice_platform.cloning import clone_readiness, clone_voice

        ready = clone_readiness()
        if not ready.get("available"):
            # Structured 503: UI shows «нужен движок X», not a bare status code.
            ru = ready.get("message") or (
                "Клонирование голоса недоступно — нужен движок "
                "xtts/coqui, openvoice, fishspeech или cosyvoice"
            )
            return jsonify(
                {
                    "ok": False,
                    "error": ru,
                    "error_code": ready.get("error_code") or "CLONE_ENGINE_MISSING",
                    "message": ru,
                    "message_ru": ru,
                    "message_en": (
                        "Voice cloning unavailable — install xtts/coqui, openvoice, "
                        "fishspeech, or cosyvoice"
                    ),
                    "adapter_id": ready.get("adapter_id") or "clone-null",
                    "required_engines": ready.get("required_engines") or [],
                    "missing_engines": ready.get("missing_engines") or [],
                    "available_engines": ready.get("available_engines") or [],
                    "hint": ready.get("hint"),
                }
            ), 503

        out_name = f"clone_{uuid.uuid4().hex[:10]}.wav"
        out_path = OUTPUT_DIR / out_name
        result = clone_voice(
            text,
            str(ref_path),
            str(out_path),
            language=language,
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if not getattr(result, "ok", False) or not out_path.is_file():
        return jsonify(
            {
                "ok": False,
                "error": getattr(result, "error", None) or "Clone synthesis failed",
                "meta": getattr(result, "meta", None) or {},
            }
        ), 500

    return jsonify(
        {
            "ok": True,
            "file": out_name,
            "download": f"/api/download/{out_name}" if out_name.endswith(".mp3") else None,
            "path": str(out_path),
            "provider": getattr(result, "provider", None),
            "elapsed_ms": getattr(result, "elapsed_ms", None),
        }
    )


@bp.post("/api/voice/assign")
def api_voice_assign():
    """Assign (and lock) a voice to a speaker in project VoiceMemory."""
    data = request.get_json(silent=True) or {}
    project_id = str(data.get("project_id") or data.get("task_id") or "default").strip()
    speaker_uuid = str(data.get("speaker_uuid") or data.get("speaker") or "").strip()
    voice_uuid = str(data.get("voice_uuid") or data.get("voice") or "").strip()
    force = bool(data.get("force", False))
    style_profile = str(data.get("style_profile") or "Documentary")
    emotion_profile = str(data.get("emotion_profile") or "calm")
    language = str(data.get("language") or "")

    if not speaker_uuid or not voice_uuid:
        return jsonify({"error": "speaker_uuid and voice_uuid required"}), 400

    try:
        from engines.voice_platform import VoiceMemory

        path = _memory_path(project_id)
        mem = VoiceMemory.load(path) if path.is_file() else VoiceMemory(project_id=project_id)
        ident = mem.assign(
            speaker_uuid,
            voice_uuid,
            style_profile=style_profile,
            emotion_profile=emotion_profile,
            language=language,
            force=force,
        )
        mem.save(path)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e), "locked": True}), 409
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify(
        {
            "ok": True,
            "project_id": project_id,
            "speaker": ident.to_dict() if hasattr(ident, "to_dict") else {
                "speaker_uuid": speaker_uuid,
                "voice_uuid": voice_uuid,
            },
            "memory_path": str(path),
        }
    )


@bp.post("/api/voice/plan")
def api_voice_plan():
    """Plan multi-speaker voices for speech units (no synthesis)."""
    data = request.get_json(silent=True) or {}
    units = data.get("speech_units") or data.get("segments") or []
    if not isinstance(units, list) or not units:
        return jsonify({"error": "speech_units (list) required"}), 400

    project_id = str(data.get("project_id") or data.get("task_id") or "").strip()
    language = str(data.get("language") or "ru")
    style = str(data.get("style") or "Movie")
    preferred_voice = data.get("preferred_voice") or data.get("voice")
    preferred_voices = data.get("preferred_voices") or {}

    try:
        from engines.voice_platform import VoiceMemory, plan_project_voices

        mem = None
        mem_path = _memory_path(project_id) if project_id else None
        if mem_path and mem_path.is_file():
            mem = VoiceMemory.load(mem_path)
        payload = plan_project_voices(
            units,
            project_id=project_id,
            style=style,
            language=language,
            preferred_voice=preferred_voice,
            preferred_voices=preferred_voices if isinstance(preferred_voices, dict) else {},
            memory=mem,
        )
        if mem_path and payload.get("memory"):
            try:
                mem_dict = payload["memory"]
                if isinstance(mem_dict, dict):
                    mem_path.parent.mkdir(parents=True, exist_ok=True)
                    import json

                    mem_path.write_text(
                        json.dumps(mem_dict, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    payload["memory_path"] = str(mem_path)
            except OSError:
                pass
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({"ok": True, **payload})


@bp.get("/api/voice/memory/<project_id>")
def api_voice_memory(project_id: str):
    path = _memory_path(project_id)
    if not path.is_file():
        return jsonify({"ok": True, "project_id": project_id, "speakers": {}, "exists": False})
    try:
        from engines.voice_platform import VoiceMemory

        mem = VoiceMemory.load(path)
        return jsonify({"ok": True, "exists": True, **mem.to_dict()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
