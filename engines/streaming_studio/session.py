"""Capture and RTMP publish (FFmpeg-based) — local record → stream path."""

from __future__ import annotations

import platform
import re
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
    # Optional local media file — enables file→record / file→RTMP without dshow
    input_file: str = ""


def probe_streaming_capabilities() -> dict[str, Any]:
    """Honest capability report — engines present or clear gaps."""
    ff = find_ffmpeg()
    is_win = platform.system().lower().startswith("win")
    devices: list[str] = []
    device_error = ""
    if ff and is_win:
        try:
            proc = subprocess.run(
                [ff, "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            blob = (proc.stderr or "") + (proc.stdout or "")
            devices = re.findall(r'"([^"]+)"', blob)
        except Exception as e:
            device_error = str(e)
    return {
        "ok": bool(ff),
        "ffmpeg": bool(ff),
        "ffmpeg_path": ff or "",
        "platform": platform.system(),
        "dshow_available": is_win and bool(ff),
        "gdigrab_available": is_win and bool(ff),
        "file_to_rtmp": bool(ff),
        "record_to_file": bool(ff),
        "dshow_devices": devices[:40],
        "device_error": device_error,
        "default_rtmp_configured": bool(default_rtmp_url()),
        "notes": (
            []
            if ff
            else ["FFmpeg not found — install FFmpeg and ensure it is on PATH"]
        ),
    }


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

    def _kill_pid(self, key: str) -> None:
        pid = self.meta.get(key)
        if not pid:
            return
        try:
            if platform.system().lower().startswith("win"):
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F"],
                    capture_output=True,
                    check=False,
                )
            else:
                subprocess.run(["kill", "-9", str(pid)], capture_output=True, check=False)
        except Exception:
            pass
        self.meta.pop(key, None)

    def start_record(self) -> dict[str, Any]:
        caps = probe_streaming_capabilities()
        ff = caps.get("ffmpeg_path") or find_ffmpeg()
        if not ff:
            self.status = "error"
            return {
                "ok": False,
                "error": "FFmpeg not found — cannot start capture/stream pipeline",
                "capabilities": caps,
            }

        out_file = self.output_dir / "capture.mp4"
        input_file = (self.spec.input_file or "").strip()
        cmd: list[str] = [ff, "-y"]

        if input_file:
            src = Path(input_file)
            if not src.is_file():
                self.status = "error"
                return {"ok": False, "error": f"Input file not found: {input_file}"}
            cmd.extend(["-i", str(src), "-c", "copy", str(out_file)])
            engine = "ffmpeg-file"
        elif self.spec.screen and caps.get("gdigrab_available"):
            # Windows desktop capture → local MP4
            cmd.extend(
                [
                    "-f",
                    "gdigrab",
                    "-framerate",
                    "15",
                    "-i",
                    "desktop",
                ]
            )
            if self.spec.microphone and caps.get("dshow_available"):
                cmd.extend(["-f", "dshow", "-i", "audio=Microphone"])
                cmd.extend(["-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-b:a", "128k"])
            else:
                cmd.extend(["-c:v", "libx264", "-preset", "ultrafast", "-an"])
            cmd.append(str(out_file))
            engine = "ffmpeg-gdigrab"
        elif self.spec.microphone:
            if not caps.get("dshow_available"):
                self.status = "error"
                return {
                    "ok": False,
                    "error": (
                        "Microphone capture requires Windows DirectShow (dshow). "
                        "Provide input_file for a file→record pipeline, or use file-to-rtmp."
                    ),
                    "capabilities": caps,
                }
            cmd.extend(["-f", "dshow", "-i", "audio=Microphone"])
            cmd.extend(["-c:a", "aac", "-b:a", "192k", str(out_file)])
            engine = "ffmpeg-dshow"
        else:
            self.status = "error"
            return {
                "ok": False,
                "error": "No capture source: enable microphone/screen or pass input_file",
                "capabilities": caps,
            }

        self.sink.log(
            stage="streaming.record.start",
            input_preview=str(self.spec),
            engine=engine,
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
            self.meta["engine"] = engine
            self.status = "recording"
            return {
                "ok": True,
                "session_id": self.session_id,
                "path": str(out_file),
                "engine": engine,
            }
        except Exception as e:
            self.status = "error"
            self.sink.log(stage="streaming.record.error", error=str(e))
            return {"ok": False, "error": str(e), "capabilities": caps}

    def stop_record(self) -> dict[str, Any]:
        self._kill_pid("record_pid")
        self.status = "stopped"
        self.sink.log(
            stage="streaming.record.stop",
            output_preview=str(self.meta.get("record_path", "")),
        )
        return {
            "ok": True,
            "session_id": self.session_id,
            "path": self.meta.get("record_path", ""),
        }

    def start_rtmp(self, url: str | None = None) -> dict[str, Any]:
        ff = find_ffmpeg()
        rtmp = (url or self.spec.rtmp_url or default_rtmp_url()).strip()
        if not ff:
            return {"ok": False, "error": "FFmpeg not found"}
        if not rtmp:
            return {"ok": False, "error": "RTMP URL not configured (VM_STREAMING_RTMP_URL)"}

        record_path = self.meta.get("record_path")
        if not record_path or not Path(str(record_path)).is_file():
            return {
                "ok": False,
                "error": "No recorded file yet — start_record first, or use file_to_rtmp",
            }

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
            return {"ok": True, "rtmp_url": rtmp, "source": record_path}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def file_to_rtmp(self, file_path: str, rtmp_url: str | None = None) -> dict[str, Any]:
        """Usable local pipeline: existing media file → RTMP (no dshow required)."""
        ff = find_ffmpeg()
        if not ff:
            return {"ok": False, "error": "FFmpeg not found", "capabilities": probe_streaming_capabilities()}
        src = Path(file_path)
        if not src.is_file():
            return {"ok": False, "error": f"File not found: {file_path}"}
        rtmp = (rtmp_url or self.spec.rtmp_url or default_rtmp_url()).strip()
        if not rtmp:
            return {"ok": False, "error": "RTMP URL not configured (VM_STREAMING_RTMP_URL or request body)"}

        # Also keep a session-local copy for diagnostics / re-publish
        dest = self.output_dir / src.name
        try:
            if src.resolve() != dest.resolve():
                dest.write_bytes(src.read_bytes())
        except Exception:
            dest = src

        self.meta["record_path"] = str(dest)
        cmd = [ff, "-re", "-i", str(dest), "-c", "copy", "-f", "flv", rtmp]
        self.sink.log(stage="streaming.file_to_rtmp", input_preview=str(dest), engine="ffmpeg-rtmp")
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            self.meta["rtmp_pid"] = proc.pid
            self.meta["rtmp_url"] = rtmp
            self.status = "streaming"
            return {
                "ok": True,
                "session_id": self.session_id,
                "rtmp_url": rtmp,
                "source": str(dest),
            }
        except Exception as e:
            self.status = "error"
            return {"ok": False, "error": str(e)}

    def stop_all(self) -> dict[str, Any]:
        self._kill_pid("rtmp_pid")
        self._kill_pid("record_pid")
        self.status = "stopped"
        return {"ok": True, "session_id": self.session_id, "path": self.meta.get("record_path", "")}

    def diagnostics(self) -> dict[str, Any]:
        base = self.sink.snapshot() if self.sink else {}
        base["status"] = self.status
        base["spec"] = {
            "screen": self.spec.screen,
            "webcam": self.spec.webcam,
            "microphone": self.spec.microphone,
            "system_audio": self.spec.system_audio,
            "input_file": self.spec.input_file,
        }
        base["capabilities"] = probe_streaming_capabilities()
        return base
