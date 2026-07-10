"""Adaptation model quality floor — prefer a capable model, warn on weak ones.

Root cause from production logs: on CPU the resolver picked the SMALLEST model
(qwen2.5:3b), which is too weak for reliable Ukrainian rephrase and corrupted
output (良心). The resolver must prefer the smallest model that still meets the
quality floor, fall back to the largest available otherwise, and surface a
clear warning.
"""

from __future__ import annotations

import engines.llm_adaptation_mode as lam


def test_assess_weak_local_model_flags_warning():
    a = lam.assess_adaptation_model("qwen2.5:3b")
    assert a["adequate"] is False
    assert a["param_b"] == 3.0
    assert "qwen2.5:3b" in a["warning"]


def test_assess_strong_local_model_ok():
    for m in ("qwen2.5:14b", "gemma2:9b", "llama3.1:8b"):
        a = lam.assess_adaptation_model(m)
        assert a["adequate"] is True, m
        assert a["warning"] == ""


def test_assess_cloud_model_assumed_ok():
    a = lam.assess_adaptation_model("gpt-4o-mini")
    assert a["adequate"] is True
    assert a["warning"] == ""


def test_resolver_prefers_capable_model_on_cpu(monkeypatch):
    monkeypatch.delenv("VM_TRANSLATE_MODEL", raising=False)
    monkeypatch.setattr(lam, "_has_gpu", lambda: False)
    # Both a weak and a capable model installed → pick the capable one, not 3b.
    chosen = lam.resolve_llm_model(["qwen2.5:3b", "llama3.1:8b"], provider="ollama")
    assert chosen == "llama3.1:8b"


def test_resolver_falls_back_to_largest_when_all_weak(monkeypatch):
    monkeypatch.delenv("VM_TRANSLATE_MODEL", raising=False)
    monkeypatch.setattr(lam, "_has_gpu", lambda: False)
    # Only weak models → pick the largest (best achievable), not the smallest.
    chosen = lam.resolve_llm_model(["qwen2.5:1.5b", "qwen2.5:3b"], provider="ollama")
    assert chosen == "qwen2.5:3b"


def test_resolver_picks_smallest_qualifying(monkeypatch):
    monkeypatch.delenv("VM_TRANSLATE_MODEL", raising=False)
    monkeypatch.setattr(lam, "_has_gpu", lambda: False)
    # Multiple capable models on CPU → smallest that meets the floor (responsive).
    chosen = lam.resolve_llm_model(
        ["qwen2.5:14b", "llama3.1:8b", "qwen2.5:3b"], provider="ollama"
    )
    assert chosen == "llama3.1:8b"
