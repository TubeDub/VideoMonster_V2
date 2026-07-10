"""Audio plugin implementations — ffmpeg loudness + compressor."""

from __future__ import annotations

import logging
import shutil
import subprocess
import uuid
from pathlib import Path

from engines.plugins.base import AudioPlugin, PluginParams

logger = logging.getLogger(__name__)


def _ffmpeg_run(in_path: Path, out_path: Path, af_filter: str) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found")
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(in_path),
            "-af",
            af_filter,
            str(out_path),
        ],
        check=True,
        capture_output=True,
    )


class LoudnessNormalizePlugin(AudioPlugin):
    plugin_id = "eq"
    label = "Loudness Normalize"
    i18n_key = "studio.plugin.loudness"

    def process(self, audio_path: str | Path, params: PluginParams | None = None) -> str:
        p = Path(audio_path)
        params = params or PluginParams()
        target = float(params.get("target_lufs", -16.0))
        out = p.parent / f"{p.stem}_loudnorm_{uuid.uuid4().hex[:6]}{p.suffix or '.wav'}"
        af = f"loudnorm=I={target}:TP=-1.5:LRA=11"
        try:
            _ffmpeg_run(p, out, af)
            logger.info("plugin eq: loudnorm %s -> %s", p.name, out.name)
            return str(out)
        except Exception as exc:
            logger.warning("plugin eq failed: %s — pass-through", exc)
            return str(p)


class SimpleCompressorPlugin(AudioPlugin):
    plugin_id = "compressor"
    label = "Simple Compressor"
    i18n_key = "studio.plugin.compressor"

    def process(self, audio_path: str | Path, params: PluginParams | None = None) -> str:
        p = Path(audio_path)
        params = params or PluginParams()
        threshold = float(params.get("threshold", -18.0))
        ratio = float(params.get("ratio", 3.0))
        out = p.parent / f"{p.stem}_comp_{uuid.uuid4().hex[:6]}{p.suffix or '.wav'}"
        af = f"acompressor=threshold={threshold}dB:ratio={ratio}:attack=5:release=50"
        try:
            _ffmpeg_run(p, out, af)
            logger.info("plugin compressor: %s -> %s", p.name, out.name)
            return str(out)
        except Exception as exc:
            logger.warning("plugin compressor failed: %s — pass-through", exc)
            return str(p)
