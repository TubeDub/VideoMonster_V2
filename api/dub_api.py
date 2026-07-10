"""
VideoMonster V2 — Dub API
Загрузка видео, запуск дубляжа, отслеживание прогресса, скачивание результата.
"""

from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path

from flask import Blueprint, request, jsonify, send_file, abort

from engines.dub_engine import DubEngine

APP_DIR     = Path(__file__).parent.parent.resolve()
OUTPUT_DIR  = APP_DIR / "output"
UPLOADS_DIR = APP_DIR / "uploads"
OUTPUT_DIR.mkdir(exist_ok=True)
UPLOADS_DIR.mkdir(exist_ok=True)

ALLOWED_VIDEO = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".wmv", ".flv", ".m4v"}
ALLOWED_AUDIO = {
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus",
    ".wma", ".aiff", ".aif", ".amr", ".ac3", ".ape", ".webm",
}
ALLOWED_SUBS = {".srt", ".vtt", ".ass", ".ssa", ".txt"}

# task_id -> {status, progress, output_file, errors, created_at}
TASKS: dict[str, dict] = {}

bp = Blueprint("dub_api", __name__)


# ─────────────────────────────────────────────
#  Загрузка файлов
# ─────────────────────────────────────────────

@bp.post("/api/dub/upload_video")
def api_upload_video():
    """Загружает видеофайл. Возвращает {video_id, filename, size_mb}."""
    if "file" not in request.files:
        return jsonify({"error": "Файл не передан"}), 400
    file = request.files["file"]
    ext = Path(file.filename).suffix.lower()
    if "_OUTPUT_" in (file.filename or "").upper():
        return jsonify({
            "error": "Файл с _OUTPUT_ в имени — это уже готовый дубляж. Выберите оригинальное видео без _OUTPUT_."
        }), 400
    if ext not in ALLOWED_VIDEO:
        return jsonify({"error": f"Неподдерживаемый формат ({ext}). Разрешены: MP4, MKV, MOV, AVI"}), 400

    video_id = uuid.uuid4().hex[:10]
    filename = f"video_{video_id}{ext}"
    path = UPLOADS_DIR / filename
    file.save(str(path))
    size_mb = round(path.stat().st_size / 1024 / 1024, 1)

    return jsonify({"video_id": video_id, "filename": filename, "size_mb": size_mb})


@bp.post("/api/dub/upload_audio")
def api_upload_audio():
    """Загружает аудиофайл."""
    if "file" not in request.files:
        return jsonify({"error": "Файл не передан"}), 400
    file = request.files["file"]
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_AUDIO:
        return jsonify({
            "error": f"Неподдерживаемый формат ({ext}). "
            f"Разрешены: MP3, WAV, M4A, AAC, FLAC, OGG, OPUS, WMA, AIFF, AMR, AC3, APE, WEBM"
        }), 400

    audio_id = uuid.uuid4().hex[:10]
    filename = f"audio_{audio_id}{ext}"
    path = UPLOADS_DIR / filename
    file.save(str(path))

    return jsonify({"audio_id": audio_id, "filename": filename})


# ─────────────────────────────────────────────
#  Предпросмотр загруженного видео
# ─────────────────────────────────────────────

@bp.get("/api/dub/preview_video/<filename>")
def api_preview_video(filename):
    """Отдаёт загруженное видео для встроенного player-а (предпросмотр)."""
    safe = Path(filename).name
    path = UPLOADS_DIR / safe
    if not path.exists():
        abort(404)
    ext = safe.rsplit(".", 1)[-1].lower()
    mime_map = {
        "mp4":  "video/mp4",
        "mkv":  "video/x-matroska",
        "mov":  "video/quicktime",
        "avi":  "video/x-msvideo",
        "webm": "video/webm",
    }
    mime = mime_map.get(ext, "video/mp4")
    return send_file(str(path), mimetype=mime)


@bp.get("/api/dub/preview_output/<filename>")
def api_preview_output(filename):
    """Inline preview of finished dub MP4 from output/."""
    safe = Path(filename).name
    if not safe.endswith(".mp4"):
        abort(404)
    path = OUTPUT_DIR / safe
    if not path.exists():
        abort(404)
    return send_file(str(path), mimetype="video/mp4", conditional=True)


