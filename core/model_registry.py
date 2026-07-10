"""Unified model registry for the LLM Dispatcher (TZ #3 §2).

Every model the program can use is described here with a single, uniform
descriptor. The registry is populated automatically from the existing discovery
layer (``llm_adaptation_mode`` / ``llm_orchestrator.model_pool``) and can be
extended by registering new descriptors — no core changes required.

The registry stores *metadata + live stats only*. It never performs network
calls itself; health probing and generation belong to the adapters.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ModelKind(str, Enum):
    LOCAL = "local"
    CLOUD = "cloud"


class ModelStatus(str, Enum):
    UNKNOWN = "unknown"
    READY = "ready"
    BUSY = "busy"
    STALLED = "stalled"
    OFFLINE = "offline"
    ERROR = "error"


# Rough parameter-size → quality tier. Kept consistent with llm_orchestrator.
def classify_tier(param_b: float) -> str:
    if param_b <= 0:
        return "cloud"
    if param_b < 5:
        return "light"
    if param_b < 15:
        return "standard"
    return "strong"


@dataclass
class ModelStats:
    """Live per-model statistics (TZ #3 §12)."""

    requests: int = 0
    successes: int = 0
    timeouts: int = 0
    errors: int = 0
    total_latency_ms: float = 0.0
    total_gen_chars: float = 0.0
    quality_sum: float = 0.0
    quality_n: int = 0
    ram_mb: float = 0.0
    vram_mb: float = 0.0
    last_latency_ms: float = 0.0
    last_response_at: float = 0.0
    consecutive_errors: int = 0

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / self.successes if self.successes else 0.0

    @property
    def avg_gen_chars(self) -> float:
        return self.total_gen_chars / self.successes if self.successes else 0.0

    @property
    def success_rate(self) -> float:
        return (self.successes / self.requests) if self.requests else 0.0

    @property
    def avg_quality(self) -> float:
        return self.quality_sum / self.quality_n if self.quality_n else 0.0

    def record(
        self,
        *,
        ok: bool,
        latency_ms: float,
        gen_chars: int = 0,
        timeout: bool = False,
        quality: float | None = None,
    ) -> None:
        self.requests += 1
        self.last_latency_ms = latency_ms
        if ok:
            self.successes += 1
            self.total_latency_ms += latency_ms
            self.total_gen_chars += gen_chars
            self.last_response_at = time.time()
            self.consecutive_errors = 0
        else:
            if timeout:
                self.timeouts += 1
            else:
                self.errors += 1
            self.consecutive_errors += 1
        if quality is not None:
            self.quality_sum += quality
            self.quality_n += 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "requests": self.requests,
            "successes": self.successes,
            "timeouts": self.timeouts,
            "errors": self.errors,
            "success_rate": round(self.success_rate, 3),
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "last_latency_ms": round(self.last_latency_ms, 1),
            "avg_gen_chars": round(self.avg_gen_chars, 1),
            "avg_quality": round(self.avg_quality, 3),
            "ram_mb": round(self.ram_mb, 1),
            "vram_mb": round(self.vram_mb, 1),
            "consecutive_errors": self.consecutive_errors,
        }


@dataclass
class ModelDescriptor:
    """One model, described uniformly (TZ #3 §2)."""

    name: str                       # Название (e.g. "qwen2.5:7b")
    provider: str                   # Тип / family (ollama, openai, anthropic, ...)
    kind: ModelKind = ModelKind.LOCAL   # Локальная / Облачная
    adapter: str = "openai_compatible"  # which adapter implementation to use
    param_b: float = 0.0            # Размер (billions)
    context_tokens: int = 8192      # Контекст
    endpoint_base: str = ""         # base_url for the adapter
    supports_json: bool = True      # Поддержка JSON
    supports_tools: bool = False    # Поддержка Tool Calling
    max_concurrency: int = 1        # Максимальное количество потоков
    cost_per_1k: float = 0.0        # Стоимость (если облако)
    priority: int = 100             # Приоритет (lower = preferred)
    tier: str = "standard"          # derived quality tier
    adequate: bool = True           # meets quality floor for dubbing
    status: ModelStatus = ModelStatus.UNKNOWN
    stats: ModelStats = field(default_factory=ModelStats)
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.tier or self.tier == "standard":
            self.tier = classify_tier(self.param_b)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "provider": self.provider,
            "kind": self.kind.value,
            "adapter": self.adapter,
            "param_b": self.param_b,
            "context_tokens": self.context_tokens,
            "endpoint_base": self.endpoint_base,
            "supports_json": self.supports_json,
            "supports_tools": self.supports_tools,
            "max_concurrency": self.max_concurrency,
            "cost_per_1k": self.cost_per_1k,
            "priority": self.priority,
            "tier": self.tier,
            "adequate": self.adequate,
            "status": self.status.value,
            "stats": self.stats.to_dict(),
        }


