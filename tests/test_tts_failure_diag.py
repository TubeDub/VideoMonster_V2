"""Tests for Stage 3A.2 TTS Failure Diagnostics."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from engines.dubbing_engine.tts_failure_diag import (
    TTSFailureReport,
    VoiceGenerationError,
    build_failure_report,
    format_diagnostic_block,
    format_ui_message,
    log_tts_failure,
    mark_segment_tts_failed,
    save_failure_report,
)


def _sample_report(**overrides) -> TTSFailureReport:
    base = dict(
        segment_id="abc123def456",
        segment_index=4,
        current=5,
        total=20,
        original_text="Hello world",
        tts_text="Привет мир",
        voice="ru-RU-DmitryNeural",
        language="ru",
        tts_file_path="/tmp/out/seg.mp3",
        error_code="EDGE_TTS",
        error_message="Connection reset",
        traceback="Traceback...\nRuntimeError: Connection reset",
        duration_ms=1234.5,
        task_id="task-1",
    )
    base.update(overrides)
    return TTSFailureReport(**base)


class TestFailureReport:
    def test_format_ui_message_contains_segment_id_and_position(self):
        report = _sample_report()
        msg = format_ui_message(report, "ru")
        assert "abc123def456" in msg
        assert "5/20" in msg
        assert "VoiceGenerationError" in msg
        assert "Edge-TTS" in msg

    def test_format_diagnostic_block_v1(self):
        report = _sample_report(
            tts_text="Добрий вечір.",
            reason="TTS engine returned empty audio.",
            pipeline_state="STOPPED",
        )
        block = format_diagnostic_block(report)
        assert "VoiceGenerationError" in block
        assert "segment_id: abc123def456" in block
        assert "engine: Edge-TTS" in block
        assert "Добрий вечір." in block
        assert "TTS engine returned empty audio." in block
        assert "pipeline_state:\nSTOPPED" in block

    def test_is_tts_diagnostic_message(self):
        from engines.dubbing_engine.tts_failure_diag import is_tts_diagnostic_message

        assert is_tts_diagnostic_message(format_ui_message(_sample_report(), "ru"))
        assert not is_tts_diagnostic_message("Произошла ошибка. Попробуйте ещё раз.")

    def test_build_failure_report_from_timeout(self):
        report = build_failure_report(
            TimeoutError("timed out"),
            segment_id="sid1",
            segment_index=0,
            current=1,
            total=3,
            original_text="src",
            tts_text="tgt",
            voice="uk-UA-OstapNeural",
            language="uk",
            tts_file_path="/x/a.mp3",
            duration_ms=500.0,
        )
        assert report.error_code == "TTS_TIMEOUT"

    def test_mark_segment_failed(self):
        seg = {"segment_id": "x", "file": "a.mp3", "text": "t"}
        report = _sample_report()
        mark_segment_tts_failed(seg, report)
        assert seg["tts_status"] == "failed"
        assert seg["file"] is None
        assert seg["container_status"] == "red"
        assert seg["tts_error"]["segment_id"] == report.segment_id

    def test_save_failure_report_json(self, tmp_path: Path):
        report = _sample_report()
        path = save_failure_report(report, session_dir=tmp_path)
        assert path is not None and path.is_file()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["segment_id"] == report.segment_id
        assert "ui_message_ru" in data


class TestVoiceGenerationError:
    def test_carries_report(self):
        report = _sample_report()
        err = VoiceGenerationError("fail", report=report)
        assert err.report is report


class TestLogging:
    def test_log_tts_failure_includes_all_fields(self, caplog):
        import logging

        caplog.set_level(logging.ERROR, logger="tubedub.tts_failure")
        report = _sample_report()
        log_tts_failure(report)
        text = caplog.text
        assert "abc123def456" in text
        assert "VoiceGenerationError" in text
        assert "Traceback" in text or "traceback" in text.lower()


class TestErrorReportZip:
    def test_build_error_report_includes_pipeline_state(self, tmp_path, monkeypatch):
        import zipfile

        from engines.beta_support import build_error_report
        from engines.dub_task_state import init_auto_task

        app_dir = tmp_path
        (app_dir / "output" / "reports").mkdir(parents=True)
        task_id = "zip-test-task"
        init_auto_task(
            task_id,
            {
                "status": "running",
                "info": {
                    "session_dir": str(app_dir / "sessions" / task_id),
                    "tts_failures": [{"segment_id": "s1", "traceback": "TB", "diagnostic_block": "DIAG"}],
                    "voice": "ru-RU-DmitryNeural",
                },
            },
        )
        result = build_error_report(
            app_dir,
            task_id=task_id,
            error_message="VoiceGenerationError test",
            diagnostic="DIAG block",
        )
        assert result["ok"]
        zpath = Path(result["path"])
        with zipfile.ZipFile(zpath) as zf:
            names = zf.namelist()
            assert "pipeline_state.json" in names
            assert "stacktrace.txt" in names
            assert "engine_info.json" in names
            assert "config.json" in names
            assert "voice_engine.json" in names
            assert "tts_failures.json" in names


class TestRecordFailureIntegration:
    def test_record_marks_segment_and_stores_in_task(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
        from engines.dub_task_state import AUTO_TASKS, STATE_LOCK, init_auto_task
        from api.auto_dub_api import _record_tts_segment_failure

        task_id = "ttsfail001"
        init_auto_task(task_id, {"status": "running", "info": {"session_dir": str(tmp_path)}})
        segments = [{"segment_id": "seg1", "index": 0, "text": "hi", "file": "a.mp3"}]
        report = _sample_report(segment_id="seg1", segment_index=0, current=1, total=1)

        with patch("api.auto_dub_api._snapshot_project_on_tts_failure"):
            with patch("api.studio_api._save_session"):
                with patch("engines.dubbing_engine.pipeline_failure_diag.build_project_session_snapshot", return_value={}):
                    msg = _record_tts_segment_failure(task_id, segments, [0], report, ui_lang="ru")

        assert "seg1" in msg or "DubbingError" in msg
        with STATE_LOCK:
            task = AUTO_TASKS[task_id]
            assert len(task["info"]["tts_failures"]) == 1
            assert segments[0]["tts_status"] == "failed"
            assert task["status"] == "error"
            assert task["info"]["pipeline_error"]["stage"] == "TTS"
