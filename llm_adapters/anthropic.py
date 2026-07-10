"""Anthropic (Claude) adapter (TZ #3 §3/§4).

Prepared adapter for the Claude Messages API. Activates only when an Anthropic
API key is present; otherwise ``connect()`` / ``health()`` report unavailable so
the Dispatcher simply skips it. Self-contained urllib implementation — no SDK.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request

from llm_adapters.base import ChatRequest, ChatResult, HealthReport, LLMAdapter

_API_URL = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"


def _api_key() -> str | None:
    return os.getenv("ANTHROPIC_API_KEY") or os.getenv("VM_ANTHROPIC_API_KEY")


class AnthropicAdapter(LLMAdapter):
    adapter_id = "anthropic"

    def connect(self) -> bool:
        self._connected = bool(_api_key())
        return self._connected

    def generate(self, request: ChatRequest) -> ChatResult:
        model = request.model or self.descriptor.name
        key = _api_key()
        t0 = time.monotonic()
        if not key:
            return ChatResult(
                error=RuntimeError("anthropic_no_api_key"),
                model=model,
                provider="anthropic",
            )
        try:
            headers = {
                "Content-Type": "application/json",
                "x-api-key": key,
                "anthropic-version": _API_VERSION,
            }
            payload: dict = {
                "model": model,
                "max_tokens": request.max_tokens,
                "temperature": request.temperature,
                "messages": [{"role": "user", "content": request.prompt}],
            }
            if request.system:
                payload["system"] = request.system
            body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(_API_URL, data=body, headers=headers, method="POST")
            timeout = request.timeout if (request.timeout and request.timeout > 0) else 120.0
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            parts = data.get("content") or []
            text = "".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()
            finish = str(data.get("stop_reason") or "stop")
            latency_ms = (time.monotonic() - t0) * 1000.0
            if finish == "max_tokens":
                return ChatResult(
                    error=ValueError("token_limit"), model=model,
                    provider="anthropic", latency_ms=latency_ms, finish_reason=finish,
                )
            return ChatResult(
                text=text or None, model=model, provider="anthropic",
                latency_ms=latency_ms, finish_reason=finish,
                tokens_out=self.estimate_tokens(text),
            )
        except Exception as exc:
            latency_ms = (time.monotonic() - t0) * 1000.0
            timed_out = "timed out" in str(exc).lower()
            return ChatResult(
                error=exc, model=model, provider="anthropic",
                latency_ms=latency_ms, timed_out=timed_out,
            )

    def health(self) -> HealthReport:
        stats = self.descriptor.stats
        alive = self.connect()
        return HealthReport(
            alive=alive,
            stalled=stats.consecutive_errors >= 3,
            last_latency_ms=stats.last_latency_ms,
            avg_latency_ms=stats.avg_latency_ms,
            error_count=stats.errors + stats.timeouts,
            network_available=alive,
            detail="anthropic" if alive else "no_api_key",
        )
