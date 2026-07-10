"""Built-in DSP plugins."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from engines.dub_studio.fx.base import EffectContext, EffectModule, ProcessResult


def _ffmpeg() -> str:
    p = shutil.which("ffmpeg")
    if not p:
        raise RuntimeError("ffmpeg not found")
    return p


def _dur_ms(path: Path) -> int:
    try:
        from pydub import AudioSegment

        return len(AudioSegment.from_file(str(path)))
    except Exception:
        return 0


class _Passthrough(EffectModule):
    plugin_id = "passthrough"
    label = "Passthrough"
    category = "utility"

    def process(self, input_path, output_path, *, params=None, ctx=None):
        shutil.copy2(input_path, output_path)
        return ProcessResult(str(output_path), duration_ms=_dur_ms(output_path))


class HighPassPlugin(EffectModule):
    plugin_id = "highpass"
    label = "High-Pass Filter"
    category = "eq"

    def default_params(self):
        return {"cutoff_hz": 80}

    def process(self, input_path, output_path, *, params=None, ctx=None):
        p = params or {}
        hz = int(p.get("cutoff_hz") or 80)
        subprocess.run(
            [_ffmpeg(), "-y", "-i", str(input_path), "-af", f"highpass=f={hz}", str(output_path)],
            check=True,
            capture_output=True,
        )
        return ProcessResult(str(output_path), duration_ms=_dur_ms(output_path))


class CompressorPlugin(EffectModule):
    plugin_id = "compressor"
    label = "Compressor"
    category = "dynamics"

    def default_params(self):
        return {"threshold_db": -18, "ratio": 3.0, "attack_ms": 5, "release_ms": 50}

    def process(self, input_path, output_path, *, params=None, ctx=None):
        p = params or {}
        af = (
            f"acompressor=threshold={p.get('threshold_db', -18)}dB:"
            f"ratio={p.get('ratio', 3)}:attack={p.get('attack_ms', 5)}:"
            f"release={p.get('release_ms', 50)}"
        )
        subprocess.run(
            [_ffmpeg(), "-y", "-i", str(input_path), "-af", af, str(output_path)],
            check=True,
            capture_output=True,
        )
        return ProcessResult(str(output_path), duration_ms=_dur_ms(output_path))


class LimiterPlugin(EffectModule):
    plugin_id = "limiter"
    label = "Limiter"
    category = "dynamics"

    def default_params(self):
        return {"limit_db": -1.0}

    def process(self, input_path, output_path, *, params=None, ctx=None):
        p = params or {}
        lim = float(p.get("limit_db") or -1.0)
        subprocess.run(
            [
                _ffmpeg(),
                "-y",
                "-i",
                str(input_path),
                "-af",
                f"alimiter=limit={lim}dB",
                str(output_path),
            ],
            check=True,
            capture_output=True,
        )
        return ProcessResult(str(output_path), duration_ms=_dur_ms(output_path))


class DeEsserPlugin(EffectModule):
    plugin_id = "deesser"
    label = "De-esser"
    category = "dynamics"

    def default_params(self):
        return {"amount": 0.5}

    def process(self, input_path, output_path, *, params=None, ctx=None):
        p = params or {}
        amt = float(p.get("amount") or 0.5)
        af = f"highpass=f=4000,acompressor=threshold=-24dB:ratio=2:attack=1:release=40"
        subprocess.run(
            [_ffmpeg(), "-y", "-i", str(input_path), "-af", af, str(output_path)],
            check=True,
            capture_output=True,
        )
        return ProcessResult(str(output_path), duration_ms=_dur_ms(output_path))


class NormalizePlugin(EffectModule):
    plugin_id = "normalize"
    label = "Loudness Normalize"
    category = "utility"

    def default_params(self):
        return {"target_lufs": -16}

    def process(self, input_path, output_path, *, params=None, ctx=None):
        p = params or {}
        lufs = float(p.get("target_lufs") or -16)
        subprocess.run(
            [
                _ffmpeg(),
                "-y",
                "-i",
                str(input_path),
                "-af",
                f"loudnorm=I={lufs}:TP=-1.5:LRA=11",
                str(output_path),
            ],
            check=True,
            capture_output=True,
        )
        return ProcessResult(str(output_path), duration_ms=_dur_ms(output_path))


class EqPlugin(EffectModule):
    plugin_id = "eq"
    label = "3-Band EQ"
    category = "eq"

    def default_params(self):
        return {"low_db": 0, "mid_db": 0, "high_db": 0}

    def process(self, input_path, output_path, *, params=None, ctx=None):
        p = params or {}
        low = float(p.get("low_db") or 0)
        mid = float(p.get("mid_db") or 0)
        high = float(p.get("high_db") or 0)
        af = f"equalizer=f=100:t=h:width=200:g={low},equalizer=f=1000:t=h:width=500:g={mid},equalizer=f=6000:t=h:width=2000:g={high}"
        subprocess.run(
            [_ffmpeg(), "-y", "-i", str(input_path), "-af", af, str(output_path)],
            check=True,
            capture_output=True,
        )
        return ProcessResult(str(output_path), duration_ms=_dur_ms(output_path))
