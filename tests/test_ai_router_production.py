"""Tests — Production Ready AI Router / Sources / Quality Score v2."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def tmp_app(tmp_path, monkeypatch):
    from core.ai_router import reset_ai_router
    from core.ai_sources import reset_ai_sources

    reset_ai_sources()
    reset_ai_router()
    monkeypatch.delenv("VM_AI_SOURCE_MODE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("VM_LLM_API_KEY", raising=False)
    yield tmp_path
    reset_ai_sources()
    reset_ai_router()


def test_default_source_is_local_and_free(tmp_app):
    from core.ai_sources import AISourceMode, get_ai_sources

    store = get_ai_sources(tmp_app)
    cfg = store.get()
    assert cfg.source_mode == AISourceMode.LOCAL.value
    d = cfg.to_dict()
    assert d["policy"]["local_always_free"] is True
    assert d["policy"]["no_auto_download"] is True
    assert d["policy"]["no_paywall"] is True


def test_recommend_model_by_vram():
    from core.ai_sources import recommend_local_model

    assert recommend_local_model(vram_gb=2, has_gpu=True)["model"] == "qwen2.5:3b"
    assert recommend_local_model(vram_gb=8, has_gpu=True)["model"] == "qwen2.5:7b"
    assert recommend_local_model(vram_gb=16, has_gpu=True)["model"] == "qwen2.5:14b"
    assert recommend_local_model(vram_gb=32, has_gpu=True)["model"] == "qwen2.5:32b"
    assert recommend_local_model(vram_gb=0, has_gpu=False)["auto_download"] is False


def test_router_falls_back_without_paywall(tmp_app, monkeypatch):
    from core.ai_router import get_ai_router

    monkeypatch.setattr(
        "core.ai_router.AIRouter.discover_local",
        lambda self: {"available": False, "provider": "", "base_url": "", "models": []},
    )
    d = get_ai_router(app_dir=str(tmp_app)).route()
    assert d.available is False
    assert d.free is True
    assert "Marian" in d.reason or "free" in d.reason.lower()
    assert "оплатите" not in d.reason.lower()
    assert "subscribe" not in d.reason.lower()
    assert "buy api" not in d.reason.lower()


def test_user_api_route(tmp_app):
    from core.ai_router import get_ai_router
    from core.ai_sources import get_ai_sources

    store = get_ai_sources(tmp_app)
    store.update(
        source_mode="user_api",
        user_api={
            "provider": "openai",
            "api_key": "sk-test-key",
            "model": "gpt-4o-mini",
            "base_url": "https://api.openai.com/v1",
        },
    )
    d = get_ai_router(app_dir=str(tmp_app)).route()
    assert d.available is True
    assert d.source == "user_api"
    assert d.model == "gpt-4o-mini"
    assert d.free is False


def test_models_dir_outside_app(tmp_app):
    from core.ai_sources import get_ai_sources

    store = get_ai_sources(tmp_app)
    external = "D:\\AI Models"
    store.update(local={"models_dir": external, "model": "qwen2.5:7b"})
    applied = store.apply_to_env()
    assert applied.get("OLLAMA_MODELS") == external
    assert not str(Path(external)).startswith(str(tmp_app))


def test_quality_score_v2_dimensions():
    from engines.quality_score_v2 import compute_quality_score_v2

    score, det = compute_quality_score_v2(
        "George Lucas created Star Wars in 1977.",
        "Джордж Лукас створив Star Wars у 1977 році.",
        src_lang="en",
        tgt_lang="uk",
    )
    assert score > 0
    dims = det["dimensions"]
    for key in (
        "semantic_similarity",
        "entity_preservation",
        "hallucination_detection",
        "naturalness",
        "grammar",
        "fluency",
        "event_preservation",
        "compression",
        "causal_links",
        "emotions",
    ):
        assert key in dims


def test_smart_segmentation_name_and_unit():
    from engines.smart_segmentation import would_break_forbidden, enforce_smart_boundaries

    bad, reason = would_break_forbidden("George", "Lucas drove home.")
    assert bad is True
    assert reason == "name_surname"
    bad2, reason2 = would_break_forbidden("He drove 18", "km home.")
    assert bad2 is True
    merged = enforce_smart_boundaries(["George", "Lucas drove home."])
    assert len(merged) == 1
    assert "George Lucas" in merged[0]


def test_semantic_retry_strategies():
    from core.semantic_retry import run_semantic_retry

    calls = {"n": 0}

    def fn(text, *, system="", model="", strict=False):
        calls["n"] += 1
        if calls["n"] < 2:
            return "плохой"  # short / poor
        return (
            "18-річний хлопець на ім'я Джордж молодший поїхав через рідне місто "
            "додому на вечерю."
        )

    result = run_semantic_retry(
        "An 18-year-old boy named George Jr. drove through his hometown on his way home for dinner.",
        tgt_lang="uk",
        translate_fn=fn,
        models=["a", "b"],
    )
    assert result.attempts
    assert calls["n"] >= 1


def test_providers_catalog_includes_github():
    from core.ai_router import list_supported_providers

    ids = {p["id"] for p in list_supported_providers()}
    assert {"ollama", "lmstudio", "vllm", "openai", "anthropic", "openrouter", "github"} <= ids


def test_first_run_continue_without_download(tmp_app):
    from core.ai_sources import get_ai_sources

    store = get_ai_sources(tmp_app)
    store.update(first_run_prompt_done=True, allow_mt_only=True, source_mode="local")
    assert store.get().local.auto_download is False
    assert store.get().first_run_prompt_done is True
