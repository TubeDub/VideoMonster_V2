"""Post-TTS voice processing per dub style profile (FFmpeg filters)."""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.engines.voice_style_fx")


def build_voice_fx_filter(fx: dict[str, Any] | None) -> str | None:
    if not fx:
        return None
    parts: list[str] = []
    if fx.get("highpass_hz"):
        parts.append(f"highpass=f={int(fx['highpass_hz'])}")
    if fx.get("low_shelf_cut_hz"):
        g = float(fx.get("low_shelf_gain_db", -3.0))
        parts.append(f"lowshelf=g={g}:f={int(fx['low_shelf_cut_hz'])}")
    if fx.get("nasal_peak_hz"):
        g = float(fx.get("nasal_peak_gain_db", 1.0))
        hz = int(fx["nasal_peak_hz"])
        parts.append(f"equalizer=f={hz}:width_type=h:width=500:g={g}")
    if fx.get("mid_peak_hz"):
        g = float(fx.get("mid_peak_gain_db", 2.0))
        w = float(fx.get("mid_peak_q", 1.0))
        parts.append(f"equalizer=f={int(fx['mid_peak_hz'])}:width_type=o:width={w}:g={g}")
    if fx.get("lowpass_hz"):
        parts.append(f"lowpass=f={int(fx['lowpass_hz'])}")
    if fx.get("compressor_ratio"):
        th = float(fx.get("compressor_threshold_db", -20))
        r = float(fx["compressor_ratio"])
        att = int(fx.get("compressor_attack_ms", 10))
        rel = int(fx.get("compressor_release_ms", 100))
        parts.append(
            f"acompressor=threshold={th}dB:ratio={r}:attack={att}:release={rel}"
        )
    if fx.get("volume_gain_db") is not None:
        parts.append(f"volume={float(fx['volume_gain_db'])}dB")
    return ",".join(parts) if parts else None


def apply_voice_style_fx(
    audio_path: str | Path,
    fx: dict[str, Any] | None,
    *,
    inplace: bool = True,
) -> str:
    """Apply style EQ/compression. Returns path to processed file."""
    src = Path(audio_path)
    if not src.is_file() or not fx:
        return str(src)

    filt = build_voice_fx_filter(fx)
    if not filt:
        return str(src)

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        logger.debug("voice_style_fx: ffmpeg missing, skip")
        return str(src)

    out = src
    tmp: Path | None = None
    if inplace:
        fd, tmp_name = tempfile.mkstemp(suffix=src.suffix, prefix="vfx_")
        import os

        os.close(fd)
        tmp = Path(tmp_name)
        out = tmp

    try:
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(src),
                "-af",
                filt,
                "-ar",
                "44100",
                str(out),
            ],
            check=True,
            capture_output=True,
            timeout=120,
        )
        if inplace and tmp and tmp.exists() and tmp.stat().st_size > 0:
            tmp.replace(src)
            return str(src)
        return str(out)
    except Exception as e:
        logger.warning("voice_style_fx failed for %s: %s", src.name, e)
        if tmp and tmp.exists():
            tmp.unlink(missing_ok=True)
        return str(src)


def apply_voice_fx_to_segment_files(
    segments_data: list[dict],
    output_dir: Path,
    fx: dict[str, Any] | None,
) -> int:
    if not fx:
        return 0
    count = 0
    seen: set[str] = set()
    for seg in segments_data:
        fname = seg.get("file")
        if not fname or fname in seen:
            continue
        seen.add(fname)
        path = output_dir / Path(str(fname)).name
        if path.is_file():
            apply_voice_style_fx(path, fx, inplace=True)
            count += 1
    return count
