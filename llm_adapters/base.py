"""LLM adapter interface (TZ #3 §4).

Every model implements the same interface so the Dispatcher never needs to know
a model's internals:

    connect()          — prepare / verify the endpoint
    generate()         — run one completion
    health()           — liveness + speed snapshot
    cancel()           — best-effort cancel of in-flight work
    estimate_time()    — predicted latency for a request
    estimate_tokens()  — rough token count for text

Adding a new model = adding a new adapter here. No dispatcher changes.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChatRequest:
    prompt: str
    system: str | None = None
    max_tokens: int = 512
    temperature: float = 0.2
    timeout: float | None = None
    model: str | None = None
    segment: int | None = None
    stage: str = ""
    task_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatResult:
    text: str | None = None
    error: Exception | None = None
    model: str = ""
    provider: str = ""
    latency_ms: float = 0.0
    finish_reason: str = ""
    tokens_out: int = 0
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.text)


@dataclass
class HealthReport:
    alive: bool = False
    stalled: bool = False
    last_latency_ms: float = 0.0
    avg_latency_ms: float = 0.0
    error_count: int = 0
    gpu_available: bool = False
    network_available: bool = True
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "alive": self.alive,
            "stalled": self.stalled,
            "last_latency_ms": round(self.last_latency_ms, 1),
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "error_count": self.error_count,
            "gpu_available": self.gpu_available,
            "network_available": self.network_available,
            "detail": self.detail,
        }


class LLMAdapter(abc.ABC):
    """Uniform interface for every model backend."""

    #: Adapter identifier, matches ``ModelDescriptor.adapter``.
    adapter_id: str = "base"

    def __init__(self, descriptor: Any) -> None:
        self.descriptor = descriptor
        self._connected = False
        self._cancelled = False

    @abc.abstractmethod
    def connect(self) -> bool:
        """Prepare / verify the endpoint. Idempotent."""

    @abc.abstractmethod
    def generate(self, request: ChatRequest) -> ChatResult:
        """Run one completion. Must never raise — errors go into ChatResult."""

    @abc.abstractmethod
    def health(self) -> HealthReport:
        """Liveness + speed snapshot."""

    def cancel(self) -> None:
        """Best-effort cancel of in-flight work."""
        self._cancelled = True

    def estimate_tokens(self, text: str) -> int:
        """Rough token estimate (~4 chars/token for mixed scripts)."""
        if not text:
            return 0
        return max(1, int(len(text) / 4) + 1)

    def estimate_time(self, request: ChatRequest) -> float:
        """Predicted latency (seconds) from measured avg + request size."""
        stats = getattr(self.descriptor, "stats", None)
        avg_ms = getattr(stats, "avg_latency_ms", 0.0) if stats else 0.0
        if avg_ms > 0:
            return avg_ms / 1000.0
        # Cold estimate: cloud is fast, local scales with size.
        tokens = self.estimate_tokens(request.prompt) + request.max_tokens
        kind = getattr(self.descriptor, "kind", None)
        per_tok = 0.008 if (kind and getattr(kind, "value", "") == "local") else 0.002
        return max(1.0, tokens * per_tok)
