"""Regression tests for LLM callable pipeline (model_missing fix)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from engines.llm_callable import (
    _model_listed,
    ensure_llm_callable,
    remediate_missing_model,
    reset_run_state,
)
from engines.llm_providers.registry import resolve_model


def test_model_listed_matches_tag_prefix():
    assert _model_listed("llama3.1:8b", ["llama3.1:8b", "qwen2.5:7b"])
    assert _model_listed("llama3.1", ["llama3.1:8b"])
    assert not _model_listed("llama3.1:8b", ["qwen2.5:7b"])


def test_resolve_model_never_returns_uninstalled_when_tags_exist():
    available = ["qwen2.5:7b", "gemma2:9b"]
    picked = resolve_model(available, provider="ollama")
    assert picked in available


def test_resolve_model_returns_empty_without_installed_tags(monkeypatch):
    monkeypatch.delenv("VM_TRANSLATE_MODEL", raising=False)
    assert resolve_model([], provider="ollama") == ""


def test_remediate_missing_model_remaps_to_installed():
    with patch("engines.llm_callable.refresh_endpoint_models") as refresh:
        refresh.return_value = {
            "available": True,
            "provider": "ollama",
            "base_url": "http://127.0.0.1:11434/v1",
            "models": ["qwen2.5:7b"],
        }
        model, remediation, tags = remediate_missing_model(
            "llama3.1:8b",
            available=["qwen2.5:7b"],
        )
    assert model == "qwen2.5:7b"
    assert remediation == "remapped"
    assert tags == ["qwen2.5:7b"]


def test_ensure_llm_callable_marks_callable_when_model_listed():
    reset_run_state()
    health = {"callable": True, "model_listed": True, "failure_phase": "responding"}
    with patch("engines.llm_callable.refresh_endpoint_models") as refresh, patch(
        "engines.llm_callable.probe_model_callable", return_value=health
    ), patch("engines.llm_adaptation_mode.resolve_llm_model", return_value="qwen2.5:7b"):
        refresh.return_value = {
            "available": True,
            "provider": "ollama",
            "base_url": "http://127.0.0.1:11434/v1",
            "models": ["qwen2.5:7b"],
        }
        status = ensure_llm_callable(max_attempts=1)
    assert status["callable"] is True
    assert status["model"] == "qwen2.5:7b"
    assert status["llm_available"] is True


def test_ensure_llm_callable_fatal_when_no_models():
    reset_run_state()
    with patch("engines.llm_callable.refresh_endpoint_models") as refresh, patch(
        "engines.llm_callable.remediate_missing_model", return_value=("", "fatal", [])
    ), patch("engines.llm_callable._try_pull_model", return_value=False), patch(
        "engines.llm_callable._fetch_models_for_endpoint", return_value=[]
    ), patch("engines.llm_adaptation_mode.resolve_llm_model", return_value="llama3.1:8b"):
        refresh.return_value = {
            "available": True,
            "provider": "ollama",
            "base_url": "http://127.0.0.1:11434/v1",
            "models": [],
        }
        status = ensure_llm_callable(max_attempts=1)
    assert status["callable"] is False
    assert status["fatal_reason"]


def test_adapt_segment_calls_ensure_before_llm(monkeypatch):
    from engines.ai_adaptation_engine import adapt_segment_ai

    ensure_mock = MagicMock(
        return_value={
            "callable": True,
            "provider": "ollama",
            "model": "qwen2.5:7b",
        }
    )
    monkeypatch.setattr("engines.llm_callable.ensure_llm_callable", ensure_mock)
    monkeypatch.setattr(
        "engines.translation_adapt.llm_rephrase_available",
        lambda: True,
    )
    monkeypatch.setattr(
        "engines.ai_adaptation_engine._generate_round_variants",
        lambda *a, **k: [],
    )
    monkeypatch.setattr("engines.translation_adapt.get_llm_calls", lambda: [])
    monkeypatch.setattr("engines.translation_adapt.get_llm_status", lambda: [])

    long_text = " ".join(["слово"] * 80)
    result = adapt_segment_ai(
        long_text,
        source_hint="many words here",
        slot_ms=1200,
        tgt_lang="uk",
        index=3,
        max_rounds=1,
        min_rounds=1,
    )
    ensure_mock.assert_called()
    assert result.trace.provider == "ollama" or result.trace.model == "qwen2.5:7b"


def test_openddf_provider_fatal_not_silent_model_missing():
    from engines.segment_timing_qa import build_openddf_full_report

    info = {
        "source_segments": ["Hello"],
        "target_lang": "uk",
        "segments_data": [
            {
                "index": 0,
                "requires_llm_adaptation": True,
                "llm_called": False,
                "ai_adaptation_trace": {
                    "provider_fatal": True,
                    "llm_skip_reason": "model_missing",
                },
            }
        ],
        "translation_audits": [{"index": 0, "raw_translation": "Привіт"}],
        "llm_status": [{"segment": 0, "needed": True, "called": False, "skip_reason": "model_missing"}],
        "llm_calls": [],
        "llm_callable": False,
        "llm_provider_status": {"fatal_reason": "model_missing"},
    }
    report = build_openddf_full_report(info)
    seg = report["segments"][0]
    codes = [e["code"] for e in seg.get("errors") or []]
    assert "LLM_PROVIDER_FATAL" in codes
    assert "LLM_NOT_CALLED" not in codes


def test_llm_adaptation_report_counts_rewrites():
    from engines.llm_adaptation_report import build_llm_adaptation_report

    info = {
        "llm_callable": True,
        "llm_provider_status": {"provider": "ollama", "model": "qwen2.5:7b"},
        "segments_data": [
            {
                "index": 0,
                "requires_llm_adaptation": True,
                "llm_called": True,
                "ai_adaptation_trace": {
                    "provider": "ollama",
                    "model": "qwen2.5:7b",
                    "iterations": 2,
                    "original_duration_ms": 5000,
                    "rewritten_duration_ms": 4200,
                    "duration_delta_ms": -800,
                    "rewrite_reason": "duration_overflow",
                },
            },
            {
                "index": 1,
                "requires_llm_adaptation": True,
                "llm_called": False,
                "provider_fatal": True,
            },
        ],
        "llm_calls": [{"segment": 0, "usable": True}],
    }
    rep = build_llm_adaptation_report(info)
    assert rep["segments_requiring_llm"] == 2
    assert rep["segments_rewritten"] == 1
    assert rep["segments_failed"] == 1
    assert rep["provider"] == "ollama"
    assert rep["avg_iterations"] == 2.0


def test_bootstrap_invokes_ensure_callable():
    from engines.ai_core.llm_bootstrap import prepare_llm_for_pipeline

    with patch("engines.llm_callable.ensure_llm_callable") as ensure, patch(
        "engines.ai_core.llm_gateway.begin_run"
    ), patch("engines.ai_core.llm_gateway.is_available", return_value=True):
        ensure.return_value = {
            "callable": True,
            "llm_available": True,
            "provider": "ollama",
            "model": "qwen2.5:7b",
            "base_url": "http://127.0.0.1:11434/v1",
            "installed_models": ["qwen2.5:7b"],
            "remediation": "installed",
            "attempts": 1,
            "fatal_reason": "",
            "health": {},
        }
        status = prepare_llm_for_pipeline("task-1", {}, phase="ADAPTATION")
    ensure.assert_called_once()
    assert status["callable"] is True
    assert status["model"] == "qwen2.5:7b"