# ─────────────────────────────────────────────
#  Запуск дубляжа
# ─────────────────────────────────────────────

@bp.post("/api/dub/start")
def api_dub_start():
    """
    Запускает дубляж в фоновом потоке.

    Параметры JSON:
      video_filename  — имя файла из uploads/
      audio_filename  — имя файла из uploads/ или output/
      mix_mode        — full_dub | atmosphere | language_learning | custom
      mode            — legacy 'replace' | 'mix'
      original_volume, dub_volume, background_volume — для custom
      keep_original_track — сохранить извлечённую оригинальную дорожку в projects/
    """
    data = request.get_json(silent=True) or {}

    video_filename = data.get("video_filename", "")
    audio_filename = data.get("audio_filename", "")
    mix_mode = (data.get("mix_mode") or data.get("mode") or "full_dub").strip().lower()
    if mix_mode == "replace":
        mix_mode = "full_dub"
    elif mix_mode == "mix":
        mix_mode = "language_learning"
    legacy_mode = data.get("mode", "replace")
    mix_volume = float(data.get("mix_volume", 0.3))
    keep_original_track = bool(data.get("keep_original_track", False))

    def _fvol(k):
        if k not in data:
            return None
        try:
            return float(data[k])
        except (TypeError, ValueError):
            return None

    if not video_filename or not audio_filename:
        return jsonify({"error": "Необходимо указать video_filename и audio_filename"}), 400

    if "_OUTPUT_" in Path(video_filename).name.upper():
        return jsonify({
            "error": "Файл с _OUTPUT_ в имени — это уже готовый дубляж. Выберите оригинальное видео без _OUTPUT_."
        }), 400

    video_path = UPLOADS_DIR / Path(video_filename).name
    if not video_path.exists():
        return jsonify({"error": f"Видеофайл не найден: {video_filename}"}), 404

    audio_path = OUTPUT_DIR / Path(audio_filename).name
    if not audio_path.exists():
        audio_path = UPLOADS_DIR / Path(audio_filename).name
    if not audio_path.exists():
        return jsonify({"error": f"Аудиофайл не найден: {audio_filename}"}), 404

    engine = DubEngine(video_path=str(video_path), audio_path=str(audio_path))
    ok, errors = engine.validate()
    if not ok:
        return jsonify({"error": " | ".join(errors)}), 400

    task_id     = uuid.uuid4().hex[:10]
    output_name = f"dubbed_{task_id}.mp4"
    output_path = str(OUTPUT_DIR / output_name)

    TASKS[task_id] = {
        "status": "running",
        "progress": 0.0,
        "output_file": None,
        "errors": [],
        "created_at": time.time(),
    }

    thread = threading.Thread(
        target=_run_dub_task,
        args=(
            task_id,
            engine,
            output_path,
            mix_mode,
            legacy_mode,
            mix_volume,
            _fvol("original_volume"),
            _fvol("dub_volume"),
            _fvol("background_volume"),
            output_name,
        ),
        daemon=True,
    )
    thread.start()

    return jsonify({"task_id": task_id, "output_name": output_name})


def _run_dub_task(
    task_id,
    engine,
    output_path,
    mix_mode,
    legacy_mode,
    mix_volume,
    original_volume,
    dub_volume,
    background_volume,
    output_name,
):
    def on_progress(pct):
        if task_id in TASKS:
            TASKS[task_id]["progress"] = round(pct, 1)
    try:
        ok, out_path, warnings = engine.run(
            output_path=output_path,
            mode=legacy_mode,
            mix_mode=mix_mode,
            mix_volume=mix_volume,
            original_volume=original_volume,
            dub_volume=dub_volume,
            background_volume=background_volume,
            progress_callback=on_progress,
        )
        if ok:
            TASKS[task_id].update({"status": "done", "progress": 100.0, "output_file": output_name, "errors": warnings})
        else:
            TASKS[task_id].update({"status": "error", "errors": warnings})
    except Exception as e:
        if task_id in TASKS:
            TASKS[task_id].update({"status": "error", "errors": [f"Неожиданная ошибка: {e}"]})


# ─────────────────────────────────────────────
#  Статус / прогресс
# ─────────────────────────────────────────────

