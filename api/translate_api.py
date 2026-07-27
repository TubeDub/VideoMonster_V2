"""Translation API — standalone UI uses the same pipeline as full dub."""

from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request

from data.languages import LANG_CODE_TO_NAME
from engines.cleaner import clean_transcript
from engines.translation_compat import detect_language

APP_DIR = Path(__file__).parent.parent.resolve()
UPLOADS_DIR = APP_DIR / "uploads" / "translate"
OUTPUT_DIR = APP_DIR / "output"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

bp = Blueprint("translate_api", __name__)


def _license_gate():
    from engines.license_manager import check_translate_allowed

    return check_translate_allowed()


def _dev_inspector_allowed() -> bool:
    from engines.translation_inspector import inspector_enabled

    return inspector_enabled()


def _profile_gate(source_lang: str, target_lang: str, *, feature: str = "translate"):
    from engines.model_manager import is_profile_ready
    from engines.model_manager.estimate import estimate_profile_download_mb

    src = (source_lang or "en").split("-")[0].lower()
    tgt = (target_lang or "ru").split("-")[0].lower()
    if is_profile_ready(APP_DIR, src, tgt, feature=feature):
        return None
    est = estimate_profile_download_mb(APP_DIR, src, tgt, feature=feature)
    return (
        jsonify(
            {
                "error": "Для данного языка необходимо загрузить языковой пакет.",
                "error_code": "prepare_required",
                "estimated_download_mb": est,
                "source_lang": src,
                "target_lang": tgt,
                "feature": feature,
            }
        ),
        409,
    )


