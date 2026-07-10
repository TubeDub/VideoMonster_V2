"""
OCR — изолирован от пайплайна дубляжа речи.
По умолчанию выключен; текст с экрана НЕ попадает в Whisper/перевод/TTS дубляжа.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.engines.ocr")

DEFAULT_SAMPLE_INTERVAL_SEC = 2.0
MAX_SAMPLES = 120


def ocr_available() -> tuple[bool, str]:
    """Проверяет, доступен ли OCR (ffmpeg + tesseract)."""
    if not shutil.which("ffmpeg"):
        return False, "ffmpeg not found"
    try:
        import pytesseract  # noqa: F401

        if not shutil.which("tesseract"):
            return False, "tesseract binary not in PATH"
        return True, "pytesseract"
    except ImportError:
        return False, "pytesseract not installed (pip install pytesseract + Tesseract OCR)"


def _sample_timestamps(duration_sec: float, interval: float) -> list[float]:
    if duration_sec <= 0:
        return [0.0]
    times: list[float] = []
    t = 0.0
    while t < duration_sec and len(times) < MAX_SAMPLES:
        times.append(t)
        t += interval
    return times


def _video_duration_sec(video_path: str) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "quiet",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                video_path,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def _ocr_frame(image_path: Path, lang: str = "eng") -> str:
    import pytesseract

    text = pytesseract.image_to_string(str(image_path), lang=lang)
    return " ".join(text.split()).strip()


def extract_video_text(
    video_path: str,
    *,
    enabled: bool = False,
    sample_interval_sec: float = DEFAULT_SAMPLE_INTERVAL_SEC,
    lang: str = "eng",
) -> dict[str, Any]:
    """
    Извлекает текст с кадров видео (OCR).
    enabled=False → пустой результат, без побочных эффектов.
    """
    result: dict[str, Any] = {
        "enabled": enabled,
        "video_path": video_path,
        "segments": [],
        "full_text": "",
        "engine": None,
        "error": None,
    }

    if not enabled:
        result["note"] = "OCR disabled — speech dubbing uses Whisper only"
        return result

    ok, engine_note = ocr_available()
    if not ok:
        result["error"] = engine_note
        return result

    result["engine"] = engine_note
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not Path(video_path).exists():
        result["error"] = "video or ffmpeg missing"
        return result

    duration = _video_duration_sec(video_path)
    timestamps = _sample_timestamps(duration, sample_interval_sec)
    work = Path(tempfile.mkdtemp(prefix="tubedub_ocr_"))
    segments: list[dict[str, Any]] = []
    seen: set[str] = set()

    try:
        for i, ts in enumerate(timestamps):
            frame = work / f"frame_{i:04d}.png"
            subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-ss",
                    f"{ts:.3f}",
                    "-i",
                    video_path,
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=1280:-1",
                    str(frame),
                ],
                capture_output=True,
                timeout=30,
            )
            if not frame.exists():
                continue
            raw = _ocr_frame(frame, lang=lang)
            if not raw or raw.lower() in seen:
                continue
            seen.add(raw.lower())
            start_ms = int(ts * 1000)
            end_ms = int(min(ts + sample_interval_sec, duration) * 1000)
            segments.append({"start": start_ms, "end": end_ms, "text": raw})

        result["segments"] = segments
        result["full_text"] = "\n".join(s["text"] for s in segments)
        logger.info(
            "OCR extracted %d unique lines from %s (samples=%d)",
            len(segments),
            video_path,
            len(timestamps),
        )
    except Exception as e:
        logger.exception("OCR extract failed: %s", e)
        result["error"] = str(e)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    return result


def align_ocr_to_speech_slots(
    ocr_segments: list[dict[str, Any]],
    timing_map: list[Any],
) -> list[str]:
    """OCR-текст по слотам речи (только для диагностики, не для перевода)."""
    if not timing_map:
        return []

    out: list[str] = []
    for slot in timing_map:
        if isinstance(slot, dict):
            s0, s1 = int(slot.get("start", 0)), int(slot.get("end", 0))
        elif isinstance(slot, (list, tuple)) and len(slot) >= 2:
            s0, s1 = int(slot[0]), int(slot[1])
        else:
            out.append("")
            continue

        hits: list[str] = []
        for ocr in ocr_segments:
            o0, o1 = int(ocr.get("start", 0)), int(ocr.get("end", 0))
            if o0 < s1 and o1 > s0:
                hits.append(str(ocr.get("text") or ""))
        out.append(" | ".join(h for h in hits if h))
    return out


def export_ocr_text(ocr_result: dict[str, Any], fmt: str = "txt") -> str:
    """Экспорт OCR-текста (txt или srt-подобный)."""
    segments = ocr_result.get("segments") or []
    if fmt == "json":
        return json.dumps(ocr_result, ensure_ascii=False, indent=2)

    lines: list[str] = []
    for i, seg in enumerate(segments, 1):
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        if fmt == "srt":
            start = int(seg.get("start", 0))
            end = int(seg.get("end", 0))
            lines.append(
                f"{i}\n{_ms_srt(start)} --> {_ms_srt(end)}\n{text}\n"
            )
        else:
            lines.append(text)
    return "\n".join(lines) if fmt != "srt" else "\n".join(lines)


def _ms_srt(ms: int) -> str:
    ms = max(0, int(ms))
    h = ms // 3600000
    ms %= 3600000
    m = ms // 60000
    ms %= 60000
    s = ms // 1000
    milli = ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"
