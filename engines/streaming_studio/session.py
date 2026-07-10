"""Capture and RTMP publish (FFmpeg-based)."""

from __future__ import annotations

import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engines.ffmpeg_paths import find_ffmpeg
from engines.platform_diagnostics.sink import PlatformTraceSink
from engines.streaming_studio.config import default_rtmp_url


@dataclass
class CaptureSpec:
    screen: bool = False
    webcam: bool = False
    microphone: bool = True
    system_audio: bool = False
    rtmp_url: str = ""


@dataclass
class StreamingSession:
    session_id: str
    app_dir: Path
    spec: CaptureSpec
    status: str = "idle"
    output_dir: Path = field(default_factory=Path)
    sink: PlatformTraceSink | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls, app_dir: Path, spec: CaptureSpec) -> "StreamingSession":
        sid = uuid.uuid4().hex[:12]
        out = Path(app_dir) / "output" / "streaming" / sid
        out.mkdir(parents=True, exist_ok=True)
        return cls(
            session_id=sid,
            app_dir=Path(app_dir),
            spec=spec,
            output_dir=out,
            sink=PlatformTraceSink(Path(app_dir), module="streaming", session_id=sid),
        )

    def start_record(self) -> dict[str, Any]:
        ff = find_ffmpeg()
        if not ff:
            self.status = "error"
            return {"ok": False, "error": "FFmpeg not found"}

        out_file = self.output_dir / "capture.mp4"
        cmd = [ff, "-y"]
        inputs: list[str] = []

        if self.spec.microphone:
            inputs.extend(["-f", "dshow", "-i", "audio=Microphone"])

        if not inputs:
            self.status = "error"
            return {"ok": False, "error": "No capture source configured"}

        cmd.extend(inputs)
        cmd.extend(["-c:a", "aac", "-b:a", "192k", str(out_file)])

        self.sink.log(
            stage="streaming.record.start",
            input_preview=str(self.spec),
            engine="ffmpeg-dshow",
        )
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.meta["record_pid"] = proc.pid
            self.meta["record_path"] = str(out_file)
            self.status = "recording"
            return {"ok": True, "session_id": self.session_id, "path": str(out_file)}
        except Exception as e:
            self.status = "error"
            self.sink.log(stage="streaming.record.error", error=str(e))
            return {"ok": False, "error": str(e)}

    def stop_record(self) -> dict[str, Any]:
        pid = self.meta.get("record_pid")
        if pid:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F"],
                    capture_output=True,
                    check=False,
                )
            except Exception:
                pass
        self.status = "stopped"
        self.sink.log(stage="streaming.record.stop", output_preview=str(self.meta.get("record_path", "")))
        return {"ok": True, "session_id": self.session_id, "path": self.meta.get("record_path", "")}

    def start_rtmp(self, url: str | None = None) -> dict[str, Any]:
        ff = find_ffmpeg()
        rtmp = (url or self.spec.rtmp_url or default_rtmp_url()).strip()
        if not ff:
            return {"ok": False, "error": "FFmpeg not found"}
        if not rtmp:
            return {"ok": False, "error": "RTMP URL not configured (VM_STREAMING_RTMP_URL)"}

        record_path = self.meta.get("record_path")
        if not record_path or not Path(str(record_path)).is_file():
            return {"ok": False, "error": "Start recording first or provide file"}

        cmd = [
            ff,
            "-re",
            "-i",
            str(record_path),
            "-c",
            "copy",
            "-f",
            "flv",
            rtmp,
        ]
        self.sink.log(stage="streaming.rtmp.start", input_preview=rtmp, engine="ffmpeg-rtmp")
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            self.meta["rtmp_pid"] = proc.pid
            self.meta["rtmp_url"] = rtmp
            self.status = "streaming"
            return {"ok": True, "rtmp_url": rtmp}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def diagnostics(self) -> dict[str, Any]:
        base = self.sink.snapshot() if self.sink else {}
        base["status"] = self.status
        base["spec"] = {
            "screen": self.spec.screen,
            "webcam": self.spec.webcam,
            "microphone": self.spec.microphone,
            "system_audio": self.spec.system_audio,
        }
        return base
