"""Extract audio chunks via FFmpeg for streaming STT simulation."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from engines.ffmpeg_paths import find_ffmpeg


def ensure_pcm_wav(source: str, *, work_dir: Path, sample_rate: int = 16000) -> tuple[bool, str, str]:
    """Demux entire source to mono WAV for chunk processing."""
    ff = find_ffmpeg()
    if not ff:
        return False, "", "FFmpeg not found"
    out = work_dir / "live_source.wav"
    cmd = [
        ff,
        "-y",
        "-i",
        source,
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-f",
        "wav",
        str(out),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)
        if proc.returncode != 0 or not out.is_file():
            err = (proc.stderr or "ffmpeg failed")[-500:]
            return False, "", err
        return True, str(out), ""
    except subprocess.TimeoutExpired:
        return False, "", "FFmpeg timeout"
    except Exception as e:
        return False, "", str(e)


def extract_chunk_wav(
    pcm_wav: str,
    *,
    start_sec: float,
    duration_sec: float,
    work_dir: Path,
    chunk_index: int,
) -> tuple[bool, str, str]:
    ff = find_ffmpeg()
    if not ff:
        return False, "", "FFmpeg not found"
    out = work_dir / f"chunk_{chunk_index:04d}.wav"
    cmd = [
        ff,
        "-y",
        "-ss",
        f"{start_sec:.3f}",
        "-t",
        f"{duration_sec:.3f}",
        "-i",
        pcm_wav,
        "-ac",
        "1",
        "-ar",
        "16000",
        "-f",
        "wav",
        str(out),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
        if proc.returncode != 0 or not out.is_file() or out.stat().st_size < 44:
            return False, "", (proc.stderr or "empty chunk")[-300:]
        return True, str(out), ""
    except Exception as e:
        return False, "", str(e)


def wav_duration_sec(wav_path: str) -> float:
    """Rough duration from WAV header (PCM 16-bit mono)."""
    p = Path(wav_path)
    if not p.is_file():
        return 0.0
    try:
        import wave

        with wave.open(str(p), "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:
        ff = find_ffmpeg()
        if not ff:
            return 0.0
        cmd = [ff, "-i", str(p), "-f", "null", "-"]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        import re

        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", proc.stderr or "")
        if not m:
            return 0.0
        h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
        return h * 3600 + mi * 60 + s
