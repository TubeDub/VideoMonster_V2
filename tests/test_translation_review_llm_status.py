"""Tests for translation review LLM advisory (weak/slow model surfacing)."""

from __future__ import annotations

from engines.translation_review import build_llm_status, build_translation_review


def _degraded_info():
    return {
        "target_lang": "uk",
        "source_lang": "en",
        "source_segments": ["George drove home.", "He survived."],
        "adaptation_capabilities": {
            "provider": "ollama",
            "llm_model": "qwen2.5:3b",
            "llm_model_adequate": False,
            "llm_model_warning": "Модель ~3B слишком мала для украинского.",
        },
        "llm_diagnostics": {
            "call_count": 67,
            "avg_call_ms": 75218.6,
            "models": ["qwen2.5:3b"],
            "providers": ["ollama"],
            "skip_reasons": {"llm_circuit_open": 18},
        },
        "llm_effectiveness": {
            "llm_not_called_segments": 18,
            "llm_calls_total": 67,
        },
        "segments_data": [
            {
                "index": 0,
                "text": "Джордж поїхав.",
                "validation_warnings": [
                    {"code": "preserved_token", "tokens": ["Fiat"], "stage": "final"}
                ],
            },
            {"index": 1, "text": "Він вижив."},
        ],
    }


def test_llm_status_flags_degraded_and_slow():
    status = build_llm_status(_degraded_info())
    assert status["degraded"] is True
    assert status["model"] == "qwen2.5:3b"
    assert status["model_adequate"] is False
    assert status["circuit_tripped"] is True
    assert status["model_too_slow"] is True
    assert status["segments_without_adaptation"] == 18
    assert status["recommend_cloud"] is True
    assert "OPENAI_API_KEY" in status["recommendation"]


def test_llm_status_entity_risk_aggregated():
    status = build_llm_status(_degraded_info())
    assert status["entity_risk_count"] == 1
    assert status["entity_risk_segments"][0]["names"] == ["Fiat"]


def test_llm_status_healthy_not_degraded():
    info = {
        "adaptation_capabilities": {
            "provider": "openai",
            "llm_model": "gpt-4o-mini",
            "llm_model_adequate": True,
        },
        "llm_diagnostics": {"call_count": 20, "avg_call_ms": 1800.0},
        "llm_effectiveness": {"llm_not_called_segments": 0},
        "segments_data": [],
    }
    status = build_llm_status(info)
    assert status["degraded"] is False
    assert status["recommend_cloud"] is False


def test_review_includes_llm_status():
    review = build_translation_review(_degraded_info())
    assert "llm_status" in review
    assert review["llm_status"]["degraded"] is True


def test_detect_capabilities_recommend_cloud_when_weak(monkeypatch):
    import engines.llm_adaptation_mode as m

    monkeypatch.setattr(m, "_cloud_api_key", lambda: "")
    monkeypatch.setattr(
        m,
        "resolve_llm_endpoint",
        lambda: {"available": True, "provider": "ollama", "models": ["qwen2.5:3b"], "source": "discovered", "base_url": "x"},
    )
    monkeypatch.setattr(m, "resolve_llm_model", lambda *a, **k: "qwen2.5:3b")
    monkeypatch.setattr(
        m, "assess_adaptation_model", lambda model: {"param_b": 3.0, "adequate": False, "warning": "too small"}
    )
    caps = m.detect_capabilities()
    assert caps["recommend_cloud"] is True
    assert caps["model_adequate"] is False
    assert "OPENAI_API_KEY" in caps["cloud_hint"]
