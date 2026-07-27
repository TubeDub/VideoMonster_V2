"""Lip Sync — duration/offset analysis for Cinema / StreamDub mode."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

from engines.ffmpeg_paths import find_ffmpeg, find_ffprobe
from engines.streamdub.base import ModuleCapabilities, StreamModule

logger = logging.getLogger("tubedub.lip_sync")


def _probe_duration_sec(path: Path) -> float | None:
    if not path.is_file():
        return None
    probe = find_ffprobe()
    if probe:
        cmd = [
            str(probe),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        try:
            return float((proc.stdout or "").strip())
        except ValueError:
            return None
    # Fallback: ffmpeg -i probe via stderr (less reliable).
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return None
    proc = subprocess.run(
        [str(ffmpeg), "-i", str(path), "-f", "null", "-"],
        capture_output=True,
        text=True,
        check=False,
    )
    text = (proc.stderr or "") + (proc.stdout or "")
    import re

    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if not m:
        return None
    h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return h * 3600 + mi * 60 + s


class LipSyncEngine(StreamModule):
    module_id = "lip_sync"

    def _on_initialize(self, *, app_dir: Any = None, config: dict[str, Any] | None = None) -> None:
        self._app_dir = Path(app_dir) if app_dir else Path.cwd()
        self._cfg = config or {}

    def _on_health_check(self) -> tuple[bool, str, dict[str, Any] | None]:
        ok = bool(find_ffmpeg() or find_ffprobe())
        return ok, "ready" if ok else "ffmpeg_missing", {"status": "ready" if ok else "degraded"}

    def capabilities(self) -> ModuleCapabilities:
        return ModuleCapabilities(
            module_id=self.module_id,
            features=["lip_sync", "offset_analysis"],
            meta={"status": "ready", "planned": False},
        )

    def process(self, payload: dict[str, Any]) -> dict[str, Any]:
        video = Path(str(payload.get("video_path") or payload.get("video") or ""))
        audio = Path(str(payload.get("audio_path") or payload.get("audio") or ""))
        words = payload.get("word_timing") or payload.get("words") or []

        video_dur = _probe_duration_sec(video) if video.name else None
        audio_dur = _probe_duration_sec(audio) if audio.name else None

        offset_ms = 0
        if video_dur is not None and audio_dur is not None:
            offset_ms = int(round((video_dur - audio_dur) * 1000))

        word_span_ms = None
        if isinstance(words, list) and words:
            starts = []
            ends = []
            for w in words:
                if isinstance(w, dict):
                    if "start" in w or "start_ms" in w:
                        starts.append(float(w.get("start_ms", w.get("start", 0))))
                    if "end" in w or "end_ms" in w:
                        ends.append(float(w.get("end_ms", w.get("end", 0))))
            if starts and ends:
                # Normalize: if values look like seconds (< 1000 avg), convert.
                span = max(ends) - min(starts)
                word_span_ms = int(span * 1000) if span < 600 else int(span)

        recommendation = "aligned"
        if abs(offset_ms) > 80:
            recommendation = "shift_audio" if offset_ms > 0 else "pad_audio"

        result = {
            "lip_sync": {
                "status": "ok",
                "video_duration_sec": video_dur,
                "audio_duration_sec": audio_dur,
                "offset_ms": offset_ms,
                "word_span_ms": word_span_ms,
                "recommendation": recommendation,
                "engine": "ffmpeg_duration_v1",
            }
        }
        return {**payload, **result}
