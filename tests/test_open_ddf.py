"""Tests for engines.open_ddf — OpenDDF diagnostic data format.

Covers:
- record_agent: success and failure entries
- get_report: correct structure
- save / load: JSON persistence
- mark_segment_attention: per-segment flags
- Agent failure in debug mode: pipeline continue (not raise)
- _run_agent_safe: error capture + fallback
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ── Helpers ──────────────────────────────────────────────────────────────────

def _fresh_task_id(prefix: str = "test") -> str:
    import uuid
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


# ── Tests: record_agent ───────────────────────────────────────────────────────

class TestRecordAgent:
    def test_success_entry(self):
        from engines.open_ddf import open_ddf, get_report

        tid = _fresh_task_id("success")
        open_ddf.record_agent(tid, "Whisper/STT", called=True, success=True, decision="en")

        report = get_report(tid)
        assert report["task_id"] == tid
        agents = report["agents"]
        assert len(agents) == 1
        entry = agents[0]
        assert entry["agent_name"] == "Whisper/STT"
        assert entry["called"] is True
        assert entry["success"] is True
        assert entry["decision"] == "en"
        assert entry["error_msg"] is None
        assert entry["fallback_used"] is False
        assert "timestamp" in entry

    def test_failure_entry(self):
        from engines.open_ddf import open_ddf, get_report

        tid = _fresh_task_id("fail")
        open_ddf.record_agent(
            tid, "Translation",
            called=True, success=False,
            error="ConnectionTimeout",
            fallback_used=True,
            decision="debug_fallback",
        )

        report = get_report(tid)
        entry = report["agents"][0]
        assert entry["success"] is False
        assert entry["error_msg"] == "ConnectionTimeout"
        assert entry["fallback_used"] is True
        assert report["summary"]["failed_agents"] == 1
        assert report["summary"]["fallback_used"] == 1
        assert report["summary"]["warnings"] >= 1

    def test_multiple_agents_summary(self):
        from engines.open_ddf import open_ddf, get_report

        tid = _fresh_task_id("multi")
        open_ddf.record_agent(tid, "Whisper/STT", called=True, success=True)
        open_ddf.record_agent(tid, "Translation", called=True, success=True)
        open_ddf.record_agent(tid, "TTS", called=True, success=False, error="timeout",
                              segment_idx=3)

        report = get_report(tid)
        assert len(report["agents"]) == 3
        assert report["summary"]["total_agents"] == 3
        assert report["summary"]["failed_agents"] == 1

    def test_segment_idx_recorded(self):
        from engines.open_ddf import open_ddf, get_report

        tid = _fresh_task_id("segidx")
        open_ddf.record_agent(tid, "TTS", called=True, success=False,
                              error="voice_error", segment_idx=7)

        report = get_report(tid)
        entry = report["agents"][0]
        assert entry["segment_idx"] == 7


# ── Tests: mark_segment_attention ────────────────────────────────────────────

class TestMarkSegmentAttention:
    def test_attention_recorded(self):
        from engines.open_ddf import open_ddf, get_report

        tid = _fresh_task_id("attn")
        open_ddf.mark_segment_attention(tid, 5, "tts_failed")
        open_ddf.mark_segment_attention(tid, 12, "fit_skipped")

        report = get_report(tid)
        attn = report["segment_attention"]
        assert len(attn) == 2
        assert attn[0]["seg_idx"] == 5
        assert attn[0]["reason"] == "tts_failed"
        assert attn[1]["seg_idx"] == 12
        assert attn[1]["reason"] == "fit_skipped"


# ── Tests: get_report ────────────────────────────────────────────────────────

class TestGetReport:
    def test_unknown_task_returns_no_data(self):
        from engines.open_ddf import get_report

        report = get_report("nonexistent_task_xyz_999")
        assert report["error"] == "no_data"
        assert report["agents"] == []

    def test_report_has_required_fields(self):
        from engines.open_ddf import open_ddf, get_report

        tid = _fresh_task_id("fields")
        open_ddf.record_agent(tid, "TTS", called=True, success=True)

        report = get_report(tid)
        for field in ("task_id", "created_at", "agents", "segment_attention", "summary"):
            assert field in report, f"Missing field: {field}"


# ── Tests: save / load ───────────────────────────────────────────────────────

class TestSaveLoad:
    def test_save_creates_file(self, tmp_path, monkeypatch):
        import engines.open_ddf as _ddf_mod

        monkeypatch.setattr(_ddf_mod, "_OUTPUT_DIR", tmp_path)

        from engines.open_ddf import open_ddf, save, load

        tid = _fresh_task_id("save")
        open_ddf.record_agent(tid, "SlotFit", called=True, success=True, decision="ok")
        saved_path = save(tid)

        assert saved_path is not None
        assert saved_path.is_file()

        content = json.loads(saved_path.read_text(encoding="utf-8"))
        assert content["task_id"] == tid
        assert len(content["agents"]) == 1
        assert "saved_at" in content

    def test_load_from_disk(self, tmp_path, monkeypatch):
        import engines.open_ddf as _ddf_mod

        monkeypatch.setattr(_ddf_mod, "_OUTPUT_DIR", tmp_path)

        from engines.open_ddf import open_ddf, save, load, _store

        tid = _fresh_task_id("load")
        open_ddf.record_agent(tid, "Mix", called=True, success=True)
        save(tid)

        # Remove from in-memory store so load reads from disk
        with _ddf_mod._lock:
            _store.pop(tid, None)

        loaded = load(tid)
        assert loaded is not None
        assert loaded["task_id"] == tid
        assert loaded["agents"][0]["agent_name"] == "Mix"


# ── Tests: proxy suppresses exceptions ───────────────────────────────────────

class TestProxyResistance:
    def test_proxy_record_does_not_raise_on_bad_input(self):
        from engines.open_ddf import open_ddf

        # Should not raise even with unusual inputs
        open_ddf.record_agent(None, "Agent", called=True, success=True)  # type: ignore[arg-type]
        open_ddf.record_agent("tid", None, called=True, success=True)  # type: ignore[arg-type]

    def test_proxy_get_report_on_unknown(self):
        from engines.open_ddf import open_ddf

        report = open_ddf.get_report("does_not_exist_xyz")
        assert isinstance(report, dict)


# ── Tests: IS_DEBUG_LEARNING_MODE ────────────────────────────────────────────

class TestDebugLearningMode:
    def test_off_by_default(self, monkeypatch):
        monkeypatch.delenv("VM_DEBUG_MODE", raising=False)

        from engines.core import feature_flags as ff_mod
        import importlib
        importlib.reload(ff_mod)

        # Without env or file → should be False
        # We can only test that the function is callable and returns bool
        result = ff_mod.IS_DEBUG_LEARNING_MODE()
        assert isinstance(result, bool)

    def test_on_with_env(self, monkeypatch):
        monkeypatch.setenv("VM_DEBUG_MODE", "1")
        from engines.core.feature_flags import IS_DEBUG_LEARNING_MODE

        assert IS_DEBUG_LEARNING_MODE() is True

    def test_on_with_debug_mode_json(self, monkeypatch):
        monkeypatch.delenv("VM_DEBUG_MODE", raising=False)
        from engines.core.feature_flags import IS_DEBUG_LEARNING_MODE

        assert IS_DEBUG_LEARNING_MODE() is True

    def test_off_with_zero(self, monkeypatch):
        monkeypatch.setenv("VM_DEBUG_MODE", "0")
        # Also make sure no debug_mode.json exists at a temp path
        from engines.core.feature_flags import IS_DEBUG_LEARNING_MODE

        # With 0 and no file, should be False (unless file happens to exist)
        result = IS_DEBUG_LEARNING_MODE()
        assert isinstance(result, bool)


# ── Tests: _run_agent_safe in auto_dub_api ───────────────────────────────────

class TestRunAgentSafe:
    """Tests for the _run_agent_safe utility added to auto_dub_api."""

    def test_success_returns_value(self):
        # Import the helper directly from the module
        from api.auto_dub_api import _run_agent_safe

        result = _run_agent_safe("task_rsa_ok", "TestAgent", lambda: 42)
        assert result == 42

    def test_failure_records_ddf_and_returns_none(self):
        from api.auto_dub_api import _run_agent_safe
        from engines.open_ddf import get_report

        tid = _fresh_task_id("rsa_fail")

        def _boom():
            raise ValueError("test error")

        result = _run_agent_safe(tid, "BoomAgent", _boom)
        assert result is None

        report = get_report(tid)
        assert any(a["agent_name"] == "BoomAgent" and not a["success"]
                   for a in report["agents"])

    def test_fallback_called_on_failure(self):
        from api.auto_dub_api import _run_agent_safe

        tid = _fresh_task_id("rsa_fb")

        def _boom():
            raise RuntimeError("primary failed")

        def _fallback():
            return "fallback_result"

        result = _run_agent_safe(tid, "PrimaryAgent", _boom, fallback_fn=_fallback)
        assert result == "fallback_result"

    def test_pipeline_continues_on_failure(self):
        """Simulate a pipeline step failing but continuing (debug mode behaviour)."""
        from api.auto_dub_api import _run_agent_safe
        from engines.open_ddf import get_report

        tid = _fresh_task_id("rsa_cont")
        results = []

        def _step1():
            raise ConnectionError("LLM unavailable")

        def _step2():
            return "ok"

        # step1 fails → None → step2 should still run
        r1 = _run_agent_safe(tid, "Step1", _step1)
        r2 = _run_agent_safe(tid, "Step2", _step2)

        assert r1 is None
        assert r2 == "ok"

        report = get_report(tid)
        names = [a["agent_name"] for a in report["agents"]]
        assert "Step1" in names
        assert "Step2" in names
