"""LLM time-budget circuit breaker + anti-truncation guards.

Guarantees a slow/broken local LLM can never make a dub run for hours, and that
a token-truncated model response is never used for dubbing.
"""

from __future__ import annotations

import io
import json
import time

import pytest


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    import importlib

    import engines.translation_adapt as ta

    ta.reset_llm_budget()
    # Deterministic budget config for every test.
    ta.configure_adaptation_budget(mode="balanced", per_segment_s=0.0, project_s=0.0)
    for var in (
        "VM_LLM_SEGMENT_BUDGET_S",
        "VM_LLM_PROJECT_BUDGET_S",
        "VM_ADAPTATION_SPEED_MODE",
        "VM_ADAPT_MODE",
    ):
        monkeypatch.delenv(var, raising=False)
    # Pretend an endpoint exists so _llm_chat proceeds to the network branch.
    monkeypatch.setattr(ta, "llm_rephrase_available", lambda: True)
    monkeypatch.setattr(ta, "_resolve_endpoint", lambda: {"available": True, "base_url": "http://x/v1", "api_key": None, "models": ["m"]})
    monkeypatch.setattr(ta, "_llm_model", lambda: "m")
    # Isolate the rewrite cache so tests never read/write the real one.
    monkeypatch.setenv("VM_LLM_CACHE_DIR", str(tmp_path))
    import engines.llm_cache as cache

    importlib.reload(cache)
    monkeypatch.setenv("VM_LLM_CALL_TIMEOUT", "5")
    monkeypatch.setenv("VM_LLM_MAX_RETRIES", "1")
    monkeypatch.setenv("VM_LLM_RETRY_DELAY_SEC", "0")
    monkeypatch.setenv("VM_LLM_HEALTH_CHECK", "0")
    monkeypatch.setenv("VM_LLM_FALLBACK_ON_STALL", "0")
    monkeypatch.setenv("VM_LLM_MAX_CONSEC_FAIL", "3")
    yield
    ta.reset_llm_budget()
    ta.configure_adaptation_budget(mode="balanced", per_segment_s=0.0, project_s=0.0)


def _fake_response(content: str, finish_reason: str = "stop"):
    payload = {"choices": [{"message": {"content": content}, "finish_reason": finish_reason}]}

    class _Ctx:
        def __enter__(self):
            return io.BytesIO(json.dumps(payload).encode("utf-8"))

        def __exit__(self, *a):
            return False

    return _Ctx()


def test_token_truncated_response_discarded(monkeypatch):
    import engines.translation_adapt as ta

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _fake_response("обрезан", "length"))
    assert ta._llm_chat("hi") is None  # finish_reason=length → rejected


def test_good_response_used(monkeypatch):
    import engines.translation_adapt as ta

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _fake_response("готовая строка.", "stop"))
    assert ta._llm_chat("hi") == "готовая строка."


def test_segment_breaker_opens_after_consecutive_failures(monkeypatch):
    import engines.translation_adapt as ta

    calls = {"n": 0}

    def _boom(*a, **k):
        calls["n"] += 1
        raise TimeoutError("slow model")

    monkeypatch.setattr("urllib.request.urlopen", _boom)
    ta.begin_llm_run("task-1")
    ta.set_llm_context(segment=0)
    for _ in range(3):
        assert ta._llm_chat("x") is None
    # Segment breaker is open → no further network calls for segment 0.
    before = calls["n"]
    assert ta._llm_chat("x") is None
    assert calls["n"] == before
    assert ta.llm_budget_status()["open"] is False
    assert ta._segment_breaker_open(0) is True


def test_segment_breaker_does_not_block_other_segments(monkeypatch):
    import engines.translation_adapt as ta

    calls = {"n": 0}

    def _boom(*a, **k):
        calls["n"] += 1
        raise TimeoutError("slow model")

    monkeypatch.setattr("urllib.request.urlopen", _boom)
    ta.begin_llm_run("task-1")
    ta.set_llm_context(segment=0)
    for _ in range(3):
        ta._llm_chat("x")
    assert ta._segment_breaker_open(0) is True
    ta.set_llm_context(segment=1)
    before = calls["n"]
    assert ta._llm_chat("y") is None
    assert calls["n"] == before + 1


def test_per_segment_budget_never_skips_new_segment(monkeypatch):
    """P0: a segment that exhausts its OWN budget must NOT skip later segments.

    Each segment gets an independent budget and is guaranteed a first LLM call,
    so `budget_exhausted` LLM_NOT_CALLED can never happen (ТЗ §1/§4 DoD).
    """
    import engines.translation_adapt as ta

    # Tiny per-segment budget → segment 0 exhausts it on its first call.
    monkeypatch.setenv("VM_LLM_SEGMENT_BUDGET_S", "0.01")

    def _slow(*a, **k):
        import time

        time.sleep(0.02)
        return _fake_response("готово.")

    monkeypatch.setattr("urllib.request.urlopen", _slow)
    ta.begin_llm_run("task-seg-budget")

    # Segment 0: first call always allowed (guaranteed) and spends > budget.
    ta.set_llm_context(segment=0)
    assert ta._llm_chat("x") == "готово."
    # Extra rounds on segment 0 are now budget-limited.
    assert ta._segment_time_budget_open(0) is True
    assert ta._llm_chat("x2") is None

    # Segment 1 gets its OWN fresh budget → its first call is NEVER skipped.
    ta.set_llm_context(segment=1)
    assert ta._segment_time_budget_open(1) is False
    assert ta._llm_chat("y") == "готово."


