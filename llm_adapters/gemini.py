"""Google Gemini adapter (TZ #3 §3/§4).

Prepared adapter for the Gemini ``generateContent`` REST API. Activates only
when a Gemini/Google API key is present. Self-contained urllib implementation.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request

from llm_adapters.base import ChatRequest, ChatResult, HealthReport, LLMAdapter

_API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"


def _api_key() -> str | None:
    return (
        os.getenv("GEMINI_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
        or os.getenv("VM_GEMINI_API_KEY")
    )


class GeminiAdapter(LLMAdapter):
    adapter_id = "gemini"

    def connect(self) -> bool:
        self._connected = bool(_api_key())
        return self._connected

    def generate(self, request: ChatRequest) -> ChatResult:
        model = request.model or self.descriptor.name
        key = _api_key()
        t0 = time.monotonic()
        if not key:
            return ChatResult(
                error=RuntimeError("gemini_no_api_key"), model=model, provider="gemini"
            )
        try:
            url = f"{_API_ROOT}/{model}:generateContent?key={key}"
            contents = []
            if request.system:
                contents.append({"role": "user", "parts": [{"text": request.system}]})
            contents.append({"role": "user", "parts": [{"text": request.prompt}]})
            payload = {
                "contents": contents,
                "generationConfig": {
                    "temperature": request.temperature,
                    "maxOutputTokens": request.max_tokens,
                },
            }
            body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url, data=body, headers={"Content-Type": "application/json"}, method="POST"
            )
            timeout = request.timeout if (request.timeout and request.timeout > 0) else 120.0
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            candidates = data.get("candidates") or []
            text = ""
            finish = "stop"
            if candidates:
                first = candidates[0]
                finish = str(first.get("finishReason") or "stop").lower()
                parts = (first.get("content") or {}).get("parts") or []
                text = "".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()
            latency_ms = (time.monotonic() - t0) * 1000.0
            if finish == "max_tokens":
                return ChatResult(
                    error=ValueError("token_limit"), model=model,
                    provider="gemini", latency_ms=latency_ms, finish_reason=finish,
                )
            return ChatResult(
                text=text or None, model=model, provider="gemini",
                latency_ms=latency_ms, finish_reason=finish,
                tokens_out=self.estimate_tokens(text),
            )
        except Exception as exc:
            latency_ms = (time.monotonic() - t0) * 1000.0
            timed_out = "timed out" in str(exc).lower()
            return ChatResult(
                error=exc, model=model, provider="gemini",
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
            detail="gemini" if alive else "no_api_key",
        )
