"""Recording Studio — FX chain and multitrack (TZ Etap 5)."""

from __future__ import annotations

import shutil
import subprocess
import uuid
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engines.ffmpeg_paths import find_ffmpeg
from engines.platform_diagnostics.sink import PlatformTraceSink


@dataclass
class FxPreset:
    noise_reduction: bool = True
    compress: bool = True
    limit: bool = True
    normalize: bool = True
    eq_highpass: bool = True


@dataclass
class RecordingStudioSession:
    session_id: str
    app_dir: Path
    tracks: list[str] = field(default_factory=list)
    sink: PlatformTraceSink | None = None
    output_dir: Path = field(default_factory=Path)

    @classmethod
    def create(cls, app_dir: Path) -> "RecordingStudioSession":
        sid = uuid.uuid4().hex[:12]
        out = Path(app_dir) / "output" / "recording_studio" / sid
        out.mkdir(parents=True, exist_ok=True)
        return cls(
            session_id=sid,
            app_dir=Path(app_dir),
            output_dir=out,
            sink=PlatformTraceSink(Path(app_dir), module="recording", session_id=sid),
        )

    def import_track(self, wav_path: str, *, name: str = "") -> dict[str, Any]:
        src = Path(wav_path)
        if not src.is_file():
            return {"ok": False, "error": "File not found"}
        label = name or src.stem
        dest = self.output_dir / f"track_{len(self.tracks):02d}_{label}.wav"
        shutil.copy2(src, dest)
        self.tracks.append(str(dest))
        self.sink.log(stage="recording.import", input_preview=str(src), output_preview=str(dest))
        return {"ok": True, "track": str(dest), "index": len(self.tracks) - 1}

    def apply_fx(self, track_path: str, preset: FxPreset | None = None) -> dict[str, Any]:
        preset = preset or FxPreset()
        inp = Path(track_path)
        if not inp.is_file():
            return {"ok": False, "error": "Track not found"}

        out = inp.with_name(inp.stem + "_processed.wav")
        engines_used: list[str] = []

        # RNNoise / noisereduce optional
        current = inp
        if preset.noise_reduction:
            nr_out = self._try_noisereduce(current)
            if nr_out:
                current = nr_out
                engines_used.append("noisereduce")

        # pedalboard optional
        if preset.compress or preset.limit or preset.eq_highpass:
            pb_out = self._try_pedalboard(current, preset)
            if pb_out:
                current = pb_out
                engines_used.append("pedalboard")

        # pyloudnorm / ffmpeg normalize
        if preset.normalize:
            norm = self._normalize_ffmpeg(current, out)
            if norm:
                current = Path(norm)
                engines_used.append("ffmpeg-loudnorm")

        if current != out and current != inp:
            if current != out:
                shutil.copy2(current, out)
        else:
            shutil.copy2(inp, out)

        self.sink.log(
            stage="recording.fx",
            input_preview=str(inp),
            output_preview=str(out),
            engine=",".join(engines_used) or "copy",
        )
        return {"ok": True, "output": str(out), "engines": engines_used}

    def _try_noisereduce(self, path: Path) -> Path | None:
        try:
            import noisereduce as nr  # type: ignore
            import numpy as np  # type: ignore

            with wave.open(str(path), "rb") as w:
                rate = w.getframerate()
                frames = w.readframes(w.getnframes())
                sampwidth = w.getsampwidth()
            dtype = np.int16 if sampwidth == 2 else np.int8
            data = np.frombuffer(frames, dtype=dtype).astype(np.float32)
            if data.size == 0:
                return None
            reduced = nr.reduce_noise(y=data, sr=rate)
            out = path.with_name(path.stem + "_nr.wav")
            reduced_i16 = np.clip(reduced, -32768, 32767).astype(np.int16)
            with wave.open(str(out), "wb") as wo:
                wo.setnchannels(1)
                wo.setsampwidth(2)
                wo.setframerate(rate)
                wo.writeframes(reduced_i16.tobytes())
            return out
        except Exception:
            return None

    def _try_pedalboard(self, path: Path, preset: FxPreset) -> Path | None:
        try:
            from pedalboard import Compressor, HighpassFilter, Limiter, Pedalboard  # type: ignore
            from pedalboard.io import AudioFile  # type: ignore

            board = Pedalboard([])
            if preset.eq_highpass:
                board.append(HighpassFilter(cutoff_frequency_hz=80))
            if preset.compress:
                board.append(Compressor(threshold_db=-20, ratio=3))
            if preset.limit:
                board.append(Limiter(threshold_db=-1.0))
            out = path.with_name(path.stem + "_pb.wav")
            with AudioFile(str(path)) as f:
                audio = f.read(f.frames)
                rate = f.samplerate
            effected = board(audio, rate)
            with AudioFile(str(out), "w", rate, effected.shape[0]) as o:
                o.write(effected)
            return out
        except Exception:
            return None

    def _normalize_ffmpeg(self, path: Path, dest: Path) -> str | None:
        ff = find_ffmpeg()
        if not ff:
            return None
        cmd = [
            ff,
            "-y",
            "-i",
            str(path),
            "-af",
            "loudnorm=I=-16:TP=-1.5:LRA=11",
            str(dest),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode == 0 and dest.is_file():
            return str(dest)
        return None

    def diagnostics(self) -> dict[str, Any]:
        snap = self.sink.snapshot()
        snap["tracks"] = list(self.tracks)
        return snap
