"""Tests for Pipeline Failure Diagnostics v1.0 (full pipeline)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from engines.dubbing_engine.pipeline_failure_diag import (
    PipelineFailureReport,
    RuntimeDiagnosticsRecorder,
    STAGE_STT,
    STAGE_TTS,
    build_failure_report,
    fail_pipeline,
    format_detail_block,
    format_ui_line,
    from_tts_failure_report,
    is_pipeline_diagnostic_message,
)
from engines.dubbing_engine.tts_failure_diag import TTSFailureReport, build_failure_report as build_tts_report


class TestPipelineFailureReport:
    def test_build_failure_report_has_required_fields(self):
        report = build_failure_report(
            "Speech-to-text returned empty string.",
            stage=STAGE_STT,
            task_id="t1",
            error_code="STT_EMPTY",
        )
        assert report.stage == STAGE_STT
        assert report.error_code == "STT_EMPTY"
        assert report.pipeline_state == "STOPPED"
        assert report.reason_short

    def test_format_detail_block_tts_example(self):
        report = build_failure_report(
            "Engine returned empty audio.",
            stage=STAGE_TTS,
            error_type="VoiceGenerationError",
            error_code="TTS_EMPTY_AUDIO",
            segment_id="184",
            segment_number="5/20",
            voice_engine="XTTS",
            voice="uk_female_01",
            tts_text="Добрий вечір.",
            tts_file_path="temp/tts/184.wav",
        )
        block = format_detail_block(report)
        assert "Stage: TTS" in block
        assert "segment_id: 184" in block
        assert "engine: XTTS" in block
        assert "VoiceGenerationError" in block
        assert "Добрий вечір." in block
        assert "Pipeline:\nSTOPPED" in block

    def test_from_tts_failure_report(self):
        tts = build_tts_report(
            RuntimeError("empty audio"),
            segment_id="s1",
            segment_index=0,
            current=1,
            total=2,
            original_text="hi",
            tts_text="привет",
            voice="ru-RU-DmitryNeural",
            language="ru",
            tts_file_path="/x/a.mp3",
            duration_ms=100.0,
            task_id="task-x",
        )
        pipe = from_tts_failure_report(tts)
        assert pipe.stage == STAGE_TTS
        assert pipe.error_type == "VoiceGenerationError"
        assert pipe.segment_id == "s1"
        assert pipe.pipeline_state == "STOPPED"

    def test_user_summary_structure(self):
        report = build_failure_report(
            "fail",
            stage=STAGE_TTS,
            error_type="VoiceGenerationError",
            error_code="TTS_FAILED",
            segment_id="99",
        )
        report.reason_short = "Не удалось сгенерировать аудио."
        summary = report.user_summary()
        assert summary["title"] == "Ошибка дубляжа"
        assert summary["stage"] == STAGE_TTS
        assert summary["error_type"] == "VoiceGenerationError"
        assert summary["reason_short"] == "Не удалось сгенерировать аудио."
        assert "detail_block" in summary

    def test_is_pipeline_diagnostic_message(self):
        line = format_ui_line(
            build_failure_report("x", stage=STAGE_STT, error_code="STT_EMPTY")
        )
        assert is_pipeline_diagnostic_message(line)
        assert not is_pipeline_diagnostic_message("Произошла ошибка. Попробуйте ещё раз.")


class TestFailPipeline:
    def test_fail_pipeline_sets_error_status(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "output"))
        from engines.dub_task_state import AUTO_TASKS, STATE_LOCK, init_auto_task

        task_id = "fail-fast-1"
        init_auto_task(
            task_id,
            {
                "status": "running",
                "step": "transcribe",
                "info": {"session_dir": str(tmp_path / "sess")},
            },
        )
        with patch("engines.dubbing_engine.pipeline_failure_diag.build_project_session_snapshot", return_value={}):
            with patch("api.studio_api._save_session"):
                fail_pipeline(
                    task_id,
                    "Speech-to-text returned empty string.",
                    stage=STAGE_STT,
                    error_code="STT_EMPTY",
                )
        with STATE_LOCK:
            task = AUTO_TASKS[task_id]
            assert task["status"] == "error"
            assert task["info"]["pipeline_error"]["stage"] == STAGE_STT
            assert task["info"]["pipeline_error"]["error_code"] == "STT_EMPTY"


class TestRuntimeDiagnostics:
    def test_runtime_recorder_persists(self, tmp_path):
        from engines.dub_task_state import AUTO_TASKS, STATE_LOCK, init_auto_task

        task_id = "runtime-diag-1"
        session = tmp_path / "sess"
        init_auto_task(task_id, {"status": "running", "info": {"session_dir": str(session)}})
        rec = RuntimeDiagnosticsRecorder(task_id)
        rec.stage_begin(STAGE_STT)
        record = rec.stage_complete(2, STAGE_STT, segments_total=10, segments_ok=10)
        assert record["stage"] == STAGE_STT
        assert record["duration_ms"] >= 0
        with STATE_LOCK:
            stored = AUTO_TASKS[task_id]["info"]["runtime_diagnostics"]
        assert len(stored) == 1
        path = session / "runtime_diagnostics.json"
        assert path.is_file()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data[0]["stage"] == STAGE_STT


class TestErrorReportZipEnhanced:
    def test_zip_includes_engine_info_and_runtime(self, tmp_path, monkeypatch):
        from engines.beta_support import build_error_report
        from engines.dub_task_state import init_auto_task

        app_dir = tmp_path
        (app_dir / "output" / "reports").mkdir(parents=True)
        (app_dir / "output" / "logs").mkdir(parents=True)
        (app_dir / "output" / "logs" / "tubedub.log").write_text("test log", encoding="utf-8")
        task_id = "zip-pipeline-diag"
        init_auto_task(
            task_id,
            {
                "status": "error",
                "info": {
                    "session_dir": str(app_dir / "sessions" / task_id),
                    "pipeline_failures": [{"segment_id": "s1", "traceback": "TB", "diagnostic_block": "DIAG"}],
                    "runtime_diagnostics": [{"stage": "STT", "duration_ms": 1}],
                    "voice": "ru-RU-DmitryNeural",
                },
            },
        )
        result = build_error_report(app_dir, task_id=task_id, error_message="DubbingError test")
        zpath = Path(result["path"])
        with zipfile.ZipFile(zpath) as zf:
            names = zf.namelist()
            assert "engine_info.json" in names
            assert "config.json" in names
            assert "runtime_diagnostics.json" in names
            assert "pipeline_failures.json" in names
            assert "logs/" in names or any(n.startswith("logs/") for n in names)
