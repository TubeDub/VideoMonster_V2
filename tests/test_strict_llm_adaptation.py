"""Strict LLM Adaptation mode + capabilities + caching (TubeDub AI v3.2).

Covers TZ §1/§2 (mode resolution + feature flag), §6 (LLM cache), §7 (stop
diagnostics), §8 (provider detection), §9 (capability auto-detection), and the
OpenDDF mode/rewrite surface (§5).
"""

from __future__ import annotations

_LLM_ENV = (
    "OPENAI_API_KEY",
    "VM_LLM_API_KEY",
    "VM_OPENAI_API_KEY",
    "VM_LLM_BASE_URL",
    "OPENAI_BASE_URL",
)


def _clear_llm_env(monkeypatch):
    for var in _LLM_ENV + ("VM_STRICT_LLM_ADAPTATION", "VM_TRANSLATE_MODEL"):
        monkeypatch.delenv(var, raising=False)
    # Deterministic: don't let a real local LLM on the dev box flip these tests.
    monkeypatch.setenv("VM_LLM_AUTODISCOVER", "0")
    import engines.llm_adaptation_mode as lam

    lam._discovery_cache["ts"] = 0.0
    lam._discovery_cache["result"] = None


# ── Task 1: feature flag registration ──────────────────────────────────────
def test_feature_flag_registered_and_default_off(monkeypatch):
    from engines.core.feature_flags import is_enabled

    monkeypatch.delenv("VM_STRICT_LLM_ADAPTATION", raising=False)
    # Registered but disabled by default → automatic.
    assert is_enabled("strict_llm_adaptation", developer_session=False) is False


def test_env_override_enables_flag(monkeypatch):
    from engines.core.feature_flags import is_enabled

    monkeypatch.setenv("VM_STRICT_LLM_ADAPTATION", "1")
    assert is_enabled("strict_llm_adaptation", developer_session=False) is True
    monkeypatch.setenv("VM_STRICT_LLM_ADAPTATION", "off")
    assert is_enabled("strict_llm_adaptation", developer_session=False) is False


# ── Task 2: mode resolution precedence ─────────────────────────────────────
def test_normalize_mode():
    from engines.llm_adaptation_mode import MODE_AUTOMATIC, MODE_STRICT, normalize_mode

    assert normalize_mode("strict") == MODE_STRICT
    assert normalize_mode("automatic") == MODE_AUTOMATIC
    assert normalize_mode(True) == MODE_STRICT
    assert normalize_mode(None) == MODE_AUTOMATIC
    assert normalize_mode("garbage") == MODE_AUTOMATIC


def test_per_job_setting_wins(monkeypatch):
    from engines.llm_adaptation_mode import MODE_AUTOMATIC, MODE_STRICT, resolve_adaptation_mode

    monkeypatch.setenv("VM_STRICT_LLM_ADAPTATION", "1")  # flag says strict
    # Per-job automatic overrides the strict feature flag.
    assert resolve_adaptation_mode({"strict_llm_adaptation": "automatic"}) == MODE_AUTOMATIC
    assert resolve_adaptation_mode({"strict_llm_adaptation": "strict"}) == MODE_STRICT
    # No per-job value → falls back to flag (strict here).
    assert resolve_adaptation_mode({}) == MODE_STRICT
    monkeypatch.delenv("VM_STRICT_LLM_ADAPTATION", raising=False)
    assert resolve_adaptation_mode({}) == MODE_AUTOMATIC
    assert resolve_adaptation_mode({"strict_llm_adaptation": None}) == MODE_AUTOMATIC


# ── Task 8/9: provider detection + capability auto-detection ───────────────
def test_provider_detection():
    from engines.llm_adaptation_mode import detect_llm_provider

    assert detect_llm_provider("http://localhost:11434/v1") == "ollama"
    assert detect_llm_provider("http://localhost:1234/v1") == "lmstudio"
    assert detect_llm_provider("https://openrouter.ai/api/v1") == "openrouter"
    assert detect_llm_provider("https://api.openai.com/v1") == "openai"
    assert detect_llm_provider("http://my-vllm:8000/v1") == "openai-compatible"
    assert detect_llm_provider("") == "none"


def test_capabilities_no_llm(monkeypatch):
    from engines.llm_adaptation_mode import detect_capabilities

    _clear_llm_env(monkeypatch)
    caps = detect_capabilities()
    assert caps["llm_available"] is False
    assert caps["rule_rewrite_available"] is True
    assert caps["provider"] == "none"