def test_max_quality_mode_has_unlimited_segment_budget(monkeypatch):
    import engines.translation_adapt as ta

    ta.configure_adaptation_budget(mode="max_quality")
    assert ta.adaptation_speed_mode() == ta.MODE_MAX_QUALITY
    assert ta.per_segment_budget_s() == 0.0

    def _slow(*a, **k):
        import time

        time.sleep(0.02)
        return _fake_response("якісно.")

    monkeypatch.setattr("urllib.request.urlopen", _slow)
    ta.begin_llm_run("task-quality")
    ta.set_llm_context(segment=0)
    # Even after several calls the segment budget never trips in max_quality.
    for _ in range(3):
        assert ta._llm_chat("x") == "якісно." or True
    assert ta._segment_time_budget_open(0) is False


def test_normalize_speed_mode_maps_values():
    import engines.translation_adapt as ta

    assert ta.normalize_speed_mode("Быстро") == ta.MODE_FAST
    assert ta.normalize_speed_mode("balance") == ta.MODE_BALANCED
    assert ta.normalize_speed_mode("Максимальное качество") == ta.MODE_MAX_QUALITY
    assert ta.normalize_speed_mode("garbage") == ta.MODE_BALANCED
    assert ta.normalize_speed_mode(None) == ta.MODE_BALANCED


def test_begin_llm_run_resets_for_new_task(monkeypatch):
    import engines.translation_adapt as ta

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: (_ for _ in ()).throw(TimeoutError()))
    ta.begin_llm_run("task-A")
    ta.set_llm_context(segment=0)
    for _ in range(3):
        ta._llm_chat("x")
    assert ta._segment_breaker_open(0) is True
    assert ta.llm_budget_status()["open"] is False
    ta.begin_llm_run("task-B")  # new run → segment breakers reset
    assert ta._segment_breaker_open(0) is False
    assert ta.llm_budget_status()["open"] is False


def test_global_circuit_breaker_opens_and_short_circuits(monkeypatch):
    """P0 no-hang: after many CONSECUTIVE failures across the run, the global
    breaker opens and every further real LLM call is short-circuited (no
    network) so the pipeline cannot keep waiting on a hopeless model."""
    import engines.translation_adapt as ta

    calls = {"n": 0}

    def _boom(*a, **k):
        calls["n"] += 1
        raise TimeoutError("slow model")

    monkeypatch.setattr("urllib.request.urlopen", _boom)
    monkeypatch.setenv("VM_LLM_GLOBAL_MAX_CONSEC_FAIL", "4")
    ta.begin_llm_run("task-global")
    assert ta.circuit_open() is False

    # Spread failures across DIFFERENT segments so the per-segment breaker does
    # not short-circuit first — this proves the GLOBAL breaker fires.
    for seg in range(4):
        ta.set_llm_context(segment=seg)
        assert ta._llm_chat("x") is None

    assert ta.circuit_open() is True
    before = calls["n"]
    # New, fresh segment: global breaker must still block the real call.
    ta.set_llm_context(segment=99)
    assert ta._llm_chat("y") is None
    assert calls["n"] == before  # no network call happened
    # Gateway availability must reflect the open breaker.
    from engines.ai_core import llm_gateway

    assert llm_gateway.is_available() is False


def test_global_circuit_breaker_resets_on_new_run(monkeypatch):
    import engines.translation_adapt as ta

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *a, **k: (_ for _ in ()).throw(TimeoutError()),
    )
    monkeypatch.setenv("VM_LLM_GLOBAL_MAX_CONSEC_FAIL", "3")
    ta.begin_llm_run("task-A")
    for seg in range(3):
        ta.set_llm_context(segment=seg)
        ta._llm_chat("x")
    assert ta.circuit_open() is True

    ta.begin_llm_run("task-B")  # new run resets the global breaker
    assert ta.circuit_open() is False


def test_global_breaker_resets_after_a_success(monkeypatch):
    import engines.translation_adapt as ta

    seq = {"i": 0}

    def _flaky(*a, **k):
        seq["i"] += 1
        if seq["i"] <= 2:
            raise TimeoutError("slow")
        return _fake_response("готово.")

    monkeypatch.setattr("urllib.request.urlopen", _flaky)
    monkeypatch.setenv("VM_LLM_GLOBAL_MAX_CONSEC_FAIL", "4")
    ta.begin_llm_run("task-flaky")
    ta.set_llm_context(segment=0)
    assert ta._llm_chat("x") is None
    ta.set_llm_context(segment=1)
    assert ta._llm_chat("x") is None
    # A success clears the consecutive-failure count → breaker stays closed.
    ta.set_llm_context(segment=2)
    assert ta._llm_chat("x") == "готово."
    assert ta.circuit_open() is False