def _extract_audio_from_video(video_path: str, out_mp3: str) -> None:
    import ffmpeg

    from engines.ffmpeg_paths import find_ffmpeg

    ffmpeg_bin = find_ffmpeg()
    if ffmpeg_bin:
        res = subprocess.run(
            [
                ffmpeg_bin,
                "-y",
                "-i",
                video_path,
                "-vn",
                "-acodec",
                "mp3",
                "-ar",
                "16000",
                "-ac",
                "1",
                out_mp3,
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if res.returncode == 0 and Path(out_mp3).is_file():
            return
    try:
        ffmpeg.input(video_path).output(
            out_mp3, acodec="mp3", ar="16000", ac=1
        ).run(quiet=True, overwrite_output=True)
    except ffmpeg.Error as e:
        msg = e.stderr.decode() if e.stderr else str(e)
        raise RuntimeError(f"FFmpeg: {msg}") from e


def _run_whisper(audio_path: str, *, language: str | None = None) -> dict:
    from engines.audio_formats import convert_to_mp3, is_supported_audio
    from engines.stt_engine import transcribe

    src = Path(audio_path)
    if not src.is_file():
        raise FileNotFoundError("Аудиофайл не найден")
    if not is_supported_audio(src) and src.suffix.lower() not in (
        ".mp4",
        ".mkv",
        ".avi",
        ".mov",
        ".webm",
        ".mpeg",
        ".mpg",
        ".flv",
        ".ts",
        ".m4v",
    ):
        raise ValueError(f"Неподдерживаемый формат: {src.suffix}")

    mp3 = convert_to_mp3(src, OUTPUT_DIR)
    text, srt_content, timing_map, detected = transcribe(
        str(mp3),
        language=language or None,
        model_size="tiny",
    )
    if not str(text or "").strip():
        raise RuntimeError("Распознавание вернуло пустой текст")
    return {
        "text": text,
        "srt": srt_content,
        "timing_map": timing_map,
        "detected": detected,
        "detected_name": LANG_CODE_TO_NAME.get(detected, detected),
    }


def _normalize_translate_mode(raw) -> str:
    mode = str(raw or "auto").strip().lower()
    return mode if mode in ("online", "offline", "auto") else "auto"


def _translate_with_mode(text: str, source: str, target: str, mode: str) -> tuple[str, str]:
    """Settings modes: online=Google, offline=Argos, auto=online→offline."""
    src = (source or "en").split("-")[0]
    tgt = (target or "ru").split("-")[0]
    if src == tgt:
        return text, "identity"

    def _online() -> str:
        from engines.mt.deep_engine import DeepTranslatorEngine

        r = DeepTranslatorEngine().translate(text, src, tgt)
        if not r.text:
            raise RuntimeError(r.error or "Онлайн-перевод (Google) недоступен.")
        return r.text

    def _offline() -> str:
        from engines.mt.argos_engine import translate_argos

        out = translate_argos(text, src, tgt)
        if not out:
            raise RuntimeError(
                "Офлайн-перевод (Argos) недоступен для этой пары языков. "
                "Подготовьте языковой пакет или выберите онлайн-режим."
            )
        return out

    if mode == "online":
        return _online(), "deep"
    if mode == "offline":
        return _offline(), "argos"

    # auto: online → offline (matches Settings label)
    try:
        return _online(), "deep"
    except Exception:
        return _offline(), "argos"


@bp.post("/api/translate")
def api_translate():
    """Legacy block translate — kept for studio/compat; prefer /api/translate/pipeline."""
    ok, lic_msg = _license_gate()
    if not ok:
        return jsonify({"error": lic_msg}), 403

    data = request.get_json(silent=True) or {}
    mode = _normalize_translate_mode(data.get("mode"))
    # auto + pipeline (default): Universal Translation Pipeline (same as dub).
    # online/offline: honor Settings (Google / Argos) — do not ignore mode.
    use_pipeline = bool(data.get("pipeline", True)) and mode == "auto"
    if use_pipeline:
        return api_translate_pipeline()

    text = data.get("text", "").strip()
    target = data.get("target", "ru")
    do_clean = data.get("clean", False)
    source = data.get("source") or None

    if not text:
        return jsonify({"error": "Текст не указан"}), 400

    timing_map = []
    review_items = []
    cleaned_text = None

    if do_clean:
        text, timing_map, review_items = clean_transcript(text)
        cleaned_text = text
        if not text:
            return jsonify({"error": "Текст пуст после очистки"}), 400

    detected = source or detect_language(text)

    try:
        if mode in ("online", "offline"):
            translated, engine = _translate_with_mode(text, detected, target, mode)
        else:
            # auto with pipeline=False: Settings auto = online → offline
            translated, engine = _translate_with_mode(text, detected, target, "auto")
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify(
        {
            "translated": translated,
            "cleaned": cleaned_text,
            "detected": detected,
            "detected_name": LANG_CODE_TO_NAME.get(detected, detected),
            "timing_map": timing_map,
            "review_count": len(review_items),
            "mode": mode,
            "engine": engine,
        }
    )


@bp.post("/api/translate/pipeline")
def api_translate_pipeline():
    """Universal Translation Pipeline — same engine as full dub."""
    ok, lic_msg = _license_gate()
    if not ok:
        return jsonify({"error": lic_msg}), 403

    data = request.get_json(silent=True) or {}
    text = str(data.get("text") or "").strip()
    target = str(data.get("target") or "ru").strip()
    source = str(data.get("source") or "").strip()
    do_clean = bool(data.get("clean", True))
    timing_map = list(data.get("timing_map") or [])

    if not text:
        return jsonify({"error": "Текст не указан"}), 400

    from engines.translate_lab import prepare_source_segments, run_pipeline_translate

    segments, tm, cleaned = prepare_source_segments(
        text, timing_map=timing_map, clean=do_clean
    )
    if not segments:
        return jsonify({"error": "Нет сегментов для перевода"}), 400

    detected = source or detect_language("\n".join(segments[:5]) if segments else text)
    src_lang = detected if detected and detected != "unknown" else "en"

    blocked = _profile_gate(src_lang, target, feature="translate")
    if blocked:
        return blocked

    try:
        result = run_pipeline_translate(
            segments,
            tm,
            src_lang,
            target,
            app_dir=APP_DIR,
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify(
        {
            "ok": True,
            "translated": result["translated"],
            "segments": result["segments"],
            "session_id": result["session_id"],
            "log_path": result.get("log_path", ""),
            "detected": src_lang,
            "detected_name": LANG_CODE_TO_NAME.get(src_lang, src_lang),
            "cleaned": cleaned,
            "timing_map": tm,
            "segment_count": len(segments),
            "engines": result.get("meta", {}).get("engines") or [],
            "inspector_available": _dev_inspector_allowed(),
        }
    )


@bp.get("/api/translate/logs/last")
def api_translate_logs_last():
    from engines.translate_session_log import get_latest_log

    result = get_latest_log(APP_DIR)
    if not result.get("ok"):
        return jsonify(result), 404
    data = result.pop("data", None) or {}
    if not data and result.get("path"):
        try:
            data = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
        except Exception:
            data = {}
    result["segment_count"] = (
        data.get("segment_count")
        or len(data.get("source_segments") or [])
        or len(data.get("translated_segments") or [])
        or 0
    )
    result["source_lang"] = data.get("source_lang", "")
    result["target_lang"] = data.get("target_lang", "")
    return jsonify(result)


@bp.post("/api/translate/logs/open-last")
def api_translate_logs_open_last():
    from engines.translate_session_log import get_latest_log, open_path_in_shell

    result = get_latest_log(APP_DIR)
    if not result.get("ok"):
        return jsonify(result), 404
    r = open_path_in_shell(Path(result["path"]))
    return jsonify({**result, **r})


@bp.post("/api/translate/logs/open-folder")
def api_translate_logs_open_folder():
    from engines.translate_session_log import logs_dir, open_path_in_shell

    folder = logs_dir(APP_DIR).parent
    r = open_path_in_shell(folder)
    return jsonify({"ok": r.get("ok", False), "path": str(folder), **r})


@bp.get("/api/translate/logs/report")
def api_translate_logs_report():
    from engines.translate_session_log import build_text_report, get_latest_log

    sid = request.args.get("session_id", "").strip()
    if not sid:
        latest = get_latest_log(APP_DIR)
        sid = latest.get("session_id", "") if latest.get("ok") else ""
    if not sid:
        return jsonify({"error": "no_session"}), 404
    text = build_text_report(APP_DIR, sid)
    return jsonify({"ok": True, "session_id": sid, "text": text})


@bp.post("/api/translate/logs/clear")
def api_translate_logs_clear():
    data = request.get_json(silent=True) or {}
    if not data.get("confirmed"):
        return jsonify({"error": "confirmation_required", "needs_confirm": True}), 400
    from engines.translate_session_log import clear_all_logs

    return jsonify(clear_all_logs(APP_DIR))


@bp.post("/api/translate/stt/audio")
def api_translate_stt_audio():
    """Whisper STT from audio file — text to source box only (no auto-translate)."""
    if "file" not in request.files:
        return jsonify({"error": "Файл не передан"}), 400
    file = request.files["file"]
    original = Path(file.filename or "").name
    if not original:
        return jsonify({"error": "Имя файла пустое"}), 400

    ext = Path(original).suffix.lower() or ".audio"
    fid = uuid.uuid4().hex[:10]
    path = UPLOADS_DIR / f"audio_{fid}{ext}"
    file.save(str(path))

    language = request.form.get("language") or None
    if language in ("auto", ""):
        language = None

    blocked = _profile_gate(language or "en", "ru", feature="stt")
    if blocked:
        body, code = blocked
        path.unlink(missing_ok=True)
        return body, code

    try:
        out = _run_whisper(str(path), language=language)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        path.unlink(missing_ok=True)

    return jsonify({"ok": True, "original_filename": original, **out})


@bp.post("/api/translate/stt/video")
def api_translate_stt_video():
    """Extract audio (FFmpeg) + Whisper STT — text to source box only."""
    if "file" not in request.files:
        return jsonify({"error": "Файл не передан"}), 400
    file = request.files["file"]
    original = Path(file.filename or "").name
    if not original:
        return jsonify({"error": "Имя файла пустое"}), 400

    ext = Path(original).suffix.lower() or ".mp4"
    fid = uuid.uuid4().hex[:10]
    video_path = UPLOADS_DIR / f"video_{fid}{ext}"
    audio_path = UPLOADS_DIR / f"video_{fid}.mp3"
    file.save(str(video_path))

    language = request.form.get("language") or None
    if language in ("auto", ""):
        language = None

    blocked = _profile_gate(language or "en", "ru", feature="stt")
    if blocked:
        body, code = blocked
        video_path.unlink(missing_ok=True)
        return body, code

    try:
        from engines.ffmpeg_paths import find_ffmpeg

        if not find_ffmpeg():
            return jsonify({"error": "FFmpeg не найден в системе"}), 500
        _extract_audio_from_video(str(video_path), str(audio_path))
        out = _run_whisper(str(audio_path), language=language)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        audio_path.unlink(missing_ok=True)
        video_path.unlink(missing_ok=True)

    return jsonify({"ok": True, "original_filename": original, **out})


@bp.get("/api/translate/inspector/<session_id>")
def api_translate_inspector(session_id):
    if not _dev_inspector_allowed():
        return jsonify({"error": "Developer mode required (VM_DEV_MODE=1)"}), 403

    from engines.translate_lab import build_inspector_report

    report = build_inspector_report(session_id)
    if report.get("error") == "session_not_found":
        return jsonify({"error": "Сессия не найдена"}), 404
    return jsonify({"ok": True, "session_id": session_id, "inspector": report})


@bp.get("/api/translate/inspector/<session_id>/export")
def api_translate_inspector_export(session_id):
    if not _dev_inspector_allowed():
        return jsonify({"error": "Developer mode required (VM_DEV_MODE=1)"}), 403

    from engines.translation_inspector import export_inspector_text
    from engines.translate_lab import build_inspector_report

    report = build_inspector_report(session_id)
    if report.get("error") == "session_not_found":
        return jsonify({"error": "Сессия не найдена"}), 404
    body = export_inspector_text(report)
    return jsonify(
        {
            "ok": True,
            "text": body,
            "filename": f"translation_inspector_{session_id[:8]}.txt",
        }
    )


@bp.post("/api/translate/reader")
def api_translate_open_reader():
    """Save VMR from translate page and return Reader URL."""
    data = request.get_json(silent=True) or {}
    source = str(data.get("source_text") or "").strip()
    translated = str(data.get("translated_text") or "").strip()
    timing_map = data.get("timing_map") or []
    src_lang = data.get("source_lang") or ""
    tgt_lang = data.get("target_lang") or "ru"

    if not translated:
        return jsonify({"error": "Нет перевода для Reader"}), 400

    filename = f"translate_{uuid.uuid4().hex[:10]}.vmr"
    path = OUTPUT_DIR / filename
    doc = {
        "format": "VMR",
        "version": "2.0",
        "title": "TubeDub Translate",
        "source_text": source,
        "translated_text": translated,
        "processed_text": translated,
        "timing_map": timing_map,
        "source_lang": src_lang,
        "target_lang": tgt_lang,
        "reading_position": 0,
        "bookmarks": [],
        "reader_settings": {"font_size": 17, "show_source": True},
        "origin": "translate",
    }
    from engines.storage.atomic import atomic_write_json

    atomic_write_json(path, doc)

    return jsonify(
        {
            "ok": True,
            "filename": filename,
            "reader_url": f"/reader?vmr={filename}",
        }
    )


@bp.get("/api/reader/document/<filename>")
def api_reader_document(filename):
    """Load VMR JSON from output/ for Reader auto-open."""
    safe = Path(filename).name
    if not safe.endswith(".vmr") or safe != filename:
        return jsonify({"error": "Неверный формат"}), 400
    path = OUTPUT_DIR / safe
    if not path.is_file():
        return jsonify({"error": "Файл не найден"}), 404
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(doc)


@bp.post("/api/detect")
def api_detect():
    data = request.get_json(silent=True) or {}
    text = data.get("text", "").strip()

    if not text:
        return jsonify({"lang": "unknown", "name": "Неизвестно"})

    lang = detect_language(text)
    name = LANG_CODE_TO_NAME.get(lang, lang)

    return jsonify({"lang": lang, "name": name})


@bp.post("/api/clean")
def api_clean():
    data = request.get_json(silent=True) or {}
    text = data.get("text", "")

    cleaned, timing_map, review_items = clean_transcript(text)

    return jsonify(
        {
            "cleaned": cleaned,
            "timing_map": timing_map,
            "review_count": len(review_items),
            "lines": len([ln for ln in cleaned.splitlines() if ln.strip()]),
        }
    )
