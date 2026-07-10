"""Поддержка аудиоформатов через FFmpeg (конвертация в MP3 для пайплайна)."""

from __future__ import annotations

import logging
import shutil
import subprocess
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {
    ".mp3",
    ".wav",
    ".m4a",
    ".aac",
    ".flac",
    ".ogg",
    ".opus",
    ".wma",
    ".aiff",
    ".aif",
    ".amr",
    ".ac3",
    ".ape",
    ".webm",
}


def is_supported_audio(path: str | Path) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_EXTENSIONS


def convert_to_mp3(
    input_path: str | Path,
    output_dir: str | Path,
    sample_rate: int = 16000,
    timeout_sec: int = 300,
) -> Path:
    """Конвертирует аудио в mono MP3 16kHz для Whisper/TTS пайплайна."""
    src = Path(input_path)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if src.suffix.lower() == ".mp3":
        return src

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg не найден в PATH")

    dst = out_dir / f"{uuid.uuid4().hex[:8]}_{src.stem}.mp3"
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(src),
        "-vn",
        "-acodec",
        "mp3",
        "-ar",
        str(sample_rate),
        "-ac",
        "1",
        str(dst),
    ]
    logger.info("Converting audio %s -> %s", src.name, dst.name)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
    if proc.returncode != 0 or not dst.exists():
        tail = (proc.stderr or proc.stdout or "")[-400:]
        raise RuntimeError(f"FFmpeg audio convert failed: {tail}")
    return dst