@bp.get("/api/dub/status/<task_id>")
def api_dub_status(task_id):
    task = TASKS.get(task_id)
    if task is None:
        return jsonify({"error": "Задача не найдена"}), 404
    result = {"status": task["status"], "progress": task["progress"], "errors": task["errors"]}
    if task["output_file"]:
        result["download"]    = f"/api/dub/download/{task['output_file']}"
        result["output_file"] = task["output_file"]
    return jsonify(result)


# ─────────────────────────────────────────────
#  Скачивание результата
# ─────────────────────────────────────────────

@bp.get("/api/dub/download/<filename>")
def api_dub_download(filename):
    safe = Path(filename).name
    if safe.endswith(".mp4"):
        path = OUTPUT_DIR / safe
        if not path.exists():
            abort(404)
        return send_file(str(path), as_attachment=True, download_name=safe, mimetype="video/mp4")
    if safe.endswith(".srt"):
        path = OUTPUT_DIR / safe
        if not path.exists():
            abort(404)
        return send_file(
            str(path),
            as_attachment=True,
            download_name=safe,
            mimetype="text/plain; charset=utf-8",
        )
    abort(404)


# ─────────────────────────────────────────────
#  Проверка FFmpeg
# ─────────────────────────────────────────────

@bp.get("/api/dub/check")
def api_dub_check():
    import shutil
    ffmpeg  = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    return jsonify({"ffmpeg": bool(ffmpeg), "ffprobe": bool(ffprobe), "ffmpeg_path": ffmpeg or ""})


# ─────────────────────────────────────────────
#  Сохранение в папку (desktop)
# ─────────────────────────────────────────────

@bp.post("/api/dub/save_to_folder")
def api_save_to_folder():
    import re as _re
    import shutil as _shutil
    data     = request.get_json(silent=True) or {}
    filename = data.get("filename", "")
    if not filename:
        return jsonify({"error": "filename не указан"}), 400
    safe = Path(filename).name
    if not safe.endswith(".mp4"):
        return jsonify({"error": "Только MP4"}), 400
    src = OUTPUT_DIR / safe
    if not src.exists():
        return jsonify({"error": "Файл не найден"}), 404

    # Красивое имя от клиента (например MyVideo_RU.mp4)
    suggested = str(data.get("suggested_name") or "").strip()
    if suggested and suggested.endswith(".mp4"):
        # Оставляем только безопасные символы
        save_name = _re.sub(r'[\\/:*?"<>|]', "_", suggested)
    else:
        save_name = safe

    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
        folder = filedialog.askdirectory(title="Выберите папку для сохранения видео", parent=root)
        root.destroy()
    except Exception as e:
        return jsonify({"error": f"Диалог недоступен: {e}"}), 500
    if not folder:
        return jsonify({"cancelled": True})
    dst = Path(folder) / save_name
    try:
        _shutil.copy2(str(src), str(dst))
    except Exception as e:
        return jsonify({"error": f"Ошибка копирования: {e}"}), 500
    return jsonify({"success": True, "folder": folder, "path": str(dst), "filename": save_name})


@bp.post("/api/dub/open_folder")
def api_open_folder():
    import subprocess, sys as _sys
    data   = request.get_json(silent=True) or {}
    folder = data.get("folder", "")
    if not folder or not Path(folder).exists():
        return jsonify({"error": "Папка не найдена"}), 404
    try:
        if _sys.platform == "win32":   subprocess.Popen(["explorer", folder])
        elif _sys.platform == "darwin": subprocess.Popen(["open", folder])
        else:                           subprocess.Popen(["xdg-open", folder])
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True})


# ─────────────────────────────────────────────
#  Очистка
# ─────────────────────────────────────────────

@bp.post("/api/dub/cleanup")
def api_dub_cleanup():
    now = time.time(); removed = 0
    for tid in list(TASKS.keys()):
        task = TASKS[tid]
        if now - task.get("created_at", now) > 7200:
            if task.get("output_file"):
                (OUTPUT_DIR / task["output_file"]).unlink(missing_ok=True)
            del TASKS[tid]; removed += 1
    for f in UPLOADS_DIR.glob("video_*"):
        try:
            if now - f.stat().st_mtime > 7200:
                f.unlink(); removed += 1
        except OSError as e:
            import logging
            logging.getLogger(__name__).warning("cleanup upload %s: %s", f, e)
    return jsonify({"removed": removed})
