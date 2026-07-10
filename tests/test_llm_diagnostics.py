"""Tests for LLM stall diagnostics."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_format_model_display_deepseek():
    from engines.llm_diagnostics import format_model_display

    name = format_model_display("deepseek-r1:7b", provider="ollama")
    assert "deepseek" in name.lower() or "DeepSeek" in name


def test_build_llm_stall_message_ru():
    from engines.llm_diagnostics import build_llm_stall_message

    ctx = {
        "model_display": "Qwen 2.5 (qwen2.5:3b)",
        "provider_label": "Ollama",
        "segment": 17,
        "total_segments": 420,
        "chars_sent": 512,
        "wait_sec": 120.0,
        "attempts": 2,
        "timeout": True,
        "provider": "ollama",
        "ollama": {"status_code": "busy"},
    }
    msg = build_llm_stall_message(ctx, idle_sec=120.0, lang="ru")
    assert "Qwen" in msg
    assert "Ollama" in msg
    assert "17" in msg and "420" in msg
    assert "512" in msg
    assert "таймаут: да" in msg.lower() or "таймаут: да" in msg


def test_probe_ollama_unreachable():
    from engines.llm_diagnostics import probe_ollama_health

    with patch("engines.llm_diagnostics._port_open", return_value=False):
        out = probe_ollama_health("qwen2.5:3b", port=59999)
    assert out["status_code"] == "unreachable"


def test_recovery_retry_connection():
    from engines.llm_diagnostics import attempt_llm_recovery

    with patch("engines.llm_adaptation_mode.discover_local_llm", return_value={"base_url": "http://127.0.0.1:11434/v1"}):
        result = attempt_llm_recovery("t1", attempt_index=0)
    assert result["step"] == "retry_connection"
    assert result["ok"] is True
