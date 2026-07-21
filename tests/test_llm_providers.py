"""LLM provider registry — model family selection and fallback."""

from __future__ import annotations

import pytest


@pytest.fixture
def app_dir(tmp_path):
    (tmp_path / "data").mkdir()
    return tmp_path


def test_resolve_model_prefers_largest_7b_in_quality_mode(app_dir, monkeypatch):
    from engines.ai_manager.config import save_config, default_config
    from engines.llm_providers.registry import resolve_model

    monkeypatch.delenv("VM_TRANSLATE_MODEL", raising=False)
    cfg = default_config()
    cfg["quality_mode"] = "max_quality"
    cfg["model"] = "qwen2.5:3b"
    save_config(app_dir, cfg)
    model = resolve_model(["llama3.1:8b", "qwen2.5:3b"], app_dir=app_dir)
    assert model == "llama3.1:8b"


def test_resolve_model_prefers_deepseek_when_installed(app_dir, monkeypatch):
    from engines.llm_providers.registry import resolve_model

    monkeypatch.delenv("VM_TRANSLATE_MODEL", raising=False)
    model = resolve_model(
        ["llama3.1:8b", "deepseek-r1:7b", "qwen2.5:3b"],
        app_dir=app_dir,
    )
    assert model == "deepseek-r1:7b"


def test_resolve_model_falls_back_to_qwen(app_dir, monkeypatch):
    from engines.llm_providers.registry import resolve_model

    monkeypatch.delenv("VM_TRANSLATE_MODEL", raising=False)
    model = resolve_model(["qwen2.5:3b"], app_dir=app_dir)
    assert model == "qwen2.5:3b"


def test_resolve_model_honors_env_override_when_installed(app_dir, monkeypatch):
    from engines.llm_providers.registry import resolve_model

    monkeypatch.setenv("VM_TRANSLATE_MODEL", "llama3.1:8b")
    model = resolve_model(["llama3.1:8b", "qwen2.5:7b"], app_dir=app_dir)
    assert model == "llama3.1:8b"


def test_resolve_model_rejects_unverified_env_without_tags(app_dir, monkeypatch):
    from engines.llm_providers.registry import resolve_model

    monkeypatch.setenv("VM_TRANSLATE_MODEL", "llama3.1:8b")
    model = resolve_model([], provider="ollama", app_dir=app_dir)
    assert model == ""


def test_resolve_model_uses_persisted_provider(app_dir, monkeypatch):
    from engines.ai_manager.config import save_config, default_config
    from engines.llm_providers.registry import resolve_model

    monkeypatch.delenv("VM_TRANSLATE_MODEL", raising=False)
    cfg = default_config()
    cfg["selected_provider"] = "llama"
    cfg["model"] = "llama3.1:8b"
    save_config(app_dir, cfg)

    model = resolve_model(["llama3.1:8b", "qwen2.5:3b"], app_dir=app_dir)
    assert model == "llama3.1:8b"


def test_list_providers_for_ui_marks_installed(app_dir):
    from engines.llm_providers.registry import list_providers_for_ui

    rows = list_providers_for_ui(["qwen2.5:3b"])
    by_id = {r["id"]: r for r in rows}
    assert by_id["deepseek"]["installed"] is False
    assert by_id["qwen"]["installed"] is True
    assert by_id["deepseek"]["is_default"] is True


def test_resolve_llm_model_delegates_to_registry(monkeypatch, tmp_path):
    from engines.ai_manager.config import save_config, default_config
    from engines.llm_adaptation_mode import resolve_llm_model

    monkeypatch.delenv("VM_TRANSLATE_MODEL", raising=False)
    cfg = default_config()
    cfg["quality_mode"] = "max_quality"
    cfg["selected_provider"] = "deepseek"
    save_config(tmp_path, cfg)
    monkeypatch.setattr("engines.llm_providers.registry._app_dir", lambda: tmp_path)
    model = resolve_llm_model(["deepseek-r1:7b", "qwen2.5:3b"])
    assert model == "deepseek-r1:7b"


def test_save_persisted_selection(app_dir):
    from engines.ai_manager.config import load_config
    from engines.llm_providers.registry import load_persisted_selection, save_persisted_selection

    save_persisted_selection(provider_id="qwen", model="qwen2.5:3b", app_dir=app_dir)
    sel = load_persisted_selection(app_dir)
    assert sel["provider"] == "qwen"
    assert sel["model"] == "qwen2.5:3b"
    cfg = load_config(app_dir)
    assert cfg["selected_provider"] == "qwen"
