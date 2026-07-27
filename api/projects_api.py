"""
VideoMonster V2 — Projects API
Центр проектов: список последних работ, удаление, переименование.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from flask import Blueprint, request, jsonify, abort

APP_DIR    = Path(__file__).parent.parent.resolve()
OUTPUT_DIR = APP_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

bp = Blueprint("projects_api", __name__)


@bp.get("/api/projects/list")
def api_projects_list():
    """Возвращает список последних файлов из output/ по категориям."""
    projects: list[dict] = []

    # ── Дубляжи (MP4) ─────────────────────────────────────────────
    for f in sorted(OUTPUT_DIR.glob("*_ДУБЛЯЖ_*.mp4"),
                    key=lambda x: x.stat().st_mtime, reverse=True)[:20]:
        stat = f.stat()
        projects.append({
            "type":      "dub",
            "icon":      "film",
            "label":     "Дубляж",
            "filename":  f.name,
            "title":     f.stem,
            "size_mb":   round(stat.st_size / 1024 / 1024, 1),
            "created":   round(stat.st_mtime),
            "download":  f"/api/dub/download/{f.name}",
        })

    # Любые другие MP4 (дубляж ручного режима)
    for f in sorted(OUTPUT_DIR.glob("dubbed_*.mp4"),
                    key=lambda x: x.stat().st_mtime, reverse=True)[:10]:
        stat = f.stat()
        projects.append({
            "type":      "dub",
            "icon":      "film",
            "label":     "Дубляж",
            "filename":  f.name,
            "title":     f.stem,
            "size_mb":   round(stat.st_size / 1024 / 1024, 1),
            "created":   round(stat.st_mtime),
            "download":  f"/api/dub/download/{f.name}",
        })

    # ── Озвучка (timed MP3) ────────────────────────────────────────
    for f in sorted(OUTPUT_DIR.glob("*_timed.mp3"),
                    key=lambda x: x.stat().st_mtime, reverse=True)[:10]:
        stat = f.stat()
        projects.append({
            "type":      "tts",
            "icon":      "speaker",
            "label":     "Озвучка",
            "filename":  f.name,
            "title":     f.stem,
            "size_mb":   round(stat.st_size / 1024 / 1024, 1),
            "created":   round(stat.st_mtime),
            "download":  f"/api/download/{f.name}",
            "stream":    f"/api/stream/{f.name}",
        })

    # ── Проекты Reader (VMR) ───────────────────────────────────────
    for f in sorted(OUTPUT_DIR.glob("*.vmr"),
                    key=lambda x: x.stat().st_mtime, reverse=True)[:10]:
        stat = f.stat()
        projects.append({
            "type":      "vmr",
            "icon":      "book",
            "label":     "Reader",
            "filename":  f.name,
            "title":     f.stem,
            "size_mb":   round(stat.st_size / 1024 / 1024, 3),
            "created":   round(stat.st_mtime),
            "download":  f"/api/download_vmr/{f.name}",
        })

    projects.sort(key=lambda x: x["created"], reverse=True)
    return jsonify({"projects": projects[:40]})


@bp.delete("/api/projects/delete/<filename>")
def api_projects_delete(filename):
    """Удаляет файл из output/."""
    safe = Path(filename).name
    # Разрешены только файлы из output/
    for ext in (".mp4", ".mp3", ".vmr", ".txt"):
        if safe.endswith(ext):
            path = OUTPUT_DIR / safe
            if path.exists():
                path.unlink()
                return jsonify({"ok": True})
            return jsonify({"error": "Файл не найден"}), 404
    return jsonify({"error": "Недопустимый тип файла"}), 400


@bp.post("/api/projects/open_folder")
def api_projects_open_folder():
    """Открывает папку output/ в проводнике."""
    import subprocess, sys as _sys
    folder = str(OUTPUT_DIR)
    try:
        if _sys.platform == "win32":
            subprocess.Popen(["explorer", folder])
        elif _sys.platform == "darwin":
            subprocess.Popen(["open", folder])
        else:
            subprocess.Popen(["xdg-open", folder])
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True, "folder": folder})
