"""Benchmark AI — compare available models on one sample (Production TZ §18).

Never runs on the critical dubbing path. Never auto-downloads models.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("tubedub.ai_benchmark")

DEFAULT_SAMPLE = (
    "An 18-year-old boy named George Jr. drove through his hometown "
    "on his way home for dinner."
)


@dataclass
class ModelBenchResult:
    model: str
    provider: str
    source: str
    ok: bool
    quality: float = 0.0
    latency_ms: float = 0.0
    cost_est: float = 0.0
    memory_mb: float = 0.0
    output: str = ""
    error: str = ""
    dimensions: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "provider": self.provider,
            "source": self.source,
            "ok": self.ok,
            "quality": round(self.quality, 2),
            "latency_ms": round(self.latency_ms, 1),
            "cost_est": round(self.cost_est, 6),
            "memory_mb": round(self.memory_mb, 1),
            "output": self.output,
            "error": self.error,
            "dimensions": self.dimensions,
        }


def _cost_estimate(provider: str, chars: int) -> float:
    # Rough public list-price proxies; local = 0.
    if provider in ("ollama", "lmstudio", "vllm", "local", ""):
        return 0.0
    per_1k = {
        "openai": 0.00015,
        "openrouter": 0.00015,
        "anthropic": 0.003,
        "github": 0.0,
        "tubedub_cloud": 0.0,
    }.get(provider, 0.0002)
    return (chars / 1000.0) * per_1k


def run_ai_benchmark(
    *,
    sample: str = DEFAULT_SAMPLE,
    src_lang: str = "en",
    tgt_lang: str = "uk",
    models: list[dict[str, Any]] | None = None,
    translate_fn: Any = None,
) -> dict[str, Any]:
    """Benchmark listed models. If models is None, discover from AI Router."""
    from core.ai_router import get_ai_router
    from engines.quality_score_v2 import compute_quality_score_v2

    router = get_ai_router()
    candidates: list[dict[str, Any]] = list(models or [])
    if not candidates:
        st = router.status()
        local = st.get("local") or {}
        for m in (local.get("models") or [])[:5]:
            candidates.append(
                {
                    "model": m,
                    "provider": local.get("provider") or "ollama",
                    "source": "local",
                    "base_url": local.get("base_url") or "",
                }
            )
        src = st.get("sources") or {}
        ua = src.get("user_api") or {}
        if src.get("user_api_configured"):
            candidates.append(
                {
                    "model": ua.get("model") or "gpt-4o-mini",
                    "provider": ua.get("provider") or "openai",
                    "source": "user_api",
                    "base_url": ua.get("base_url") or "",
                }
            )

    results: list[ModelBenchResult] = []
    for cand in candidates:
        model = str(cand.get("model") or "")
        provider = str(cand.get("provider") or "")
        source = str(cand.get("source") or "")
        t0 = time.perf_counter()
        out = ""
        err = ""
        ok = False
        try:
            if translate_fn:
                out = str(
                    translate_fn(
                        sample,
                        model=model,
                        provider=provider,
                        base_url=cand.get("base_url") or "",
                        tgt_lang=tgt_lang,
                        src_lang=src_lang,
                    )
                    or ""
                )
            else:
                # Best-effort via adaptation transport when available.
                from engines.llm_providers.transport import chat_completion

                system = (
                    f"Translate from {src_lang} to {tgt_lang} for film dubbing. "
                    "Preserve names and facts. Output only the translation."
                )
                out = chat_completion(
                    sample,
                    system=system,
                    model=model,
                    max_tokens=256,
                    temperature=0.2,
                    timeout=90.0,
                    transport={
                        "kind": "local" if source == "local" else "cloud",
                        "base_url": cand.get("base_url") or "",
                        "provider": provider,
                        "model": model,
                        "api_key": cand.get("api_key") or "",
                    },
                ) or ""
            ok = bool(out.strip())
        except Exception as exc:
            err = str(exc)
        ms = (time.perf_counter() - t0) * 1000.0
        quality = 0.0
        dims: dict[str, Any] = {}
        if out:
            quality, det = compute_quality_score_v2(
                sample, out, src_lang=src_lang, tgt_lang=tgt_lang
            )
            dims = det.get("dimensions") or {}
        results.append(
            ModelBenchResult(
                model=model,
                provider=provider,
                source=source,
                ok=ok,
                quality=quality,
                latency_ms=ms,
                cost_est=_cost_estimate(provider, len(out)),
                output=out,
                error=err,
                dimensions=dims,
            )
        )

    ranked = sorted(
        results,
        key=lambda r: (r.ok, r.quality, -r.latency_ms),
        reverse=True,
    )
    recommended = ranked[0].to_dict() if ranked and ranked[0].ok else None
    return {
        "sample": sample,
        "src_lang": src_lang,
        "tgt_lang": tgt_lang,
        "results": [r.to_dict() for r in ranked],
        "recommended": recommended,
        "policy": {"auto_download": False, "critical_path": False},
    }