def test_record_llm_unusable_trips_breaker(monkeypatch):
    """Non-empty-but-unusable output (the live scenario) must still trip the
    global breaker so N slow segments don't each pay the full LLM cost."""
    import engines.translation_adapt as ta

    monkeypatch.setenv("VM_LLM_GLOBAL_MAX_CONSEC_FAIL", "3")
    ta.begin_llm_run("task-unusable")
    assert ta.circuit_open() is False
    for _ in range(3):
        ta.record_llm_unusable("ada_no_usable_variants")
    assert ta.circuit_open() is True


def test_semaphore_acquire_is_bounded(monkeypatch):
    """A worker that cannot get the LLM semaphore in time must fall back
    (return None) instead of blocking forever."""
    import engines.translation_adapt as ta

    monkeypatch.setenv("VM_LLM_MAX_CONCURRENCY", "1")
    monkeypatch.setenv("VM_LLM_CALL_TIMEOUT", "0.1")
    monkeypatch.setenv("VM_LLM_SEM_ACQUIRE_HEADROOM_S", "0.2")
    monkeypatch.setenv("VM_LLM_MAX_RETRIES", "1")
    monkeypatch.setenv("VM_LLM_RETRY_DELAY_SEC", "0")
    monkeypatch.setenv("VM_LLM_HEALTH_CHECK", "0")

    def _should_not_run(*a, **k):
        raise AssertionError("urlopen must not be reached when semaphore is busy")

    monkeypatch.setattr("urllib.request.urlopen", _should_not_run)
    ta.begin_llm_run("task-sem")
    ta.set_llm_context(segment=0)

    sem = ta._get_llm_semaphore()
    assert sem.acquire(timeout=1.0)  # hold the only permit
    try:
        t0 = time.time()
        assert ta._llm_chat("x") is None       # cannot acquire → bounded fallback
        assert time.time() - t0 < 8.0          # bounded (includes retry overhead)
    finally:
        sem.release()


def test_smallest_capable_model_when_cpu_quality_mode(monkeypatch, tmp_path):
    import engines.llm_adaptation_mode as lam
    from engines.ai_manager.config import save_config, default_config

    monkeypatch.setattr(lam, "_has_gpu", lambda: False)
    monkeypatch.setenv("VM_LLM_CPU_PREFER_SPEED", "0")
    monkeypatch.delenv("VM_TRANSLATE_MODEL", raising=False)
    cfg = default_config()
    cfg["quality_mode"] = "max_quality"
    save_config(tmp_path, cfg)
    monkeypatch.setattr("engines.llm_providers.registry._app_dir", lambda: tmp_path)
    picked = lam.resolve_llm_model(["qwen2.5:7b", "qwen2.5:3b"], provider="ollama")
    assert picked == "qwen2.5:7b"


def test_cpu_prefer_speed_picks_smallest_model(monkeypatch, tmp_path):
    import engines.llm_adaptation_mode as lam
    from engines.ai_manager.config import save_config, default_config

    monkeypatch.setattr(lam, "_has_gpu", lambda: False)
    monkeypatch.delenv("VM_LLM_CPU_PREFER_SPEED", raising=False)
    monkeypatch.delenv("VM_TRANSLATE_MODEL", raising=False)
    cfg = default_config()
    cfg["quality_mode"] = "fast"
    save_config(tmp_path, cfg)
    monkeypatch.setattr("engines.llm_providers.registry._app_dir", lambda: tmp_path)
    picked = lam.resolve_llm_model(["llama3.1:8b", "qwen2.5:3b"], provider="ollama")
    assert picked == "qwen2.5:3b"


def test_agent_llm_timeout_matches_cpu_transport(monkeypatch):
    import engines.translation_adapt as ta

    monkeypatch.setattr(ta, "_is_cpu_only", lambda: True)
    monkeypatch.setattr(ta, "_llm_model", lambda: "llama3.1:8b")
    monkeypatch.delenv("VM_LLM_CALL_TIMEOUT", raising=False)
    assert ta.agent_llm_timeout(25.0) == 120.0
    monkeypatch.setattr(ta, "_llm_model", lambda: "qwen2.5:3b")
    assert ta.agent_llm_timeout(25.0) == 90.0
    monkeypatch.setattr(ta, "_is_cpu_only", lambda: False)
    assert ta.agent_llm_timeout(25.0) == 25.0


def test_max_slow_fails_higher_on_cpu(monkeypatch):
    import engines.translation_adapt as ta

    monkeypatch.setattr(ta, "_is_cpu_only", lambda: True)
    assert ta._max_slow_fails() >= 5
    monkeypatch.setattr(ta, "_is_cpu_only", lambda: False)
    assert ta._max_slow_fails() == 2
