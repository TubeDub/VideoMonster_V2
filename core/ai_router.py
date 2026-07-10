"""Unified AI Router — single entry for Local / My API / TubeDub Cloud / Future.

All providers speak through the same interface. The Router never forces a
paywall and never auto-downloads models (Production TZ §§1,3,6,11,21).
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Any

from core.ai_sources import (
    AISourceMode,
    AISourcesConfig,
    QUALITY_PRIORITY,
    QualityMode,
    get_ai_sources,
    recommend_local_model,
)

logger = logging.getLogger("tubedub.ai_router")


@dataclass
class RouteDecision:
    source: str
    provider: str
    model: str
    base_url: str
    api_key: str = ""
    kind: str = "local"  # local | user_api | cloud | none
    reason: str = ""
    fallback_chain: list[str] = field(default_factory=list)
    free: bool = True
    available: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "kind": self.kind,
            "reason": self.reason,
            "fallback_chain": self.fallback_chain,
            "free": self.free,
            "available": self.available,
            "has_api_key": bool(self.api_key),
        }


class AIRouter:
    """Intelligent source + model router (TZ §11)."""

    def __init__(self, app_dir: str | None = None) -> None:
        self.app_dir = app_dir
        self._lock = threading.RLock()
        self._last: RouteDecision | None = None

    def config(self) -> AISourcesConfig:
        return get_ai_sources(self.app_dir).get()

    def recommend_model(self) -> dict[str, Any]:
        """Hardware → suggested local model. Never downloads (TZ §5–6)."""
        vram = 0.0
        has_gpu = False
        try:
            from core.hardware_profiler import get_hardware_profile

            hw = get_hardware_profile()
            gpu = hw.gpu if hasattr(hw, "gpu") else None
            if gpu is not None:
                vram = float(getattr(gpu, "vram_gb", 0) or 0)
                has_gpu = bool(
                    getattr(gpu, "available", False)
                    or getattr(gpu, "cuda", False)
                    or getattr(gpu, "rocm", False)
                    or getattr(gpu, "directml", False)
                )
        except Exception as exc:
            logger.debug("[AI_ROUTER] hardware probe: %s", exc)
        rec = recommend_local_model(vram_gb=vram, has_gpu=has_gpu)
        rec["priority_chain"] = list(QUALITY_PRIORITY)
        return rec

    def discover_local(self) -> dict[str, Any]:
        try:
            from engines.llm_adaptation_mode import resolve_llm_endpoint

            ep = resolve_llm_endpoint()
            return {
                "available": bool(ep.get("available")),
                "provider": str(ep.get("provider") or ""),
                "base_url": str(ep.get("base_url") or ""),
                "models": list(ep.get("models") or []),
                "api_key": str(ep.get("api_key") or ""),
            }
        except Exception as exc:
            logger.debug("[AI_ROUTER] local discover: %s", exc)
            return {"available": False, "provider": "", "base_url": "", "models": []}

    def _pick_from_priority(self, available: list[str], preferred: str = "") -> str:
        avail_l = {m.lower(): m for m in available if m}
        if preferred:
            for k, v in avail_l.items():
                if preferred.lower() in k or k in preferred.lower():
                    return v
        for tag in QUALITY_PRIORITY:
            for k, v in avail_l.items():
                if tag.lower() in k or k.startswith(tag.lower().split(":")[0]):
                    return v
        return available[0] if available else preferred or ""

    def route(self, *, task: str = "translate") -> RouteDecision:
        """Pick best available source without user reconfiguration (TZ §11)."""
        cfg = self.config()
        preferred = cfg.source_mode
        chain: list[str] = []
        order = [preferred]
        for m in (
            AISourceMode.LOCAL.value,
            AISourceMode.USER_API.value,
            AISourceMode.TUBEDUB_CLOUD.value,
            AISourceMode.FUTURE.value,
        ):
            if m not in order:
                order.append(m)

        for source in order:
            chain.append(source)
            decision = self._try_source(source, cfg)
            if decision and decision.available:
                decision.fallback_chain = chain
                decision.reason = decision.reason or f"selected source={source} task={task}"
                with self._lock:
                    self._last = decision
                return decision

        # Graceful MT-only path — still free, no paywall (TZ §3, §21).
        none = RouteDecision(
            source=preferred,
            provider="none",
            model="",
            base_url="",
            kind="none",
            reason="No LLM available — Marian/Argos MT only (free, no paywall)",
            fallback_chain=chain,
            free=True,
            available=False,
        )
        with self._lock:
            self._last = none
        return none

    def _try_source(self, source: str, cfg: AISourcesConfig) -> RouteDecision | None:
        if source == AISourceMode.LOCAL.value:
            local = self.discover_local()
            if cfg.local.base_url and not local.get("available"):
                # Prefer user-configured local base URL.
                local = {
                    "available": True,
                    "provider": cfg.local.provider or "ollama",
                    "base_url": cfg.local.base_url,
                    "models": [cfg.local.model] if cfg.local.model else [],
                    "api_key": "",
                }
            if not local.get("available") and not cfg.local.model:
                return RouteDecision(
                    source=source,
                    provider=cfg.local.provider or "ollama",
                    model=cfg.local.model or "",
                    base_url=cfg.local.base_url or "",
                    kind="local",
                    reason="Local AI not detected",
                    free=True,
                    available=False,
                )
            models = list(local.get("models") or [])
            model = cfg.local.model or self._pick_from_priority(models)
            if not model and models:
                model = models[0]
            return RouteDecision(
                source=source,
                provider=str(local.get("provider") or cfg.local.provider or "ollama"),
                model=model,
                base_url=str(local.get("base_url") or cfg.local.base_url or ""),
                api_key=str(local.get("api_key") or ""),
                kind="local",
                reason="Local AI (free)",
                free=True,
                available=bool(model or local.get("available")),
            )

        if source == AISourceMode.USER_API.value:
            ua = cfg.user_api
            key = ua.api_key or os.getenv("OPENAI_API_KEY") or os.getenv("VM_LLM_API_KEY") or ""
            # Also accept provider-specific env keys without forcing paywall.
            if not key:
                if ua.provider == "anthropic":
                    key = os.getenv("ANTHROPIC_API_KEY") or ""
                elif ua.provider == "openrouter":
                    key = os.getenv("OPENROUTER_API_KEY") or ""
                elif ua.provider == "github":
                    key = (
                        os.getenv("GITHUB_MODELS_TOKEN")
                        or os.getenv("GITHUB_TOKEN")
                        or ""
                    )
            base = ua.base_url
            if not base and key:
                from core.ai_sources import _default_base_for_provider

                base = _default_base_for_provider(ua.provider)
            # Require an API key (or explicit custom base+key). Bare default URL ≠ configured.
            if not key:
                return RouteDecision(
                    source=source,
                    provider=ua.provider,
                    model=ua.model,
                    base_url=base or "",
                    kind="user_api",
                    reason="My API not configured",
                    free=False,
                    available=False,
                )
            if not base:
                from core.ai_sources import _default_base_for_provider

                base = _default_base_for_provider(ua.provider)
            return RouteDecision(
                source=source,
                provider=ua.provider,
                model=ua.model,
                base_url=base,
                api_key=key,
                kind="user_api",
                reason="User API",
                free=False,
                available=True,
            )

        if source == AISourceMode.TUBEDUB_CLOUD.value:
            tc = cfg.tubedub_cloud
            url = tc.base_url or os.getenv("VM_TUBEDUB_CLOUD_URL") or ""
            key = tc.api_key or ""
            if not tc.enabled or not url:
                return RouteDecision(
                    source=source,
                    provider="tubedub_cloud",
                    model=tc.model or "",
                    base_url=url,
                    kind="cloud",
                    reason="TubeDub Cloud not configured (optional)",
                    free=False,
                    available=False,
                )
            base = url.rstrip("/") + "/v1"
            return RouteDecision(
                source=source,
                provider="tubedub_cloud",
                model=tc.model or "tubedub-default",
                base_url=base,
                api_key=key,
                kind="cloud",
                reason="TubeDub Cloud",
                free=False,
                available=True,
            )

        if source == AISourceMode.FUTURE.value:
            return RouteDecision(
                source=source,
                provider="future",
                model="",
                base_url="",
                kind="none",
                reason="Future AI extension point — not yet available",
                free=True,
                available=False,
            )
        return None

    def apply_route(self, decision: RouteDecision | None = None) -> RouteDecision:
        """Apply routed credentials to env so legacy stack follows the router."""
        d = decision or self.route()
        store = get_ai_sources(self.app_dir)
        # Ensure quality mode env is set.
        store.apply_to_env()
        if d.available:
            if d.model:
                os.environ["VM_TRANSLATE_MODEL"] = d.model
            if d.base_url:
                os.environ["VM_LLM_BASE_URL"] = d.base_url
                if d.kind in ("user_api", "cloud"):
                    os.environ["OPENAI_BASE_URL"] = d.base_url
            if d.api_key:
                os.environ["VM_LLM_API_KEY"] = d.api_key
                if d.provider in ("openai", "openrouter", "github", "tubedub_cloud", "custom"):
                    os.environ["OPENAI_API_KEY"] = d.api_key
                if d.provider == "anthropic":
                    os.environ["ANTHROPIC_API_KEY"] = d.api_key
                if d.provider == "openrouter":
                    os.environ["OPENROUTER_API_KEY"] = d.api_key
                if d.provider == "github":
                    os.environ["GITHUB_TOKEN"] = d.api_key
        os.environ["VM_AI_SOURCE_MODE"] = d.source
        os.environ["VM_AI_ROUTE_PROVIDER"] = d.provider
        return d

    def status(self) -> dict[str, Any]:
        cfg = self.config()
        local = self.discover_local()
        decision = self.route()
        rec = self.recommend_model()
        return {
            "source_mode": cfg.source_mode,
            "quality_mode": cfg.quality_mode,
            "local": local,
            "recommended_local_model": rec,
            "active_route": decision.to_dict(),
            "sources": cfg.to_dict(),
            "providers": list_supported_providers(),
            "policy": {
                "local_always_free": True,
                "no_paywall": True,
                "no_auto_download": True,
                "installer_size_unaffected": True,
            },
            "first_run_prompt_needed": (
                not cfg.first_run_prompt_done and not local.get("available")
            ),
        }


def list_supported_providers() -> list[dict[str, Any]]:
    """Unified provider catalog (TZ §2). Extensible without core rewrites."""
    return [
        {"id": "ollama", "kind": "local", "label": "Ollama", "free": True},
        {"id": "lmstudio", "kind": "local", "label": "LM Studio", "free": True},
        {"id": "vllm", "kind": "local", "label": "vLLM", "free": True},
        {"id": "openai", "kind": "cloud", "label": "OpenAI API", "free": False},
        {"id": "anthropic", "kind": "cloud", "label": "Anthropic API", "free": False},
        {"id": "openrouter", "kind": "cloud", "label": "OpenRouter", "free": False},
        {"id": "github", "kind": "cloud", "label": "GitHub Models", "free": False},
        {"id": "tubedub_cloud", "kind": "cloud", "label": "TubeDub Cloud", "free": False},
        {"id": "future", "kind": "future", "label": "Future AI", "free": True},
    ]


_router: AIRouter | None = None
_router_lock = threading.Lock()


def get_ai_router(*, app_dir: str | None = None) -> AIRouter:
    global _router
    if _router is None:
        with _router_lock:
            if _router is None:
                _router = AIRouter(app_dir=app_dir)
    return _router


def reset_ai_router() -> None:
    global _router
    with _router_lock:
        _router = None
