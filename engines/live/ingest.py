"""Media ingest adapters for live pipeline."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse


@dataclass
class IngestResult:
    ok: bool
    source_type: str
    local_path: str = ""
    stream_url: str = ""
    title: str = ""
    error: str = ""
    engine: str = ""


class IngestAdapter(Protocol):
    def open(self, uri: str, *, work_dir: Path) -> IngestResult: ...


def _is_url(uri: str) -> bool:
    p = urlparse(uri.strip())
    return p.scheme in ("http", "https", "rtsp", "rtmp", "hls")


def _is_local_media(path: str) -> bool:
    p = Path(path)
    return p.exists() and p.is_file()


class FileIngest:
    engine = "file"

    def open(self, uri: str, *, work_dir: Path) -> IngestResult:
        p = Path(uri.strip()).resolve()
        if not p.is_file():
            return IngestResult(ok=False, source_type="file", error=f"File not found: {p}")
        ext = p.suffix.lower()
        if ext not in {".mp4", ".mkv", ".webm", ".mov", ".avi", ".mp3", ".wav", ".m4a", ".flac"}:
            return IngestResult(
                ok=False,
                source_type="file",
                error=f"Unsupported media extension: {ext}",
            )
        return IngestResult(
            ok=True,
            source_type="file",
            local_path=str(p),
            title=p.name,
            engine=self.engine,
        )


class UrlIngest:
    """yt-dlp when available, else direct HTTP/HLS URL passthrough."""

    engine = "url"

    def open(self, uri: str, *, work_dir: Path) -> IngestResult:
        uri = uri.strip()
        if not _is_url(uri):
            return IngestResult(ok=False, source_type="url", error="Invalid URL")

        ytdlp = shutil.which("yt-dlp") or shutil.which("yt-dlp.exe")
        if ytdlp:
            try:
                proc = subprocess.run(
                    [ytdlp, "-g", "--no-playlist", uri],
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    stream = proc.stdout.strip().splitlines()[0]
                    return IngestResult(
                        ok=True,
                        source_type="url",
                        stream_url=stream,
                        title=uri,
                        engine="yt-dlp",
                    )
                err = (proc.stderr or proc.stdout or "yt-dlp failed").strip()[:400]
                return IngestResult(ok=False, source_type="url", error=err, engine="yt-dlp")
            except subprocess.TimeoutExpired:
                return IngestResult(
                    ok=False, source_type="url", error="yt-dlp timeout", engine="yt-dlp"
                )
            except Exception as e:
                return IngestResult(ok=False, source_type="url", error=str(e), engine="yt-dlp")

        # Direct stream URL (HLS/RTSP) without yt-dlp
        if uri.lower().endswith((".m3u8", ".mpd")) or "m3u8" in uri.lower():
            return IngestResult(
                ok=True,
                source_type="hls",
                stream_url=uri,
                title=uri,
                engine="direct-hls",
            )

        return IngestResult(
            ok=False,
            source_type="url",
            error=(
                "Install yt-dlp for YouTube/Twitch/Vimeo URLs, "
                "or provide direct HLS/RTSP link."
            ),
            engine="none",
        )


def resolve_ingest(uri: str, *, work_dir: Path) -> IngestResult:
    uri = (uri or "").strip()
    if not uri:
        return IngestResult(ok=False, source_type="", error="Empty source URI")
    if _is_local_media(uri) or (not _is_url(uri) and Path(uri).exists()):
        return FileIngest().open(uri, work_dir=work_dir)
    return UrlIngest().open(uri, work_dir=work_dir)
