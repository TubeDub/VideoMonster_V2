from pathlib import Path
from flask import Blueprint, request, jsonify, send_file, abort

from engines.tts import generate_audio, cleanup_old_files, get_output_path, OUTPUT_DIR
from engines.cleaner import split_by_timing_map
from data.languages import VOICES, DEFAULT_VOICE

bp = Blueprint("tts_api", __name__)


def _parse_total_duration(s: str) -> float:
    """Парсит 'ЧЧ:ММ:СС' или 'ММ:СС' в секунды. Возвращает 0.0 при ошибке."""
    s = s.strip()
    if not s:
        return 0.0
    parts = s.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except (ValueError, IndexError):
        return 0.0


@bp.post("/api/tts")
def api_tts():
    """
    Генерирует аудио из текста через edge-tts.
    Опционально: прогоняет через Timing Engine.

    Параметры JSON:
      text           — текст для озвучки
      voice          — ID голоса (default: ru-RU-DmitryNeural)
      timing_map     — список тайм-кодов из Cleaner
      use_timing     — true/false — использовать ли Timing Engine
      timing_mode    — 'exact' | 'preserve_pauses' | 'match_total'
      total_duration — 'ЧЧ:ММ:СС' (только для match_total)
    """
    data = request.get_json(silent=True) or {}
    text = data.get("text", "").strip()
    voice = data.get("voice", DEFAULT_VOICE)
    timing_map = data.get("timing_map", [])
    use_timing = bool(data.get("use_timing", False))
    timing_mode = data.get("timing_mode", "exact")
    total_duration_str = data.get("total_duration", "")

    if not text:
        return jsonify({"error": "Нет текста для озвучки"}), 400

    cleanup_old_files()

    segments = split_by_timing_map(text, timing_map)

    try:
        files = generate_audio(text=text, voice=voice, segments=segments)
    except Exception as e:
        return jsonify({"error": f"Ошибка TTS: {e}"}), 500

    if not files:
        return jsonify({"error": "Нет сегментов для генерации"}), 400

    downloads = [f"/api/download/{f}" for f in files]
    streams = [f"/api/stream/{f}" for f in files]

    response = {
        "files": files,
        "count": len(files),
        "downloads": downloads,
        "streams": streams,
        "download": downloads[0],
        "stream": streams[0],
        "warnings": [],
    }

    # ── Timing Engine (если запрошен и есть тайминг-карта) ────────────
    if use_timing and timing_map:
        try:
            from engines.timing_engine import build_timed_audio
            import uuid as _uuid

            segment_paths = [str(OUTPUT_DIR / f) for f in files]
            timed_name = f"{_uuid.uuid4().hex[:8]}_timed.mp3"
            normalized_map = _normalize_timing_map(timing_map, len(files))
            timed_audio_obj, warnings = build_timed_audio(
                segment_paths=segment_paths,
                timing_map=normalized_map,
                mode="exact",
            )
            timed_path = OUTPUT_DIR / timed_name
            timed_audio_obj.export(str(timed_path), format="mp3")
            response["timed_file"] = timed_name
            response["timed_download"] = f"/api/download/{timed_name}"
            response["timed_stream"] = f"/api/stream/{timed_name}"
            response["warnings"] = warnings
        except ImportError:
            response["warnings"].append(
                "pydub не установлен — Timing Engine отключён. "
                "Выполните: pip install pydub"
            )
        except Exception as e:
            response["warnings"].append(f"Timing Engine: {e}")

    return jsonify(response)


def _normalize_timing_map(timing_map: list, segment_count: int) -> list:
    """Приводит timing_map к списку dict {start,end} в миллисекундах."""
    if not timing_map:
        out = []
        for i in range(segment_count):
            start = i * 3200
            out.append({"start": start, "end": start + 3000})
        return out

    out: list = []
    for i, item in enumerate(timing_map):
        if isinstance(item, dict) and item.get("start") is not None:
            start = int(item["start"])
            end = int(item.get("end", start + 3000))
            out.append({"start": start, "end": max(end, start + 100)})
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            start = int(item[0])
            end = int(item[1])
            out.append({"start": start, "end": max(end, start + 100)})
        else:
            start = i * 3200
            out.append({"start": start, "end": start + 3000})

    while len(out) < segment_count:
        i = len(out)
        start = i * 3200
        out.append({"start": start, "end": start + 3000})
    return out[:segment_count]


@bp.get("/api/download/<filename>")
def api_download(filename):
    """Скачивает MP3-файл."""
    path = get_output_path(filename)
    if path is None or not filename.endswith(".mp3"):
        abort(404)
    return send_file(
        str(path),
        as_attachment=True,
        download_name=filename,
        mimetype="audio/mpeg",
    )


@bp.get("/api/stream/<filename>")
def api_stream(filename):
    """Стримит MP3 для браузерного плеера."""
    path = get_output_path(filename)
    if path is None or not filename.endswith(".mp3"):
        abort(404)
    return send_file(str(path), mimetype="audio/mpeg")


@bp.get("/api/voices")
def api_voices():
    """Возвращает список голосов для заданного языка."""
    lang = request.args.get("lang", "ru")
    voice_list = VOICES.get(lang, VOICES.get("ru", []))
    return jsonify({"voices": voice_list, "lang": lang})


@bp.get("/api/languages")
def api_languages():
    """Возвращает все доступные языки."""
    from data.languages import LANGUAGES
    return jsonify({
        "languages": [
            {"name": k, "code": v} for k, v in LANGUAGES.items()
        ]
    })
