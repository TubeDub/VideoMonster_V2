from pathlib import Path
from flask import Blueprint, request, jsonify, send_file, abort

from engines.tts import generate_audio, cleanup_old_files, get_output_path, OUTPUT_DIR
from engines.cleaner import split_by_timing_map
from data.languages import VOICES, DEFAULT_VOICE

bp = Blueprint("tts_api", __name__)


def _parse_total_duration(s: str) -> float:
    """Парсит 'ЧЧ:ММ:СС' или 'ММ:СС' в секунды. Возвращает 0.0 при ошибке."""
    s = (s or "").strip()
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
      rate / pitch   — Edge-TTS rate/pitch overrides
      engine_id      — TTS engine id (default edge-offline)
      emotion        — optional emotion hint for engines that support it
      timing_map     — список тайм-кодов из Cleaner
      use_timing     — true/false — использовать ли Timing Engine
      timing_mode    — 'exact' | 'preserve_pauses' | 'match_total' (engine: exact only)
      total_duration — 'ЧЧ:ММ:СС' or seconds (target_duration_ms for Timing Engine)
    """
    data = request.get_json(silent=True) or {}
    text = data.get("text", "").strip()
    voice = data.get("voice", DEFAULT_VOICE)
    timing_map = data.get("timing_map", [])
    use_timing = bool(data.get("use_timing", False))
    timing_mode = str(data.get("timing_mode") or "exact").strip().lower() or "exact"
    total_duration_str = data.get("total_duration", "")
    rate = data.get("rate")
    pitch = data.get("pitch")
    engine_id = data.get("engine_id")
    emotion = data.get("emotion")
    task_id = data.get("task_id")
    volume = data.get("volume", data.get("mykyta_volume"))
    length_scale = data.get("length_scale", data.get("mykyta_length_scale"))

    if not text:
        return jsonify({"error": "Нет текста для озвучки"}), 400

    cleanup_old_files()

    segments = split_by_timing_map(text, timing_map)

    try:
        from engines.tts_backends import (
            normalize_backend_name,
            resolve_mykyta_controls,
            set_pipeline_mykyta_controls,
        )

        eid = normalize_backend_name(engine_id)
        ctx = None
        if eid == "tts_uk":
            mk = resolve_mykyta_controls(
                {
                    "rate": rate,
                    "pitch": pitch,
                    "volume": volume,
                    "length_scale": length_scale,
                }
            )
            set_pipeline_mykyta_controls(mk)
            rate = str(mk["rate"])
            pitch = str(mk["pitch"])
            ctx = {
                "tts_rate": mk["rate"],
                "tts_pitch": mk["pitch"],
                "tts_volume": mk["volume"],
                "tts_length_scale": mk["length_scale"],
                "tts_backend": "tts_uk",
            }
        files = generate_audio(
            text=text,
            voice=voice,
            segments=segments,
            rate=rate,
            pitch=pitch,
            engine_id=engine_id,
            emotion=emotion,
            task_id=str(task_id) if task_id else None,
            context=ctx,
        )
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
        "engine_id": engine_id or "edge-offline",
        "timing_mode": timing_mode,
    }

    # ── Timing Engine (если запрошен и есть тайминг-карта) ────────────
    if use_timing and timing_map:
        try:
            from engines.timing_engine import build_timed_audio
            import uuid as _uuid

            segment_paths = [str(OUTPUT_DIR / f) for f in files]
            timed_name = f"{_uuid.uuid4().hex[:8]}_timed.mp3"
            normalized_map = _normalize_timing_map(timing_map, len(files))
            # Timing Engine currently supports mode="exact" only.
            if timing_mode not in ("exact", ""):
                response["warnings"].append(
                    f"timing_mode={timing_mode!r} не поддерживается Timing Engine — "
                    "использован exact"
                )
            target_ms = None
            total_sec = _parse_total_duration(str(total_duration_str or ""))
            if total_sec > 0:
                target_ms = int(total_sec * 1000)
            elif timing_mode == "match_total" and not total_sec:
                response["warnings"].append(
                    "match_total без total_duration — длина берётся из timing_map"
                )
            timed_audio_obj, warnings = build_timed_audio(
                segment_paths=segment_paths,
                timing_map=normalized_map,
                mode="exact",
                target_duration_ms=target_ms,
            )
            timed_path = OUTPUT_DIR / timed_name
            timed_audio_obj.export(str(timed_path), format="mp3")
            if not timed_path.is_file():
                response["warnings"].append("Timing Engine: timed file missing after export")
            else:
                response["timed_file"] = timed_name
                response["timed_download"] = f"/api/download/{timed_name}"
                response["timed_stream"] = f"/api/stream/{timed_name}"
            response["warnings"].extend(warnings or [])
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
