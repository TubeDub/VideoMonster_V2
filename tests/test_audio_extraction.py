"""Tests for engines.audio_extraction."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from engines.audio_extraction import (
    AudioExtractionResult,
    error_code_for_result,
    extract_audio_from_video,
    probe_video_metadata,
    save_audio_extraction_report,
    user_friendly_extract_error,
)


def _probe_with_audio():
    return {
        "video_path": "v.mp4",
        "exists": True,
        "size_bytes": 1000,
        "ffprobe_available": True,
        "audio_tracks": [{"index": 1, "codec": "aac"}],
        "selected_track": {"index": 1, "codec": "aac"},
    }


def test_extract_success_mock(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake-video")
    out_mp3 = tmp_path / "clip_extracted.mp3"

    ok_proc = MagicMock(returncode=0, stdout="", stderr="ffmpeg ok")

    def fake_run(cmd, **_k):
        Path(cmd[-1]).write_bytes(b"mp3-bytes")
        return ok_proc

    with patch("engines.audio_extraction.probe_video_metadata", return_value=_probe_with_audio()):
        with patch("engines.ffmpeg_paths.find_ffmpeg", return_value="ffmpeg"):
            with patch("engines.audio_extraction._run_subprocess", side_effect=fake_run):
                result = extract_audio_from_video(
                    video,
                    tmp_path,
                    "task-ok",
                    output_path=out_mp3,
                )

    assert result.success is True
    assert result.retry_count == 0
    assert result.ffmpeg_returncode == 0
    assert out_mp3.is_file()


def test_retry_on_failure(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake-video")
    out_mp3 = tmp_path / "out.mp3"

    fail_proc = MagicMock(returncode=1, stdout="", stderr="decode error")
    ok_proc = MagicMock(returncode=0, stdout="", stderr="")

    calls = {"n": 0}

    def fake_run(cmd, **_k):
        calls["n"] += 1
        if calls["n"] < 3:
            return fail_proc
        Path(cmd[-1]).write_bytes(b"mp3")
        return ok_proc

    with patch("engines.audio_extraction.probe_video_metadata", return_value=_probe_with_audio()):
        with patch("engines.ffmpeg_paths.find_ffmpeg", return_value="ffmpeg"):
            with patch("engines.audio_extraction._run_subprocess", side_effect=fake_run):
                with patch("engines.audio_extraction.time.sleep"):
                    result = extract_audio_from_video(
                        video,
                        tmp_path,
                        "task-retry",
                        output_path=out_mp3,
                        max_retries=3,
                    )

    assert result.success is True
    assert result.retry_count == 2
    assert calls["n"] == 3


def test_no_audio_track_detected(tmp_path):
    video = tmp_path / "silent.mp4"
    video.write_bytes(b"video-only")

    with patch(
        "engines.audio_extraction.probe_video_metadata",
        return_value={
            "exists": True,
            "audio_tracks": [],
            "video_path": str(video),
        },
    ):
        with patch("engines.ffmpeg_paths.find_ffmpeg", return_value="ffmpeg"):
            result = extract_audio_from_video(video, tmp_path, "task-no-audio")

    assert result.success is False
    assert "нет аудиодорожки" in result.error
    assert error_code_for_result(result) == "NO_AUDIO_TRACK"


def test_file_not_found(tmp_path):
    missing = tmp_path / "missing.mp4"

    result = extract_audio_from_video(missing, tmp_path, "task-missing")

    assert result.success is False
    assert result.error.startswith("Файл не найден")
    assert error_code_for_result(result) == "AUDIO_FILE_NOT_FOUND"


def test_report_json_created(tmp_path):
    result = AudioExtractionResult(
        success=False,
        output_path=str(tmp_path / "out.mp3"),
        error="FFmpeg не найден в PATH",
        diagnostics={"probe": {"video_path": "x.mp4"}, "operation_duration_ms": 12.5},
        ffmpeg_stderr="not found",
    )
    report_path = save_audio_extraction_report(
        "task-report",
        result,
        app_dir=tmp_path,
        project_uuid="proj-1",
    )
    assert report_path.is_file()
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["task_id"] == "task-report"
    assert payload["success"] is False
    assert (tmp_path / "output" / "manifests" / "proj-1" / "audio_extraction_report.json").is_file()
    assert (tmp_path / "output" / "diagnostics" / "task-report" / "ffmpeg_stderr.log").is_file()


def test_unicode_path_handling(tmp_path):
    video = tmp_path / "видео_тест.mp4"
    video.write_bytes(b"fake")
    out_mp3 = tmp_path / "видео_extracted.mp3"

    ok_proc = MagicMock(returncode=0, stdout="", stderr="")

    def fake_run(cmd, **_k):
        Path(cmd[-1]).write_bytes(b"mp3")
        return ok_proc

    with patch("engines.audio_extraction.probe_video_metadata", return_value=_probe_with_audio()):
        with patch("engines.ffmpeg_paths.find_ffmpeg", return_value="ffmpeg"):
            with patch("engines.audio_extraction._run_subprocess", side_effect=fake_run) as run_mock:
                result = extract_audio_from_video(
                    video,
                    tmp_path,
                    "task-unicode",
                    output_path=out_mp3,
                )

    assert result.success is True
    cmd = run_mock.call_args[0][0]
    assert any("видео" in str(part) for part in cmd)


def test_user_friendly_extract_error():
    r = AudioExtractionResult(success=False, output_path="", error="FFmpeg не найден в PATH")
    assert user_friendly_extract_error(r) == "FFmpeg не найден в PATH"


def test_probe_video_metadata_missing_file(tmp_path):
    meta = probe_video_metadata(tmp_path / "nope.mp4")
    assert meta["exists"] is False
    assert meta["ffprobe_error"] == "file_not_found"
