"""Audio extraction from video — ffprobe diagnostics, ffmpeg with retries, reports."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.audio_extraction")

_APP_DIR = Path(__file__).resolve().parent.parent


@dataclass
class AudioExtractionResult:
    success: bool
    output_path: str
    diagnostics: dict = field(default_factory=dict)
    error: str = ""
    ffmpeg_cmd: list = field(default_factory=list)
    ffmpeg_returncode: int = -1
    ffmpeg_stderr: str = ""
    retry_count: int = 0


def _win_long_path(path: str | Path) -> str:
    """Windows extended-length path for Unicode / long paths."""
    resolved = str(Path(path).resolve())
    if os.name == "nt" and not resolved.startswith("\\\\?\\") and len(resolved) >= 240:
        return "\\\\?\\" + resolved
    return resolved


def _safe_path_for_subprocess(path: str | Path) -> str:
    return _win_long_path(path)


def _run_subprocess(
    cmd: list[str],
    *,
    timeout: int = 300,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def probe_video_metadata(video_path: str | Path) -> dict[str, Any]:
    """ffprobe: container, duration, codec, audio streams."""
    from engines.ffmpeg_paths import find_ffprobe

    path = Path(video_path)
    meta: dict[str, Any] = {
        "video_path": str(path),
        "exists": path.is_file(),
        "size_bytes": path.stat().st_size if path.is_file() else 0,
        "ffprobe_available": False,
        "container": None,
        "duration_s": None,
        "video_codec": None,
        "audio_tracks": [],
        "selected_track": None,
        "ffprobe_error": None,
    }
    if not path.is_file():
        meta["ffprobe_error"] = "file_not_found"
        return meta

    ffprobe = find_ffprobe()
    if not ffprobe:
        meta["ffprobe_error"] = "ffprobe_not_found"
        return meta

    meta["ffprobe_available"] = True
    probe_path = _safe_path_for_subprocess(path)
    try:
        res = _run_subprocess(
            [
                ffprobe,
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                probe_path,
            ],
            timeout=60,
        )
        if res.returncode != 0:
            meta["ffprobe_error"] = (res.stderr or res.stdout or "ffprobe failed")[:2000]
            return meta
        payload = json.loads(res.stdout or "{}")
    except json.JSONDecodeError as exc:
        meta["ffprobe_error"] = f"invalid_json: {exc}"
        return meta
    except Exception as exc:
        meta["ffprobe_error"] = str(exc)
        return meta

    fmt = payload.get("format") or {}
    meta["container"] = fmt.get("format_name")
    try:
        dur = float(fmt.get("duration") or 0)
        meta["duration_s"] = dur if dur > 0 else None
    except (TypeError, ValueError):
        meta["duration_s"] = None

    audio_tracks: list[dict[str, Any]] = []
    video_codec = None
    for stream in payload.get("streams") or []:
        codec_type = stream.get("codec_type")
        if codec_type == "video" and not video_codec:
            video_codec = stream.get("codec_name")
        if codec_type != "audio":
            continue
        idx = stream.get("index")
        audio_tracks.append(
            {
                "index": idx,
                "codec": stream.get("codec_name"),
                "channels": stream.get("channels"),
                "sample_rate": stream.get("sample_rate"),
                "language": stream.get("tags", {}).get("language"),
                "title": stream.get("tags", {}).get("title"),
            }
        )
    meta["video_codec"] = video_codec
    meta["audio_tracks"] = audio_tracks
    if audio_tracks:
        meta["selected_track"] = audio_tracks[0]
    return meta


def _build_ffmpeg_cmd(
    ffmpeg_bin: str,
    video_path: str | Path,
    output_path: str | Path,
    *,
    audio_stream_index: int | None = None,
) -> list[str]:
    video = _safe_path_for_subprocess(video_path)
    out = _safe_path_for_subprocess(output_path)
    cmd = [ffmpeg_bin, "-y", "-i", video]
    if audio_stream_index is not None:
        cmd.extend(["-map", f"0:a:{audio_stream_index}"])
    cmd.extend(["-vn", "-acodec", "mp3", "-ar", "16000", "-ac", "1", out])
    return cmd


def _classify_failure(
    *,
    video_exists: bool,
    video_path: str,
    ffmpeg_bin: str | None,
    audio_tracks: list,
    output_path: Path,
    returncode: int,
    stderr: str,
    exc: BaseException | None = None,
) -> str:
    if not video_exists:
        return f"Файл не найден: {video_path}"
    if not ffmpeg_bin:
        return "FFmpeg не найден в PATH"
    if not audio_tracks:
        return "В видео нет аудиодорожки"
    if exc and isinstance(exc, PermissionError):
        return "Не удалось записать файл: проверьте права"
    if not output_path.parent.exists():
        return "Не удалось записать файл: проверьте права"
    err_lower = (stderr or str(exc or "")).lower()
    if "permission" in err_lower or "access is denied" in err_lower:
        return "Не удалось записать файл: проверьте права"
    if "no such file" in err_lower or "does not exist" in err_lower:
        return f"Файл не найден: {video_path}"
    if returncode != 0:
        tail = (stderr or "").strip().splitlines()
        hint = tail[-1][:200] if tail else f"код возврата {returncode}"
        return f"Не удалось извлечь аудио: {hint}"
    if not output_path.is_file() or output_path.stat().st_size <= 0:
        return "Не удалось записать файл: проверьте права"
    return "Не удалось извлечь аудио из видео"


def extract_audio_from_video(
    video_path: str | Path,
    output_dir: str | Path,
    task_id: str,
    *,
    output_path: str | Path | None = None,
    max_retries: int = 3,
    app_dir: Path | None = None,
) -> AudioExtractionResult:
    """
    Full diagnostic extraction with ffprobe metadata, ffmpeg retries, and logging.
    """
    from engines.ffmpeg_paths import find_ffmpeg

    started = time.perf_counter()
    video = Path(video_path)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = Path(output_path) if output_path else out_dir / f"{video.stem}_extracted.mp3"

    probe = probe_video_metadata(video)
    diagnostics: dict[str, Any] = {
        "task_id": task_id,
        "probe": probe,
        "attempts": [],
        "traceback": None,
    }

    ffmpeg_bin = find_ffmpeg()
    result = AudioExtractionResult(success=False, output_path=str(out_file), diagnostics=diagnostics)

    if not video.is_file():
        result.error = _classify_failure(
            video_exists=False,
            video_path=str(video),
            ffmpeg_bin=ffmpeg_bin,
            audio_tracks=[],
            output_path=out_file,
            returncode=-1,
            stderr="",
        )
        diagnostics["failure_reason"] = result.error
        diagnostics["operation_duration_ms"] = round((time.perf_counter() - started) * 1000, 1)
        return result

    if not ffmpeg_bin:
        result.error = "FFmpeg не найден в PATH"
        diagnostics["failure_reason"] = result.error
        diagnostics["operation_duration_ms"] = round((time.perf_counter() - started) * 1000, 1)
        return result

    if not probe.get("audio_tracks"):
        result.error = "В видео нет аудиодорожки"
        diagnostics["failure_reason"] = result.error
        diagnostics["operation_duration_ms"] = round((time.perf_counter() - started) * 1000, 1)
        return result

    selected = probe.get("selected_track") or {}
    stream_idx = 0
    for i, tr in enumerate(probe.get("audio_tracks") or []):
        if tr.get("index") == selected.get("index"):
            stream_idx = i
            break

    last_stderr = ""
    last_rc = -1
    last_cmd: list[str] = []
    last_exc: BaseException | None = None

    for attempt in range(max(1, max_retries)):
        if attempt > 0:
            time.sleep(min(2 ** attempt, 8))
        cmd = _build_ffmpeg_cmd(ffmpeg_bin, video, out_file, audio_stream_index=stream_idx)
        last_cmd = cmd
        attempt_info: dict[str, Any] = {
            "attempt": attempt + 1,
            "ffmpeg_cmd": cmd,
            "ffmpeg_returncode": None,
            "ffmpeg_stderr": "",
            "ffmpeg_stdout": "",
            "exception": None,
        }
        try:
            res = _run_subprocess(cmd, timeout=300)
            last_rc = res.returncode
            last_stderr = res.stderr or ""
            attempt_info["ffmpeg_returncode"] = res.returncode
            attempt_info["ffmpeg_stderr"] = last_stderr[:8000]
            attempt_info["ffmpeg_stdout"] = (res.stdout or "")[:2000]
            if res.returncode == 0 and out_file.is_file() and out_file.stat().st_size > 0:
                result.success = True
                result.output_path = str(out_file)
                result.ffmpeg_cmd = cmd
                result.ffmpeg_returncode = res.returncode
                result.ffmpeg_stderr = last_stderr
                result.retry_count = attempt
                diagnostics["attempts"].append(attempt_info)
                diagnostics["failure_reason"] = None
                diagnostics["output_size_bytes"] = out_file.stat().st_size
                diagnostics["operation_duration_ms"] = round((time.perf_counter() - started) * 1000, 1)
                logger.info(
                    "Task %s: audio extracted → %s (%d bytes, attempt %d)",
                    task_id,
                    out_file,
                    out_file.stat().st_size,
                    attempt + 1,
                )
                return result
        except subprocess.TimeoutExpired as exc:
            last_exc = exc
            attempt_info["exception"] = f"TimeoutExpired: {exc}"
        except Exception as exc:
            last_exc = exc
            attempt_info["exception"] = traceback.format_exc()
            diagnostics["traceback"] = attempt_info["exception"]
        diagnostics["attempts"].append(attempt_info)

    result.retry_count = max(0, max_retries - 1)
    result.ffmpeg_cmd = last_cmd
    result.ffmpeg_returncode = last_rc
    result.ffmpeg_stderr = last_stderr
    result.error = _classify_failure(
        video_exists=True,
        video_path=str(video),
        ffmpeg_bin=ffmpeg_bin,
        audio_tracks=probe.get("audio_tracks") or [],
        output_path=out_file,
        returncode=last_rc,
        stderr=last_stderr,
        exc=last_exc,
    )
    diagnostics["failure_reason"] = result.error
    diagnostics["operation_duration_ms"] = round((time.perf_counter() - started) * 1000, 1)
    logger.error(
        "Task %s: audio extraction failed after %d attempts: %s",
        task_id,
        max_retries,
        result.error,
    )
    return result


def _diagnostics_root(app_dir: Path | None = None) -> Path:
    base = app_dir or _APP_DIR
    return base / "output" / "diagnostics"


def save_audio_extraction_report(
    task_id: str,
    result: AudioExtractionResult,
    *,
    app_dir: Path | None = None,
    project_uuid: str | None = None,
) -> Path:
    """Persist audio_extraction_report.json under output/diagnostics/{task_id}/."""
    base = app_dir or _APP_DIR
    report_dir = _diagnostics_root(base) / task_id
    report_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "task_id": task_id,
        "success": result.success,
        "output_path": result.output_path,
        "error": result.error,
        "ffmpeg_cmd": result.ffmpeg_cmd,
        "ffmpeg_returncode": result.ffmpeg_returncode,
        "ffmpeg_stderr": result.ffmpeg_stderr,
        "retry_count": result.retry_count,
        **result.diagnostics,
    }
    report_path = report_dir / "audio_extraction_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    if result.ffmpeg_stderr:
        stderr_path = report_dir / "ffmpeg_stderr.log"
        stderr_path.write_text(result.ffmpeg_stderr, encoding="utf-8", errors="replace")

    if result.diagnostics.get("traceback"):
        tb_path = report_dir / "traceback.txt"
        tb_path.write_text(str(result.diagnostics["traceback"]), encoding="utf-8")

    if project_uuid:
        manifest_dir = base / "output" / "manifests" / project_uuid
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest_copy = manifest_dir / "audio_extraction_report.json"
        manifest_copy.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    return report_path


def record_audio_extraction_openddf(
    task_id: str,
    result: AudioExtractionResult,
    *,
    app_dir: Path | None = None,
    project_uuid: str | None = None,
) -> Path:
    """Save report and record step in OpenDDF."""
    from engines.open_ddf import open_ddf

    report_path = save_audio_extraction_report(
        task_id,
        result,
        app_dir=app_dir,
        project_uuid=project_uuid,
    )
    duration_ms = float((result.diagnostics or {}).get("operation_duration_ms") or 0)
    open_ddf.record_agent(
        task_id,
        "AudioExtraction",
        called=True,
        success=result.success,
        error=result.error or None,
        decision="extracted" if result.success else "failed",
        retry_count=result.retry_count,
        execution_time_ms=duration_ms,
        output_metrics={
            "output_path": result.output_path,
            "report_path": str(report_path),
            "ffmpeg_returncode": result.ffmpeg_returncode,
        },
        input_metrics={
            "video_path": (result.diagnostics.get("probe") or {}).get("video_path"),
            "audio_tracks": len((result.diagnostics.get("probe") or {}).get("audio_tracks") or []),
        },
    )
    try:
        open_ddf.save(task_id)
    except Exception as exc:
        logger.debug("OpenDDF save after audio extraction skipped: %s", exc)
    return report_path


def user_friendly_extract_error(result: AudioExtractionResult) -> str:
    """User-facing Russian message for extraction failures."""
    return result.error or "Не удалось извлечь аудио из видео"


def error_code_for_result(result: AudioExtractionResult) -> str:
    err = result.error or ""
    if "FFmpeg не найден" in err:
        return "FFMPEG_NOT_FOUND"
    if "нет аудиодорожки" in err:
        return "NO_AUDIO_TRACK"
    if "проверьте права" in err:
        return "AUDIO_WRITE_FAILED"
    if err.startswith("Файл не найден"):
        return "AUDIO_FILE_NOT_FOUND"
    return "FFMPEG_EXTRACT_FAILED"


def collect_supplemental_diagnostic_files(
    task_id: str,
    *,
    app_dir: Path | None = None,
) -> dict[str, Any]:
    """Files to merge into passive diagnostic ZIP bundles."""
    base = app_dir or _APP_DIR
    task_diag = _diagnostics_root(base) / task_id
    extras: dict[str, Any] = {}

    report = task_diag / "audio_extraction_report.json"
    if report.is_file():
        try:
            extras["audio_extraction_report.json"] = json.loads(
                report.read_text(encoding="utf-8")
            )
        except Exception:
            extras["audio_extraction_report.json"] = report.read_text(
                encoding="utf-8", errors="replace"
            )

    stderr_log = task_diag / "ffmpeg_stderr.log"
    if stderr_log.is_file():
        extras["ffmpeg_stderr.log"] = stderr_log.read_text(encoding="utf-8", errors="replace")

    tb = task_diag / "traceback.txt"
    if tb.is_file() and "traceback.txt" not in extras:
        extras["traceback.txt"] = tb.read_text(encoding="utf-8", errors="replace")

    ddf = base / "output" / f"ddf_{task_id}.json"
    if ddf.is_file():
        try:
            extras["ddf_report.json"] = json.loads(ddf.read_text(encoding="utf-8"))
        except Exception:
            extras["ddf_report.json"] = ddf.read_text(encoding="utf-8", errors="replace")

    return extras


__all__ = [
    "AudioExtractionResult",
    "extract_audio_from_video",
    "save_audio_extraction_report",
    "record_audio_extraction_openddf",
    "error_code_for_result",
    "user_friendly_extract_error",
    "collect_supplemental_diagnostic_files",
    "probe_video_metadata",
]
