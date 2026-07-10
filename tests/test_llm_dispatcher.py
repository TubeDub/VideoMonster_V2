"""Tests for the LLM Dispatcher layer (TZ #3)."""

from __future__ import annotations

import pytest

from core.llm_dispatcher import LLMDispatcher
from core.model_registry import (
    ModelDescriptor,
    ModelKind,
    ModelRegistry,
    ModelStatus,
    classify_tier,
)
from llm_adapters import register_adapter
from llm_adapters.base import ChatRequest, ChatResult, HealthReport, LLMAdapter


@pytest.fixture(autouse=True)
def _disable_semantic_cache(monkeypatch):
    """Isolate dispatcher tests from cross-test semantic cache pollution."""
    monkeypatch.setenv("VM_SEMANTIC_CACHE", "0")
    monkeypatch.setenv("VM_AI_MEMORY", "0")


# ── Fake adapters (no network) ───────────────────────────────────────


class _OkAdapter(LLMAdapter):
    adapter_id = "fake_ok"

    def connect(self):
        return True

    def generate(self, request: ChatRequest) -> ChatResult:
        return ChatResult(
            text=f"[{self.descriptor.name}] {request.prompt}",
            model=self.descriptor.name,
            provider=self.descriptor.provider,
            latency_ms=10.0,
        )

    def health(self) -> HealthReport:
        return HealthReport(alive=True, last_latency_ms=10.0)


class _FailAdapter(LLMAdapter):
    adapter_id = "fake_fail"

    def connect(self):
        return True

    def generate(self, request: ChatRequest) -> ChatResult:
        return ChatResult(
            error=TimeoutError("boom"),
            model=self.descriptor.name,
            provider=self.descriptor.provider,
            latency_ms=5.0,
            timed_out=True,
        )

    def health(self) -> HealthReport:
        return HealthReport(alive=False, stalled=True)


register_adapter(_OkAdapter)
register_adapter(_FailAdapter)


def _fresh_dispatcher() -> LLMDispatcher:
    reg = ModelRegistry()
    d = LLMDispatcher(registry=reg)
    d._initialized = True  # skip network discovery
    return d


def _desc(name, adapter="fake_ok", provider="ollama", param_b=7.0, priority=100, adequate=True, kind=ModelKind.LOCAL):
    return ModelDescriptor(
        name=name, provider=provider, adapter=adapter, param_b=param_b,
        priority=priority, adequate=adequate, kind=kind, status=ModelStatus.READY,
    )


def test_registry_descriptor_fields_and_tier():
    d = _desc("qwen2.5:7b")
    assert classify_tier(3.0) == "light"
    assert classify_tier(7.0) == "standard"
    assert classify_tier(70.0) == "strong"
    dd = d.to_dict()
    for key in (
        "name", "provider", "kind", "param_b", "context_tokens", "supports_json",
        "supports_tools", "max_concurrency", "cost_per_1k", "priority", "tier",
        "status", "stats",
    ):
        assert key in dd


def test_entry_points_route_through_dispatcher():
    d = _fresh_dispatcher()
    d.registry.register(_desc("qwen2.5:7b"))
    assert d.generate("hi").startswith("[qwen2.5:7b]")
    assert d.translate("hi").startswith("[qwen2.5:7b]")
    assert d.review("hi").startswith("[qwen2.5:7b]")
    assert d.rewrite("hi").startswith("[qwen2.5:7b]")
    assert d.summary("hi").startswith("[qwen2.5:7b]")
    assert d.fix_json("{}").startswith("[qwen2.5:7b]")


def test_quality_first_prefers_stronger_model():
    d = _fresh_dispatcher()
    d.registry.register(_desc("small", param_b=3.0))       # light
    d.registry.register(_desc("big", param_b=32.0))        # strong
    out = d.translate("x")
    assert out.startswith("[big]")  # never picks weaker for speed (§7)


def test_failover_switches_on_failure():
    d = _fresh_dispatcher()
    # Primary fails, backup succeeds.
    d.registry.register(_desc("primary", adapter="fake_fail", param_b=32.0, priority=10))
    d.registry.register(_desc("backup", adapter="fake_ok", param_b=32.0, priority=20))
    text, err, meta = d.execute_chat("hello", task_type="translate")
    assert err is None
    assert text.startswith("[backup]")
    assert meta["attempts"] == 2
    assert any("primary" in f for f in meta["failover"])


def test_all_models_fail_returns_error():
    d = _fresh_dispatcher()
    d.registry.register(_desc("m1", adapter="fake_fail"))
    d.registry.register(_desc("m2", adapter="fake_fail"))
    text, err, meta = d.execute_chat("hello")
    assert text is None
    assert err is not None
    assert meta["attempts"] == 2


def test_hot_swap_active_model():
    d = _fresh_dispatcher()
    d.registry.register(_desc("qwen", param_b=32.0, priority=50))
    d.registry.register(_desc("deepseek", param_b=32.0, priority=10))
    # Auto-select would prefer deepseek (lower priority number).
    assert d.translate("x").startswith("[deepseek]")
    # Hot-swap to qwen — remaining calls use qwen without restart.
    assert d.set_active_model("qwen") is True
    assert d.translate("x").startswith("[qwen]")
    assert d.set_active_model("nonexistent") is False
    d.set_active_model(None)
    assert d.translate("x").startswith("[deepseek]")


def test_stats_recorded_per_model():
    d = _fresh_dispatcher()
    d.registry.register(_desc("qwen2.5:7b"))
    d.generate("a")
    d.generate("b")
    desc = d.registry.get("qwen2.5:7b")
    assert desc.stats.requests == 2
    assert desc.stats.successes == 2
    assert desc.stats.success_rate == 1.0
    status = d.get_status()
    assert "qwen2.5:7b" in status["models"]


def test_failover_chain_ordering_configurable(monkeypatch):
    monkeypatch.setenv("VM_LLM_FAILOVER_CHAIN", "deepseek,qwen")
    d = _fresh_dispatcher()
    d.registry.register(_desc("qwen", param_b=32.0, priority=10))
    d.registry.register(_desc("deepseek", param_b=32.0, priority=90))
    cands = d._candidates(task_type="translate", model_hint=None)
    # Chain puts deepseek first despite worse priority.
    assert cands[0].name == "deepseek"


def test_resource_limit_concurrency_semaphore():
    d = _fresh_dispatcher()
    desc = _desc("qwen2.5:7b")
    desc.max_concurrency = 2
    d.registry.register(desc)
    sem = d._semaphore_for(desc)
    assert sem.acquire(timeout=0.1)
    assert sem.acquire(timeout=0.1)
    assert sem.acquire(timeout=0.1) is False  # limit reached
    sem.release()
    sem.release()


def test_health_refresh_updates_status():
    d = _fresh_dispatcher()
    d.registry.register(_desc("good", adapter="fake_ok"))
    d.registry.register(_desc("bad", adapter="fake_fail"))
    report = d.refresh_health()
    assert report["good"]["alive"] is True
    assert d.registry.get("bad").status in (ModelStatus.STALLED, ModelStatus.OFFLINE)
