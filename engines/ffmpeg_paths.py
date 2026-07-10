"""Поиск FFmpeg: bundled рядом с EXE или в PATH."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def _app_base() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def find_ffmpeg() -> str | None:
    base = _app_base()
    for candidate in (
        base / "ffmpeg" / "ffmpeg.exe",
        base / "ffmpeg.exe",
    ):
        if candidate.is_file():
            return str(candidate)
    return shutil.which("ffmpeg")


def find_ffprobe() -> str | None:
    base = _app_base()
    for candidate in (
        base / "ffmpeg" / "ffprobe.exe",
        base / "ffprobe.exe",
    ):
        if candidate.is_file():
            return str(candidate)
    return shutil.which("ffprobe")


def ensure_ffmpeg_path() -> None:
    """Добавляет bundled ffmpeg в PATH для дочерних процессов."""
    ff = find_ffmpeg()
    if not ff:
        return
    folder = str(Path(ff).parent)
    path = os.environ.get("PATH", "")
    if folder.lower() not in path.lower():
        os.environ["PATH"] = folder + os.pathsep + path
