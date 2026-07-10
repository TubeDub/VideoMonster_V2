"""OpenAI-compatible adapter (TZ #3 §3/§4).

Covers every backend that speaks the OpenAI ``/chat/completions`` protocol:
Ollama, Qwen, DeepSeek, Gemma, Llama, Mistral, vLLM, LM Studio, OpenAI, OpenRouter.

The actual HTTP send is delegated to ``translation_adapt._raw_chat_send`` so the
byte-for-byte request format, headers and parsing stay identical to the current
pipeline (no quality/behavior change — only routing moves here).
"""

from __future__ import annotations

import time

from llm_adapters.base import ChatRequest, ChatResult, HealthReport, LLMAdapter


class OpenAICompatibleAdapter(LLMAdapter):
    adapter_id = "openai_compatible"

    def connect(self) -> bool:
        try:
            from engines.translation_adapt import llm_rephrase_available

            self._connected = bool(llm_rephrase_available())
        except Exception:
            self._connected = False
        return self._connected

    def generate(self, request: ChatRequest) -> ChatResult:
        model = request.model or self.descriptor.name
        provider = self.descriptor.provider
        t0 = time.monotonic()
        try:
            from engines.translation_adapt import _raw_chat_send

            text, finish, err = _raw_chat_send(
                request.prompt,
                model=model,
                system=request.system,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
                timeout=request.timeout,
                endpoint_base=self.descriptor.endpoint_base or None,
            )
        except Exception as exc:  # transport helper missing / import error
            text, finish, err = None, "error", exc

        latency_ms = (time.monotonic() - t0) * 1000.0
        timed_out = isinstance(err, TimeoutError) or (
            err is not None and "timed out" in str(err).lower()
        )
        return ChatResult(
            text=text,
            error=err,
            model=model,
            provider=provider,
            latency_ms=latency_ms,
            finish_reason=finish,
            tokens_out=self.estimate_tokens(text or ""),
            timed_out=timed_out,
        )

    def health(self) -> HealthReport:
        stats = self.descriptor.stats
        alive = self.connect()
        gpu = False
        try:
            from core.resource_monitor import ResourceMonitor

            gpu = ResourceMonitor().sample().gpu_available
        except Exception:
            pass
        stalled = stats.consecutive_errors >= 3
        return HealthReport(
            alive=alive and not stalled,
            stalled=stalled,
            last_latency_ms=stats.last_latency_ms,
            avg_latency_ms=stats.avg_latency_ms,
            error_count=stats.errors + stats.timeouts,
            gpu_available=gpu,
            network_available=True,
            detail=self.descriptor.provider,
        )