class ModelRegistry:
    """Thread-safe registry of all known models."""

    def __init__(self) -> None:
        self._models: dict[str, ModelDescriptor] = {}
        self._lock = threading.RLock()
        self._discovered = False

    def register(self, descriptor: ModelDescriptor, *, overwrite: bool = False) -> ModelDescriptor:
        with self._lock:
            existing = self._models.get(descriptor.name)
            if existing and not overwrite:
                # Merge freshly-discovered metadata, keep accumulated stats.
                existing.provider = descriptor.provider or existing.provider
                existing.kind = descriptor.kind
                existing.endpoint_base = descriptor.endpoint_base or existing.endpoint_base
                existing.param_b = descriptor.param_b or existing.param_b
                existing.tier = descriptor.tier or existing.tier
                existing.adequate = descriptor.adequate
                existing.adapter = descriptor.adapter or existing.adapter
                return existing
            self._models[descriptor.name] = descriptor
            return descriptor

    def unregister(self, name: str) -> bool:
        with self._lock:
            return self._models.pop(name, None) is not None

    def get(self, name: str) -> ModelDescriptor | None:
        with self._lock:
            return self._models.get(name)

    def all(self) -> list[ModelDescriptor]:
        with self._lock:
            return list(self._models.values())

    def available(self) -> list[ModelDescriptor]:
        with self._lock:
            return [
                m for m in self._models.values()
                if m.status in (ModelStatus.READY, ModelStatus.BUSY, ModelStatus.UNKNOWN)
            ]

    def by_provider(self, provider: str) -> list[ModelDescriptor]:
        with self._lock:
            return [m for m in self._models.values() if m.provider == provider]

    def by_tier(self, tier: str) -> list[ModelDescriptor]:
        with self._lock:
            return [m for m in self._models.values() if m.tier == tier]

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {name: m.to_dict() for name, m in self._models.items()}

    # ── Discovery ────────────────────────────────────────────────────

    def discover(self, *, force: bool = False) -> list[ModelDescriptor]:
        """Populate the registry from the existing discovery layer.

        Reuses ``llm_adaptation_mode`` + ``llm_orchestrator.model_pool`` so the
        dispatcher sees exactly the models the current pipeline would use.
        """
        with self._lock:
            if self._discovered and not force:
                return list(self._models.values())

        self._discover_local()
        self._discover_cloud()

        with self._lock:
            self._discovered = True
            return list(self._models.values())

    def _discover_local(self) -> None:
        try:
            from engines.llm_orchestrator.model_pool import get_model_pool

            pool = get_model_pool()
            pool.discover(force=True)
            for info in pool.all():
                base = getattr(info, "endpoint_base", "") or ""
                kind = ModelKind.CLOUD if getattr(info, "provider", "") in _CLOUD_PROVIDERS else ModelKind.LOCAL
                self.register(
                    ModelDescriptor(
                        name=info.name,
                        provider=getattr(info, "provider", "") or "ollama",
                        kind=kind,
                        adapter=_adapter_for(getattr(info, "provider", "")),
                        param_b=float(getattr(info, "param_b", 0.0) or 0.0),
                        endpoint_base=base,
                        adequate=bool(getattr(info, "adequate", True)),
                        priority=_priority_for(getattr(info, "provider", "")),
                    )
                )
        except Exception:
            pass

    def _discover_cloud(self) -> None:
        import os

        # OpenAI-compatible cloud (already the live fallback path).
        if os.getenv("OPENAI_API_KEY") or os.getenv("VM_OPENAI_API_KEY"):
            self.register(
                ModelDescriptor(
                    name=os.getenv("VM_OPENAI_MODEL", "gpt-4o-mini"),
                    provider="openai",
                    kind=ModelKind.CLOUD,
                    adapter="openai_compatible",
                    param_b=0.0,
                    context_tokens=128000,
                    supports_tools=True,
                    cost_per_1k=0.15,
                    priority=50,
                    tier="strong",
                    adequate=True,
                )
            )
        if os.getenv("ANTHROPIC_API_KEY") or os.getenv("VM_ANTHROPIC_API_KEY"):
            self.register(
                ModelDescriptor(
                    name=os.getenv("VM_ANTHROPIC_MODEL", "claude-3-5-sonnet-latest"),
                    provider="anthropic",
                    kind=ModelKind.CLOUD,
                    adapter="anthropic",
                    context_tokens=200000,
                    supports_tools=True,
                    cost_per_1k=3.0,
                    priority=40,
                    tier="strong",
                    adequate=True,
                )
            )
        if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or os.getenv("VM_GEMINI_API_KEY"):
            self.register(
                ModelDescriptor(
                    name=os.getenv("VM_GEMINI_MODEL", "gemini-1.5-pro"),
                    provider="gemini",
                    kind=ModelKind.CLOUD,
                    adapter="gemini",
                    context_tokens=1000000,
                    supports_tools=True,
                    cost_per_1k=1.25,
                    priority=45,
                    tier="strong",
                    adequate=True,
                )
            )


_CLOUD_PROVIDERS = {"openai", "anthropic", "gemini", "openrouter", "deepseek-cloud"}

_PROVIDER_ADAPTER = {
    "ollama": "openai_compatible",
    "lmstudio": "openai_compatible",
    "vllm": "openai_compatible",
    "openai": "openai_compatible",
    "openrouter": "openai_compatible",
    "anthropic": "anthropic",
    "gemini": "gemini",
}

_PROVIDER_PRIORITY = {
    "anthropic": 40,
    "gemini": 45,
    "openai": 50,
    "ollama": 100,
    "lmstudio": 110,
    "vllm": 90,
}


def _adapter_for(provider: str) -> str:
    return _PROVIDER_ADAPTER.get((provider or "").lower(), "openai_compatible")


def _priority_for(provider: str) -> int:
    return _PROVIDER_PRIORITY.get((provider or "").lower(), 100)


_registry: ModelRegistry | None = None
_registry_lock = threading.Lock()


def get_registry() -> ModelRegistry:
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = ModelRegistry()
    return _registry
