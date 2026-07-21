"""Video Merge — mux dubbed audio with source video."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

from engines.streamdub.base import ModuleCapabilities, StreamModule

logger = logging.getLogger("tubedub.streamdub.video_merge")


class VideoMergeEngine(StreamModule):
    module_id = "video_merge"

    def _on_initialize(self, *, app_dir: Any = None, config: dict[str, Any] | None = None) -> None:
        self._app_dir = Path(app_dir) if app_dir else None

    def _on_health_check(self) -> tuple[bool, str, dict[str, Any] | None]:
        ffmpeg = shutil.which("ffmpeg")
        return bool(ffmpeg), ffmpeg or "ffmpeg_missing", None

    def capabilities(self) -> ModuleCapabilities:
        return ModuleCapabilities(
            module_id=self.module_id,
            features=["mux", "audio_replace"],
        )

    def process(self, payload: dict[str, Any]) -> dict[str, Any]:
        video_path = str(payload.get("video_path") or "")
        audio_path = str(payload.get("output_audio") or "")
        project_id = str(payload.get("project_id") or "streamdub")
        out_dir = (self._app_dir or Path(".")) / "output" / "streamdub" / project_id
        out_dir.mkdir(parents=True, exist_ok=True)
        out_video = out_dir / f"{project_id}_OUTPUT.mp4"

        if not video_path or not audio_path:
            return {**payload, "output_video": "", "merge": "skipped"}

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return {**payload, "output_video": "", "merge": "no_ffmpeg"}

        cmd = [
            ffmpeg,
            "-y",
            "-i",
            video_path,
            "-i",
            audio_path,
            "-c:v",
            "copy",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-shortest",
            str(out_video),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=600)
            return {**payload, "output_video": str(out_video), "merge": "ok"}
        except Exception as exc:
            logger.warning("Video merge failed: %s", exc)
            return {**payload, "output_video": "", "merge": "error", "error": str(exc)}
