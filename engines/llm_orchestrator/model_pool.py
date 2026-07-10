"""Discover and classify available LLM models into quality tiers."""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("tubedub.llm_orchestrator.pool")

_PARAM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*b\b", re.I)


class ModelTier(str, Enum):
    """Quality tier — higher = stronger model, slower."""

    LIGHT = "light"       # ≤4B — short/simple segments only
    STANDARD = "standard" # 5–12B — default adaptation
    STRONG = "strong"     # ≥13B or cloud flagship — complex segments / backup


@dataclass
class LLMModelInfo:
    name: str
    provider: str
    param_b: float
    tier: ModelTier
    adequate: bool
    endpoint_base: str = ""
    available: bool = True
    # Rolling latency (ms) — updated after each call
    avg_latency_ms: float = 0.0
    call_count: int = 0
    failure_count: int = 0
    last_used_at: float = 0.0
    in_flight: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def failure_rate(self) -> float:
        if self.call_count <= 0:
            return 0.0
        return self.failure_count / self.call_count

    @property
    def is_busy(self) -> bool:
        return self.in_flight > 0

    def record_call(self, *, latency_ms: float, ok: bool) -> None:
        self.call_count += 1
        if not ok:
            self.failure_count += 1
        # Exponential moving average for latency
        alpha = 0.25
        if self.avg_latency_ms <= 0:
            self.avg_latency_ms = latency_ms
        else:
            self.avg_latency_ms = alpha * latency_ms + (1 - alpha) * self.avg_latency_ms
        self.last_used_at = time.monotonic()


def _param_billions(name: str) -> float:
    m = _PARAM_RE.search(name or "")
    if m:
        return float(m.group(1))
    low = (name or "").lower()
    if "70b" in low or "72b" in low:
        return 70.0
    if "32b" in low:
        return 32.0
    if "14b" in low:
        return 14.0
    if "13b" in low:
        return 13.0
    if "9b" in low or "8b" in low:
        return 8.0
    if "7b" in low:
        return 7.0
    if "3b" in low:
        return 3.0
    if "gpt-4" in low:
        return 100.0
    if "gpt-3.5" in low:
        return 20.0
    return 7.0


def _tier_for(param_b: float, *, cloud: bool = False) -> ModelTier:
    if cloud and param_b >= 20:
        return ModelTier.STRONG
    if param_b >= 13:
        return ModelTier.STRONG
    if param_b >= 5:
        return ModelTier.STANDARD
    return ModelTier.LIGHT


def _adequate_for_dub(param_b: float, *, cloud: bool = False) -> bool:
    if cloud:
        return True
    return param_b >= 7.0


