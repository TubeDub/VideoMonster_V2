"""LLM Dispatcher — the single AI-model control layer (TZ #3).

After this stage, **no module talks to a model directly**. Everything goes
through the Dispatcher:

    dispatcher.generate()   dispatcher.translate()  dispatcher.review()
    dispatcher.rewrite()    dispatcher.summary()    dispatcher.fix_json()

The Dispatcher decides *which* model to use (quality-first, §7), performs
automatic failover (§8), supports hot-swapping the model mid-film (§9),
load-balances across local models (§10), enforces resource limits (§11),
keeps per-model statistics (§12), and monitors model health (§5).

It never contains model-specific code — that lives in ``llm_adapters/``.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

from core.model_registry import (
    ModelDescriptor,
    ModelRegistry,
    ModelStatus,
    get_registry,
)
from llm_adapters import ChatRequest, ChatResult, LLMAdapter, build_adapter

logger = logging.getLogger("tubedub.llm_dispatcher")


def dispatcher_enabled() -> bool:
    """Route LLM calls through the Dispatcher. Default on (TZ #3)."""
    return str(os.getenv("VM_LLM_DISPATCHER", "1")).strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


# Task type → quality floor. Quality-critical tasks must use an adequate model.
_QUALITY_CRITICAL = {"translate", "rewrite", "review", "adapt", "naturalize"}

_TIER_RANK = {"strong": 3, "standard": 2, "cloud": 3, "light": 1}


class LLMDispatcher:
    """Single entry point for all model access."""

    def __init__(self, registry: ModelRegistry | None = None) -> None:
        self.registry = registry or get_registry()
        self._adapters: dict[str, LLMAdapter] = {}
        self._semaphores: dict[str, threading.Semaphore] = {}
        self._lock = threading.RLock()
        self._active_model: str | None = None  # hot-swap override (§9)
        self._health_thread: threading.Thread | None = None
        self._health_stop = threading.Event()
        self._initialized = False

    # ── Setup ────────────────────────────────────────────────────────

    def ensure_ready(self) -> None:
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            try:
                self.registry.discover(force=False)
            except Exception as exc:
                logger.warning("[DISPATCH] discovery failed: %s", exc)
            self._initialized = True

    def _adapter_for(self, desc: ModelDescriptor) -> LLMAdapter:
        with self._lock:
            ad = self._adapters.get(desc.name)
            if ad is None:
                ad = build_adapter(desc)
                self._adapters[desc.name] = ad
            return ad

    def _semaphore_for(self, desc: ModelDescriptor) -> threading.Semaphore:
        with self._lock:
            sem = self._semaphores.get(desc.name)
            if sem is None:
                sem = threading.Semaphore(max(1, int(desc.max_concurrency)))
                self._semaphores[desc.name] = sem
            return sem

    # ── Hot swap (§9) ────────────────────────────────────────────────

    def set_active_model(self, name: str | None) -> bool:
        """Switch model mid-processing. Remaining chunks use the new model."""
        with self._lock:
            if name is None:
                self._active_model = None
                logger.info("[DISPATCH] active model cleared (auto-select)")
                return True
            if self.registry.get(name) is None:
                self.ensure_ready()
            if self.registry.get(name) is None:
                logger.warning("[DISPATCH] set_active_model: unknown model %s", name)
                return False
            self._active_model = name
            logger.info("[DISPATCH] active model → %s (hot swap)", name)
            return True

    def active_model(self) -> str | None:
        return self._active_model

    def should_route(self) -> bool:
        """Whether the transport chokepoint should delegate selection to us.

        Cheap, no network: engage only when there is an actual decision to make —
        a hot-swapped model, a configured failover chain, or more than one model
        already in the registry. In the single-model case, "selecting" the model
        equals using it directly, so we let the direct send run (zero overhead,
        identical behavior).
        """
        if not dispatcher_enabled():
            return False
        if self._active_model:
            return True
        if self._failover_chain():
            return True
        try:
            return len(self.registry.available()) > 1
        except Exception:
            return False

    # ── Model selection (§6 + quality-first §7) ──────────────────────

    def _failover_chain(self) -> list[str]:
        raw = os.getenv("VM_LLM_FAILOVER_CHAIN", "")
        return [p.strip().lower() for p in raw.split(",") if p.strip()]

    def _candidates(self, *, task_type: str, model_hint: str | None) -> list[ModelDescriptor]:
        self.ensure_ready()
        models = [
            m
            for m in self.registry.available()
            if m.status != ModelStatus.OFFLINE
        ]
        if not models:
            return []

        critical = task_type in _QUALITY_CRITICAL
        pool = [m for m in models if m.adequate] if critical else list(models)
        if not pool:
            pool = list(models)  # never skip work; use what exists

        def sort_key(m: ModelDescriptor) -> tuple:
            # Quality-first: strongest tier first, then priority, then least loaded.
            tier_rank = _TIER_RANK.get(m.tier, 1)
            in_flight = m.stats.requests - m.stats.successes - m.stats.errors - m.stats.timeouts
            return (-tier_rank, m.priority, max(0, in_flight), m.stats.consecutive_errors)

        pool.sort(key=sort_key)

        ordered: list[ModelDescriptor] = []

        # 1. Explicit hint / hot-swapped active model wins if present.
        forced = model_hint or self._active_model or os.getenv("VM_TRANSLATE_MODEL")
        if forced:
            match = self.registry.get(forced) or next(
                (m for m in pool if forced.lower() in m.name.lower()), None
            )
            if match:
                ordered.append(match)

        # 2. Best available (quality-first).
        for m in pool:
            if m not in ordered:
                ordered.append(m)

        # 3. Configured failover chain ordering appended (§8).
        chain = self._failover_chain()
        if chain:
            def chain_rank(m: ModelDescriptor) -> int:
                for i, tok in enumerate(chain):
                    if tok in m.name.lower() or tok == m.provider.lower():
                        return i
                return len(chain)

            ordered.sort(key=chain_rank)
            # Re-pin forced model to the front regardless of chain.
            if forced and ordered and ordered[0].name != (self.registry.get(forced).name if self.registry.get(forced) else forced):
                match = self.registry.get(forced) or next(
                    (m for m in ordered if forced.lower() in m.name.lower()), None
                )
                if match:
                    ordered.remove(match)
                    ordered.insert(0, match)
        return ordered

    # ── Core execution with failover (§8) + limits (§11) ─────────────

    def execute_chat(
        self,
        prompt: str,
        *,
        task_type: str = "generate",
        system: str | None = None,
        model: str | None = None,
        max_tokens: int = 512,
        temperature: float = 0.2,
        timeout: float | None = None,
        segment: int | None = None,
        stage: str = "",
        task_id: str = "",
        allow_failover: bool = True,
        source_lang: str = "",
        target_lang: str = "",
        memory_context: str = "",
    ) -> tuple[str | None, Exception | None, dict[str, Any]]:
        """Run a chat request through the best model, with failover.

        Returns (text, error, meta) — mirrors ``_llm_chat_once`` so it drops into
        the existing transport chokepoint.
        """
        meta: dict[str, Any] = {"model": "", "provider": "", "attempts": 0, "failover": [], "cache_hit": False}

        # Semantic cache check before LLM (TZ #6 §2).
        try:
            from core.semantic_cache import semantic_cache_enabled, get_semantic_cache

            if semantic_cache_enabled():
                ctx = memory_context or (system or "")
                hit = get_semantic_cache().lookup(
                    prompt,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    context=ctx,
                    task_type=task_type,
                )
                if hit:
                    meta["cache_hit"] = True
                    meta["cache_source"] = hit.source
                    meta["cache_similarity"] = hit.similarity
                    logger.info(
                        "[DISPATCH] semantic cache %s hit (sim=%.2f) — LLM skipped",
                        hit.source, hit.similarity,
                    )
                    return hit.text, None, meta
        except Exception:
            pass

        # Inject AI Memory context into system prompt (TZ #6 §4).
        enriched_system = system
        try:
            from core.ai_memory import memory_enabled, get_memory

            if memory_enabled() and task_id:
                mem_ctx = get_memory(task_id).build_context_prompt()
                if mem_ctx:
                    enriched_system = (enriched_system or "") + "\n\n" + mem_ctx
        except Exception:
            pass

        candidates = self._candidates(task_type=task_type, model_hint=model)
        if not candidates:
            return None, RuntimeError("no_model_available"), meta

        last_err: Exception | None = None
        tried = candidates if allow_failover else candidates[:1]
        for desc in tried:
            if not self._acquire(desc, timeout):
                meta["failover"].append(f"{desc.name}:busy")
                continue
            try:
                adapter = self._adapter_for(desc)
                req = ChatRequest(
                    prompt=prompt,
                    system=enriched_system,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    timeout=timeout,
                    model=desc.name,
                    segment=segment,
                    stage=stage,
                    task_id=task_id,
                )
                result: ChatResult = adapter.generate(req)
            finally:
                self._release(desc)

            meta["attempts"] += 1
            self._record(desc, result)

            if result.ok:
                meta["model"] = result.model
                meta["provider"] = result.provider
                if meta["failover"]:
                    logger.info(
                        "[DISPATCH] %s succeeded after failover from %s",
                        result.model,
                        meta["failover"],
                    )
                # Store in semantic cache (TZ #6 §2).
                try:
                    from core.semantic_cache import semantic_cache_enabled, get_semantic_cache

                    if semantic_cache_enabled() and result.text:
                        get_semantic_cache().store(
                            prompt, result.text,
                            source_lang=source_lang, target_lang=target_lang,
                            context=memory_context or (enriched_system or ""),
                            task_type=task_type, model=result.model,
                        )
                except Exception:
                    pass
                return result.text, None, meta

            last_err = result.error or RuntimeError("empty_response")
            meta["failover"].append(f"{desc.name}:{type(last_err).__name__}")
            desc.status = ModelStatus.STALLED if result.timed_out else ModelStatus.ERROR
            logger.warning(
                "[DISPATCH] %s failed (%s) — trying next model",
                desc.name,
                last_err,
            )

        meta["model"] = candidates[0].name if candidates else ""
        return None, last_err, meta

    def _acquire(self, desc: ModelDescriptor, timeout: float | None) -> bool:
        sem = self._semaphore_for(desc)
        wait = 0.5 if desc.stats.requests == 0 else min(5.0, (timeout or 30.0) * 0.1)
        ok = sem.acquire(timeout=wait)
        if ok:
            desc.status = ModelStatus.BUSY
        return ok

    def _release(self, desc: ModelDescriptor) -> None:
        try:
            self._semaphore_for(desc).release()
        except Exception:
            pass
        if desc.status == ModelStatus.BUSY:
            desc.status = ModelStatus.READY

    def _record(self, desc: ModelDescriptor, result: ChatResult) -> None:
        desc.stats.record(
            ok=result.ok,
            latency_ms=result.latency_ms,
            gen_chars=len(result.text or ""),
            timeout=result.timed_out,
        )
        if result.ok:
            desc.status = ModelStatus.READY

    # ── Public task API (§1) ─────────────────────────────────────────

    def generate(self, prompt: str, **kwargs: Any) -> str | None:
        text, _err, _meta = self.execute_chat(prompt, task_type="generate", **kwargs)
        return text

    def translate(self, prompt: str, **kwargs: Any) -> str | None:
        text, _err, _meta = self.execute_chat(prompt, task_type="translate", **kwargs)
        return text

    def review(self, prompt: str, **kwargs: Any) -> str | None:
        text, _err, _meta = self.execute_chat(prompt, task_type="review", **kwargs)
        return text

    def rewrite(self, prompt: str, **kwargs: Any) -> str | None:
        text, _err, _meta = self.execute_chat(prompt, task_type="rewrite", **kwargs)
        return text

    def summary(self, prompt: str, **kwargs: Any) -> str | None:
        text, _err, _meta = self.execute_chat(prompt, task_type="summary", **kwargs)
        return text

    def fix_json(self, prompt: str, **kwargs: Any) -> str | None:
        kwargs.setdefault("temperature", 0.0)
        text, _err, _meta = self.execute_chat(prompt, task_type="fix_json", **kwargs)
        return text

    # ── Health monitoring (§5) ───────────────────────────────────────

    def refresh_health(self) -> dict[str, Any]:
        self.ensure_ready()
        report: dict[str, Any] = {}
        for desc in self.registry.all():
            try:
                h = self._adapter_for(desc).health()
                if h.stalled:
                    desc.status = ModelStatus.STALLED
                elif h.alive and desc.status in (ModelStatus.UNKNOWN, ModelStatus.OFFLINE):
                    desc.status = ModelStatus.READY
                elif not h.alive:
                    desc.status = ModelStatus.OFFLINE
                report[desc.name] = h.to_dict()
            except Exception as exc:
                report[desc.name] = {"alive": False, "detail": str(exc)}
        return report

    def start_health_monitor(self, interval_s: float = 5.0) -> None:
        if self._health_thread and self._health_thread.is_alive():
            return
        self._health_stop.clear()

        def _loop() -> None:
            while not self._health_stop.wait(interval_s):
                try:
                    self.refresh_health()
                except Exception:
                    pass

        self._health_thread = threading.Thread(
            target=_loop, name="llm-dispatcher-health", daemon=True
        )
        self._health_thread.start()

    def stop_health_monitor(self) -> None:
        self._health_stop.set()

    # ── Status / stats (§12) ─────────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        self.ensure_ready()
        return {
            "enabled": dispatcher_enabled(),
            "active_model": self._active_model,
            "failover_chain": self._failover_chain(),
            "models": self.registry.to_dict(),
        }


_dispatcher: LLMDispatcher | None = None
_dispatcher_lock = threading.Lock()


def get_dispatcher() -> LLMDispatcher:
    global _dispatcher
    if _dispatcher is None:
        with _dispatcher_lock:
            if _dispatcher is None:
                _dispatcher = LLMDispatcher()
    return _dispatcher
