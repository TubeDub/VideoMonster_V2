"""P615 Voice Quality Validator."""

from __future__ import annotations

import wave
from pathlib import Path
from typing import Any


def validate_synthesis_audio(
    path: str | Path,
    *,
    expected_sample_rate: int | None = None,
    min_duration_ms: float = 20.0,
    max_silence_ratio: float = 0.98,
) -> dict[str, Any]:
    """
    Check: truncation, silence, clipping, corrupt WAV, sample rate, sync hints.
    """
    p = Path(path)
    issues: list[str] = []
    metrics: dict[str, Any] = {"path": str(p)}

    if not p.is_file():
        return {"ok": False, "issues": ["missing_file"], "metrics": metrics}

    size = p.stat().st_size
    metrics["bytes"] = size
    if size < 44:
        return {"ok": False, "issues": ["corrupt_wav"], "metrics": metrics}

    # Try WAV; MP3/other formats get soft pass on size only
    if p.suffix.lower() not in {".wav", ".wave"}:
        if size < 256:
            issues.append("truncated")
        return {"ok": len(issues) == 0, "issues": issues, "metrics": metrics}

    try:
        with wave.open(str(p), "rb") as wf:
            nchan = wf.getnchannels()
            sw = wf.getsampwidth()
            sr = wf.getframerate()
            nframes = wf.getnframes()
            raw = wf.readframes(nframes)
        metrics.update(
            {
                "channels": nchan,
                "sampwidth": sw,
                "sample_rate": sr,
                "frames": nframes,
                "duration_ms": round(1000.0 * nframes / max(1, sr), 1),
            }
        )
        if expected_sample_rate and sr != expected_sample_rate:
            issues.append("wrong_sample_rate")
        if metrics["duration_ms"] < min_duration_ms:
            issues.append("truncated")
        # Silence / clipping heuristics on PCM16
        if sw == 2 and raw:
            import array

            samples = array.array("h")
            samples.frombytes(raw[: len(raw) - (len(raw) % 2)])
            if samples:
                peak = max(abs(s) for s in samples)
                zeros = sum(1 for s in samples if abs(s) < 8)
                silence_ratio = zeros / len(samples)
                metrics["peak"] = peak
                metrics["silence_ratio"] = round(silence_ratio, 3)
                if silence_ratio >= max_silence_ratio:
                    issues.append("silence")
                if peak >= 32767:
                    issues.append("clipping")
                elif peak == 0:
                    issues.append("silence")
    except wave.Error:
        issues.append("corrupt_wav")
    except Exception as exc:
        issues.append(f"read_error:{exc}")

    # Deduplicate issue labels
    issues = list(dict.fromkeys(issues))
    return {"ok": len(issues) == 0, "issues": issues, "metrics": metrics}
