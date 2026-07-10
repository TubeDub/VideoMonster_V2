"""Tests for LLM Retry Manager."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_retry_config_defaults_cpu():
    from engines.llm_retry_manager import RetryConfig

    cfg = RetryConfig.from_env(cpu_only=True)
    assert cfg.max_retries >= 1
    assert cfg.call_timeout_sec <= 120.0
    assert cfg.retry_delay_sec >= 0


def test_classify_generation_timeout():
    from engines.llm_retry_manager import classify_call_failure

    import socket

    code, phase = classify_call_failure(socket.timeout(), model_loaded_before=True)
    assert code == "timeout"
    assert phase == "generation_timeout"


def test_classify_cold_start_timeout():
    from engines.llm_retry_manager import classify_call_failure

    import socket

    code, phase = classify_call_failure(
        socket.timeout(),
        model_loaded_before=False,
        ollama_health={"model_listed": True},
    )
    assert phase == "model_cold"


def test_build_fallback_chain():
    from engines.llm_retry_manager import build_fallback_chain

    chain = build_fallback_chain(
        "qwen2.5:3b",
        ["qwen2.5:3b", "deepseek-r1:7b", "gemma2:2b"],
    )
    assert chain[0] == "qwen2.5:3b"
    assert "deepseek-r1:7b" in chain


def test_run_with_retry_succeeds_on_second_attempt():
    from engines.llm_retry_manager import run_with_retry

    calls = {"n": 0}

    def fake_once(prompt, **kw):
        calls["n"] += 1
        if calls["n"] < 2:
            return None, TimeoutError("timeout"), {"model": "m", "provider": "ollama"}
        return "ok", None, {"model": "m", "provider": "ollama"}

    with patch("engines.llm_retry_manager.RetryConfig.from_env") as mock_cfg:
        from engines.llm_retry_manager import RetryConfig

        mock_cfg.return_value = RetryConfig(
            max_retries=3,
            retry_delay_sec=0,
            call_timeout_sec=10,
            health_check=False,
            fallback_enabled=False,
        )
        with patch("engines.translation_adapt._llm_model", return_value="qwen2.5:3b"):
            with patch("engines.translation_adapt._resolve_endpoint", return_value={"provider": "ollama", "models": []}):
                out = run_with_retry(
                    fake_once,
                    prompt="hi",
                    system=None,
                    max_tokens=32,
                    temperature=0.2,
                    count_budget=True,
                )
    assert out.ok
    assert out.text == "ok"
    assert out.attempts == 2