class LLMModelPool:
    """Registry of discovered models with tier classification and latency tracking."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._models: dict[str, LLMModelInfo] = {}
        self._discovered_at = 0.0
        self._ttl = 120.0

    def discover(self, *, force: bool = False) -> list[LLMModelInfo]:
        now = time.monotonic()
        with self._lock:
            if not force and self._models and (now - self._discovered_at) < self._ttl:
                return list(self._models.values())

        models = self._discover_impl()
        with self._lock:
            # Preserve latency stats for models still present
            for m in models:
                old = self._models.get(m.name)
                if old:
                    m.avg_latency_ms = old.avg_latency_ms
                    m.call_count = old.call_count
                    m.failure_count = old.failure_count
                self._models[m.name] = m
            self._discovered_at = now
            return list(self._models.values())

    def _discover_impl(self) -> list[LLMModelInfo]:
        from engines.llm_adaptation_mode import resolve_llm_endpoint, resolve_llm_model

        ep = resolve_llm_endpoint()
        if not ep.get("available"):
            return []

        provider = str(ep.get("provider") or "unknown")
        base = str(ep.get("base_url") or "")
        is_cloud = provider == "openai" or bool(ep.get("api_key"))
        tags = list(ep.get("models") or [])

        if not tags:
            primary = resolve_llm_model([], provider=provider)
            if primary:
                tags = [primary]

        out: list[LLMModelInfo] = []
        for tag in tags:
            name = str(tag).strip()
            if not name:
                continue
            pb = _param_billions(name)
            tier = _tier_for(pb, cloud=is_cloud)
            out.append(
                LLMModelInfo(
                    name=name,
                    provider=provider,
                    param_b=pb,
                    tier=tier,
                    adequate=_adequate_for_dub(pb, cloud=is_cloud),
                    endpoint_base=base,
                    available=True,
                )
            )

        # Ensure primary resolved model is present
        primary = resolve_llm_model(tags, provider=provider)
        if primary and not any(m.name == primary for m in out):
            pb = _param_billions(primary)
            out.append(
                LLMModelInfo(
                    name=primary,
                    provider=provider,
                    param_b=pb,
                    tier=_tier_for(pb, cloud=is_cloud),
                    adequate=_adequate_for_dub(pb, cloud=is_cloud),
                    endpoint_base=base,
                )
            )

        out.sort(key=lambda m: (-m.param_b, m.name))
        return out

    def get(self, name: str) -> LLMModelInfo | None:
        with self._lock:
            return self._models.get(name)

    def by_tier(self, tier: ModelTier) -> list[LLMModelInfo]:
        self.discover()
        with self._lock:
            return [m for m in self._models.values() if m.tier == tier and m.available]

    def best_for_tier(self, tier: ModelTier, *, prefer_idle: bool = True) -> LLMModelInfo | None:
        candidates = self.by_tier(tier)
        if not candidates:
            # Fall back to any adequate higher tier
            if tier == ModelTier.LIGHT:
                candidates = self.by_tier(ModelTier.STANDARD) or self.by_tier(ModelTier.STRONG)
            elif tier == ModelTier.STANDARD:
                candidates = self.by_tier(ModelTier.STRONG) or self.by_tier(ModelTier.LIGHT)
            else:
                candidates = self.by_tier(ModelTier.STANDARD)
        if not candidates:
            self.discover(force=True)
            with self._lock:
                candidates = list(self._models.values())
        if not candidates:
            return None

        def _score(m: LLMModelInfo) -> tuple:
            idle_bonus = 0 if (prefer_idle and m.is_busy) else 1
            adequate_bonus = 1 if m.adequate else 0
            fail_penalty = m.failure_rate
            latency = m.avg_latency_ms if m.avg_latency_ms > 0 else 999999
            return (adequate_bonus, idle_bonus, -fail_penalty, -latency, m.param_b)

        return max(candidates, key=_score)

    def idle_models(self) -> list[LLMModelInfo]:
        self.discover()
        with self._lock:
            return [m for m in self._models.values() if m.in_flight <= 0 and m.available]

    def acquire(self, name: str) -> bool:
        with self._lock:
            m = self._models.get(name)
            if not m:
                return False
            m.in_flight += 1
            return True

    def release(self, name: str) -> None:
        with self._lock:
            m = self._models.get(name)
            if m and m.in_flight > 0:
                m.in_flight -= 1

    def to_dict(self) -> dict[str, Any]:
        self.discover()
        with self._lock:
            return {
                "model_count": len(self._models),
                "models": [
                    {
                        "name": m.name,
                        "provider": m.provider,
                        "param_b": m.param_b,
                        "tier": m.tier.value,
                        "adequate": m.adequate,
                        "avg_latency_ms": round(m.avg_latency_ms, 1),
                        "call_count": m.call_count,
                        "failure_rate": round(m.failure_rate, 3),
                        "in_flight": m.in_flight,
                    }
                    for m in sorted(self._models.values(), key=lambda x: -x.param_b)
                ],
            }


_pool: LLMModelPool | None = None
_pool_lock = threading.Lock()


def get_model_pool() -> LLMModelPool:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = LLMModelPool()
        return _pool
