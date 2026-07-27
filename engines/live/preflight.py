"""Live pipeline preflight — honest engine availability checks."""

from __future__ import annotations

from typing import Any

from engines.ffmpeg_paths import find_ffmpeg


def preflight_live(*, require_stt: bool = True, require_tts: bool = False) -> dict[str, Any]:
    """Return ok/issues before starting a live session."""
    issues: list[str] = []
    engines: dict[str, Any] = {}

    ff = find_ffmpeg()
    engines["ffmpeg"] = bool(ff)
    engines["ffmpeg_path"] = ff or ""
    if not ff:
        issues.append("FFmpeg not found — cannot demux/chunk audio for live STT")

    if require_stt:
        try:
            from engines import stt_engine  # noqa: F401

            engines["stt"] = True
        except Exception as e:
            engines["stt"] = False
            issues.append(f"STT engine unavailable: {e}")

    if require_tts:
        try:
            from engines import tts  # noqa: F401

            engines["tts"] = True
        except Exception as e:
            engines["tts"] = False
            issues.append(f"TTS engine unavailable: {e}")

    return {
        "ok": len(issues) == 0,
        "issues": issues,
        "engines": engines,
    }