def test_capabilities_selfhosted(monkeypatch):
    from engines.llm_adaptation_mode import detect_capabilities

    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("VM_LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("VM_TRANSLATE_MODEL", "qwen2.5:7b")
    caps = detect_capabilities()
    assert caps["llm_available"] is True
    assert caps["local_llm_available"] is True
    assert caps["provider"] == "ollama"
    assert caps["model"] == "qwen2.5:7b"


# ── Task 7: stop diagnostics ───────────────────────────────────────────────
def test_stop_diagnostics_no_llm(monkeypatch):
    from engines.llm_adaptation_mode import MODE_STRICT, build_stop_diagnostics

    _clear_llm_env(monkeypatch)
    diag = build_stop_diagnostics(
        mode=MODE_STRICT,
        reason="no LLM endpoint configured",
        pending_indices=[2, 5],
        total_segments=10,
    )
    assert diag["strict_gate_activated"] is True
    assert diag["requires_llm_count"] == 2
    assert diag["problem_segment_indices"] == [2, 5]
    assert diag["llm_available"] is False
    assert any("AI-модуль" in r for r in diag["recommendations"])


# ── Task 6: LLM cache ──────────────────────────────────────────────────────
def test_llm_cache_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setenv("VM_LLM_CACHE_DIR", str(tmp_path))
    import importlib

    import engines.llm_cache as cache

    importlib.reload(cache)
    k1 = cache.make_key("3.2.0", "model-a", "prompt one")
    k2 = cache.make_key("3.2.0", "model-a", "prompt two")
    k3 = cache.make_key("9.9.9", "model-a", "prompt one")  # algo bump → new key
    assert cache.get(k1) is None
    cache.put(k1, "result one")
    assert cache.get(k1) == "result one"
    assert cache.get(k2) is None
    assert k1 != k3  # algorithm version invalidates


def test_llm_cache_persists_across_reload(monkeypatch, tmp_path):
    monkeypatch.setenv("VM_LLM_CACHE_DIR", str(tmp_path))
    import importlib

    import engines.llm_cache as cache

    importlib.reload(cache)
    key = cache.make_key("v", "m", "persisted")
    cache.put(key, "saved-value")

    importlib.reload(cache)
    assert cache.get(key) == "saved-value"


def test_llm_chat_uses_cache(monkeypatch, tmp_path):
    """A repeated identical rewrite must not hit the network twice (TZ §6)."""
    monkeypatch.setenv("VM_LLM_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("VM_LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("VM_TRANSLATE_MODEL", "test-model")
    import importlib

    import engines.llm_cache as cache

    importlib.reload(cache)
    from engines import translation_adapt

    # Isolate from other tests that may have tripped the global circuit breaker
    # (a broken/slow LLM in a prior test must not skip this one's real call).
    translation_adapt.reset_circuit_breaker()
    translation_adapt.reset_endpoint_cache()
    translation_adapt.begin_llm_capture()

    calls = {"n": 0}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            import json

            return json.dumps(
                {"choices": [{"message": {"content": "ОТВЕТ"}}]}
            ).encode("utf-8")

    def _fake_urlopen(req, timeout=30):
        calls["n"] += 1
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

    out1 = translation_adapt._llm_chat("unique prompt 42", max_tokens=100)
    out2 = translation_adapt._llm_chat("unique prompt 42", max_tokens=100)
    assert out1 == "ОТВЕТ"
    assert out2 == "ОТВЕТ"
    assert calls["n"] == 1, "second identical call must come from cache"


# ── Task 5: OpenDDF mode + per-segment rewrite surface ─────────────────────
def test_openddf_mode_block(monkeypatch):
    from engines.segment_timing_qa import _build_openddf_adaptation_mode_block

    _clear_llm_env(monkeypatch)
    block = _build_openddf_adaptation_mode_block(
        {
            "adaptation_mode": "strict",
            "llm_gate_diagnostics": {
                "strict_gate_activated": True,
                "reason": "no LLM endpoint configured",
            },
        }
    )
    assert block["mode"] == "strict"
    assert block["strict_gate_activated"] is True
    assert block["stop_reason"] == "no LLM endpoint configured"


def test_resolve_model_ignores_stale_cloud_default_on_local():
    from engines.llm_adaptation_mode import resolve_llm_model

    # gpt-4o-mini is not on Ollama → auto-select best installed model.
    picked = resolve_llm_model(
        ["llama3.1:8b", "qwen2.5:7b", "nomic-embed-text"], provider="ollama"
    )
    assert picked == "qwen2.5:7b"


def test_discovery_probes_candidates(monkeypatch):
    """Auto-discovery picks the first reachable server (TZ §9)."""
    import engines.llm_adaptation_mode as lam

    monkeypatch.delenv("VM_LLM_AUTODISCOVER", raising=False)
    lam._discovery_cache["ts"] = 0.0
    lam._discovery_cache["result"] = None

    # Pretend Ollama's port is open and it reports one model.
    monkeypatch.setattr(lam, "_port_open", lambda host, port, timeout=0.25: port == 11434)
    monkeypatch.setattr(
        lam,
        "_http_get_json",
        lambda url, timeout=0.8: {"models": [{"name": "qwen2.5:7b"}]}
        if "11434" in url
        else None,
    )
    disc = lam.discover_local_llm(force=True)
    assert disc is not None
    assert disc["provider"] == "ollama"
    assert disc["base_url"] == "http://127.0.0.1:11434/v1"
    assert "qwen2.5:7b" in disc["models"]


def test_discovery_disabled(monkeypatch):
    import engines.llm_adaptation_mode as lam

    monkeypatch.setenv("VM_LLM_AUTODISCOVER", "0")
    lam._discovery_cache["ts"] = 0.0
    assert lam.discover_local_llm(force=True) is None


def test_endpoint_prefers_env_over_discovery(monkeypatch):
    import engines.llm_adaptation_mode as lam

    monkeypatch.setenv("VM_LLM_BASE_URL", "http://localhost:1234/v1")
    ep = lam.resolve_llm_endpoint()
    assert ep["source"] == "env"
    assert ep["provider"] == "lmstudio"


def test_detect_rewrite_usage():
    from engines.segment_timing_qa import _detect_rewrite_usage

    usage = _detect_rewrite_usage(
        [[{"stage": "strong", "applied": True}, {"stage": "llm_rephrase", "applied": True}]],
        ["fits_after_stage"],
    )
    assert usage["rule_rewrite_used"] is True
    assert usage["llm_rewrite_used"] is True

    usage2 = _detect_rewrite_usage([[{"stage": "minimal"}]], [])
    assert usage2["rule_rewrite_used"] is True
    assert usage2["llm_rewrite_used"] is False
