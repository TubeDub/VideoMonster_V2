"""Strict LLM Adaptation mode + capability detection (TubeDub AI v3.2).

Single source of truth for the intelligent Translation Pipeline's adaptation
policy. Centralises:

* capability auto-detection (TZ §9): is a cloud LLM, a self-hosted LLM, and the
  rule rewrite available right now?
* provider detection (TZ §8): Ollama / LM Studio / OpenRouter / OpenAI-compatible
  are recognised without any pipeline change — only the endpoint differs.
* mode resolution (TZ §1/§2): per-job UI setting wins, then the registered
  feature flag, then (developer-only) the env override.
* stop diagnostics (TZ §7): a structured, actionable bundle saved whenever the
  strict gate blocks a run.

There are NO hidden fallbacks here: every decision is explicit and reported.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import time
import urllib.request
from typing import Any, Callable

logger = logging.getLogger("tubedub.engines.llm_adaptation_mode")

MODE_AUTOMATIC = "automatic"
MODE_STRICT = "strict"

FEATURE_ID = "strict_llm_adaptation"
ENV_KEY = "VM_STRICT_LLM_ADAPTATION"

# Bumped when the adaptation algorithm changes so cached LLM results invalidate
# (TZ §6: re-run rewrite only if text / lang / settings / algorithm change).
ADAPTATION_ALGO_VERSION = "3.2.0"

# Local LLM servers we auto-discover, in priority order (TZ §9):
#   Ollama → LM Studio → generic OpenAI-compatible (vLLM / llama.cpp / etc.)
# Each entry: (provider, host, port, openai_base, models_url, models_kind)
_LOCAL_CANDIDATES = [
    ("ollama", "127.0.0.1", 11434, "http://127.0.0.1:11434/v1", "http://127.0.0.1:11434/api/tags", "ollama"),
    ("lmstudio", "127.0.0.1", 1234, "http://127.0.0.1:1234/v1", "http://127.0.0.1:1234/v1/models", "openai"),
    ("openai-compatible", "127.0.0.1", 8000, "http://127.0.0.1:8000/v1", "http://127.0.0.1:8000/v1/models", "openai"),
    ("openai-compatible", "127.0.0.1", 8080, "http://127.0.0.1:8080/v1", "http://127.0.0.1:8080/v1/models", "openai"),
]

# Preferred chat models when auto-selecting (best multilingual instruct first).
_MODEL_PREFERENCE = (
    "deepseek", "qwen2.5", "qwen3", "qwen", "llama3.1", "llama3.2", "llama3", "llama",
    "gemma2", "gemma", "mistral", "mixtral", "phi3", "phi", "aya", "command-r",
)

# Minimum parameter size (billions) for reliable multilingual rephrase/adaptation.
# Below this, small models (e.g. qwen2.5:3b) hallucinate, drift in meaning, or leak
# foreign script — so we prefer a larger installed model and warn when only a weak
# one is available. Overridable via VM_LLM_MIN_QUALITY_B.
_ADAPT_QUALITY_FLOOR_B = 7.0

_DISCOVERY_TTL_S = 30.0
_discovery_cache: dict[str, Any] = {"ts": 0.0, "result": None}
_GPU_CACHE: bool | None = None


def _auto_discovery_disabled() -> bool:
    return str(os.getenv("VM_LLM_AUTODISCOVER") or "").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    )


def _port_open(host: str, port: int, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _http_get_json(url: str, timeout: float = 0.8) -> Any | None:
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if getattr(resp, "status", 200) != 200:
                return None
            return json.loads(resp.read().decode("utf-8", "ignore"))
    except Exception:
        return None


def _parse_models(payload: Any, kind: str) -> list[str]:
    out: list[str] = []
    if not isinstance(payload, dict):
        return out
    if kind == "ollama":
        for m in payload.get("models") or []:
            name = (m or {}).get("name") or (m or {}).get("model")
            if name:
                out.append(str(name))
    else:  # OpenAI /v1/models
        for m in payload.get("data") or []:
            mid = (m or {}).get("id")
            if mid:
                out.append(str(mid))
    return out


def discover_local_llm(force: bool = False) -> dict[str, Any] | None:
    """Probe localhost for a running local LLM server (TZ §9).

    Returns ``{"provider", "base_url", "models"}`` for the first reachable
    server, or ``None``. Result is cached briefly so the pipeline does not probe
    on every segment. No manual configuration is required — if Ollama / LM Studio
    / an OpenAI-compatible server is up, it is detected automatically.
    """
    if _auto_discovery_disabled():
        return None
    now = time.time()
    if not force and (now - float(_discovery_cache["ts"])) < _DISCOVERY_TTL_S:
        return _discovery_cache["result"]

    found: dict[str, Any] | None = None
    for provider, host, port, base, models_url, kind in _LOCAL_CANDIDATES:
        if not _port_open(host, port):
            continue
        payload = _http_get_json(models_url)
        models = _parse_models(payload, kind)
        # A reachable port with the expected API responding counts as available.
        if payload is not None or _port_open(host, port):
            found = {"provider": provider, "base_url": base, "models": models}
            logger.info(
                "[LLM] auto-discovered %s at %s (%d model(s))",
                provider,
                base,
                len(models),
            )
            break

    _discovery_cache["ts"] = now
    _discovery_cache["result"] = found
    return found


def _cloud_api_key() -> str | None:
    return (
        os.getenv("OPENAI_API_KEY")
        or os.getenv("VM_LLM_API_KEY")
        or os.getenv("VM_OPENAI_API_KEY")
    )


def resolve_llm_endpoint() -> dict[str, Any]:
    """Single resolver for the active LLM endpoint (TZ §8/§9).

    Precedence:
      1. Explicit env base URL (VM_LLM_BASE_URL / OPENAI_BASE_URL)
      2. Auto-discovered local server (Ollama → LM Studio → OpenAI-compatible)
      3. Cloud OpenAI when an API key is present
      4. Nothing available
    """
    env_base = os.getenv("VM_LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL")
    api_key = _cloud_api_key()
    if env_base:
        base = env_base.rstrip("/")
        return {
            "available": True,
            "source": "env",
            "base_url": base,
            "provider": detect_llm_provider(base),
            "api_key": api_key,
            "models": [],
        }

    disc = discover_local_llm()
    if disc:
        return {
            "available": True,
            "source": "discovered",
            "base_url": disc["base_url"].rstrip("/"),
            "provider": disc["provider"],
            "api_key": api_key,
            "models": disc.get("models") or [],
        }

    if api_key:
        return {
            "available": True,
            "source": "cloud",
            "base_url": "https://api.openai.com/v1",
            "provider": "openai",
            "api_key": api_key,
            "models": [],
        }

    return {
        "available": False,
        "source": None,
        "base_url": "",
        "provider": "none",
        "api_key": None,
        "models": [],
    }


def resolve_llm_model(models: list[str] | None = None, *, provider: str = "") -> str:
    """Pick the chat model to use (TZ §8/§9).

    Delegates to the LLM provider registry (DeepSeek → Qwen → Llama fallback).
    Honors VM_TRANSLATE_MODEL and persisted ai_module.json selection.
    """
    try:
        from engines.llm_providers.registry import _app_dir, resolve_model

        return resolve_model(
            list(models or []),
            provider=provider,
            app_dir=_app_dir(),
            env_override=os.getenv("VM_TRANSLATE_MODEL"),
        )
    except Exception:
        return _legacy_resolve_llm_model(models, provider=provider)


def _legacy_resolve_llm_model(models: list[str] | None = None, *, provider: str = "") -> str:
    """Legacy auto-select heuristics (speed/quality floor). Used as last resort."""
    env_model = os.getenv("VM_TRANSLATE_MODEL")
    available = list(models or [])
    if env_model:
        # Honor the explicit choice when the server has no model list (cloud) or
        # the model is actually installed. Otherwise it's a stale default (e.g.
        # gpt-4o-mini on Ollama) → auto-select a real local model below.
        if not available or env_model in available:
            return env_model
    if available:
        # CPU-only: default to the fastest installed model (e.g. qwen2.5:3b) so
        # adaptation finishes in minutes, not hours. Quality floor applies only
        # when VM_LLM_CPU_PREFER_SPEED=0 or a GPU is present.
        if not _has_gpu():
            if _cpu_prefer_speed():
                fastest = _smallest_model(available)
                if fastest:
                    return fastest
            responsive = _smallest_model_at_least(available, _quality_floor_b())
            if responsive:
                return responsive
            # No installed model meets the floor → use the LARGEST available
            # (best quality achievable) instead of the smallest (best effort).
            largest = _largest_model(available)
            if largest:
                return largest
        low = {m.lower(): m for m in available}
        for pref in _MODEL_PREFERENCE:
            for lname, original in low.items():
                if pref in lname:
                    return original
        return available[0]
    return env_model or "gpt-4o-mini"


def _quality_floor_b() -> float:
    """Quality floor (params in billions) for adaptation, env-overridable."""
    try:
        v = float(os.getenv("VM_LLM_MIN_QUALITY_B", "") or _ADAPT_QUALITY_FLOOR_B)
        return v if v > 0 else _ADAPT_QUALITY_FLOOR_B
    except (TypeError, ValueError):
        return _ADAPT_QUALITY_FLOOR_B


def assess_adaptation_model(model: str) -> dict[str, Any]:
    """Judge whether a chat model is strong enough for quality rephrase.

    Returns {model, param_b, adequate, warning}. Cloud/hosted models (no size in
    the name) are assumed adequate. Small local models (< floor) get a warning.
    """
    name = str(model or "")
    if not name:
        return {"model": "", "param_b": 0.0, "adequate": False, "warning": "LLM не настроена."}
    size = _model_param_billions(name)
    floor = _quality_floor_b()
    # Unknown size (cloud model, or no size tag) → assume adequate.
    if size >= 9999.0:
        return {"model": name, "param_b": 0.0, "adequate": True, "warning": ""}
    adequate = size >= floor
    warning = ""
    if not adequate:
        warning = (
            f"Локальная модель «{name}» (~{size:g}B) слишком мала для качественной "
            f"украинской адаптации — рекомендуется модель ≥{floor:g}B "
            f"(например, qwen2.5:14b, gemma2:9b, llama3.1:8b) или облачная модель "
            f"через OPENAI_API_KEY. Слабая модель может искажать смысл или "
            f"смешивать языки."
        )
    return {"model": name, "param_b": size, "adequate": adequate, "warning": warning}


def _smallest_model(models: list[str]) -> str | None:
    """Smallest installed model by parameter count (fastest on CPU)."""
    sized = [(m, _model_param_billions(m)) for m in models if m]
    known = [(m, b) for m, b in sized if 0.0 < b < 9999.0]
    if known:
        known.sort(key=lambda x: x[1])
        return known[0][0]
    return models[0] if models else None


def _cpu_prefer_speed() -> bool:
    """On CPU-only machines, prefer the fastest local model unless disabled."""
    env = str(os.getenv("VM_LLM_CPU_PREFER_SPEED") or "").strip().lower()
    if env in ("0", "false", "no", "off"):
        return False
    if env in ("1", "true", "yes", "on"):
        return True
    return not _has_gpu()


def resolve_default_adaptation_speed_mode() -> str:
    """Default dub adaptation speed from Settings → AI Module quality mode."""
    try:
        from engines.translation_adapt import MODE_BALANCED, MODE_FAST, MODE_MAX_QUALITY, normalize_speed_mode

        env_mode = os.getenv("VM_ADAPTATION_SPEED_MODE") or os.getenv("VM_ADAPT_MODE")
        if env_mode:
            return normalize_speed_mode(env_mode)
        from engines.llm_providers.registry import load_quality_mode

        return normalize_speed_mode(load_quality_mode())
    except Exception:
        return MODE_MAX_QUALITY


def _smallest_model_at_least(models: list[str], floor_b: float) -> str | None:
    """Smallest installed model whose size is >= floor_b (None if none qualify)."""
    sized = [(m, _model_param_billions(m)) for m in models if m]
    qualifying = [(m, b) for m, b in sized if 0.0 < b < 9999.0 and b >= floor_b]
    if not qualifying:
        return None
    qualifying.sort(key=lambda x: x[1])
    return qualifying[0][0]


def _largest_model(models: list[str]) -> str | None:
    sized = [(m, _model_param_billions(m)) for m in models if m]
    known = [(m, b) for m, b in sized if b < 9999.0]
    if not known:
        return None
    known.sort(key=lambda x: x[1], reverse=True)
    return known[0][0]


def _has_gpu() -> bool:
    """Best-effort GPU detection (NVIDIA CUDA). Cached per process."""
    global _GPU_CACHE
    if _GPU_CACHE is not None:
        return _GPU_CACHE
    found = False
    try:
        import torch  # type: ignore

        found = bool(torch.cuda.is_available())
    except Exception:
        found = False
    if not found:
        import shutil as _sh

        found = _sh.which("nvidia-smi") is not None
    _GPU_CACHE = found
    return found


def _model_param_billions(name: str) -> float:
    """Parse the parameter size (in billions) from an Ollama model tag."""
    import re as _re

    m = _re.search(r"(\d+(?:\.\d+)?)\s*b\b", str(name).lower())
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return 9999.0
    return 9999.0  # unknown size → treat as large so it loses to a sized model


def normalize_mode(value: Any) -> str:
    """Coerce any UI/setting value into 'automatic' or 'strict'."""
    if value is True:
        return MODE_STRICT
    if value is False or value is None:
        return MODE_AUTOMATIC
    s = str(value).strip().lower()
    if s in ("strict", "strict_mode", "hard", "enforce", "1", "true", "yes", "on"):
        return MODE_STRICT
    return MODE_AUTOMATIC


def detect_llm_provider(base_url: str | None) -> str:
    """Best-effort provider label from the configured base URL (TZ §8)."""
    b = (base_url or "").strip().lower()
    if not b or "api.openai.com" in b:
        return "openai" if b else "none"
    if "openrouter" in b:
        return "openrouter"
    if "11434" in b or "ollama" in b:
        return "ollama"
    if "1234" in b or "lmstudio" in b or "lm-studio" in b or "lm_studio" in b:
        return "lmstudio"
    if "anthropic" in b:
        return "anthropic-compatible"
    return "openai-compatible"


def detect_capabilities() -> dict[str, Any]:
    """Auto-detect which adaptation backends are usable right now (TZ §9).

    Rule rewrite is always available (pure-Python). LLM availability is resolved
    via env → auto-discovered local server (Ollama/LM Studio/OpenAI-compatible)
    → cloud key, with zero manual configuration required.
    """
    cloud_key = bool(_cloud_api_key())
    ep = resolve_llm_endpoint()
    llm_available = bool(ep.get("available"))
    model = resolve_llm_model(ep.get("models"), provider=ep.get("provider", "")) if llm_available else ""
    assessment = assess_adaptation_model(model) if llm_available else {
        "param_b": 0.0, "adequate": False, "warning": ""
    }

    model_adequate = bool(assessment.get("adequate", True)) if llm_available else False
    is_cloud = ep.get("provider") == "openai" or bool(cloud_key)
    # Recommend cloud when there's no LLM at all, or the local model is too weak
    # and no cloud key is configured yet.
    recommend_cloud = (not llm_available) or (not model_adequate and not cloud_key)
    cloud_hint = ""
    if recommend_cloud:
        cloud_hint = (
            "Для качественной адаптации задайте OPENAI_API_KEY (облачная модель) "
            "или используйте локальную модель ≥7B (qwen2.5:14b, gemma2:9b, "
            "llama3.1:8b) либо GPU."
        )

    return {
        "llm_available": llm_available,
        "cloud_api_available": cloud_key,
        "local_llm_available": ep.get("source") in ("env", "discovered") and ep.get("provider") != "openai",
        "rule_rewrite_available": True,
        "provider": ep.get("provider"),
        "is_cloud": is_cloud,
        "model": model,
        "model_param_b": assessment.get("param_b", 0.0),
        "model_adequate": model_adequate,
        "model_warning": assessment.get("warning", ""),
        "recommend_cloud": recommend_cloud,
        "cloud_hint": cloud_hint,
        "base_url": ep.get("base_url"),
        "source": ep.get("source"),
        "available_models": ep.get("models") or [],
    }


def recommended_mode(capabilities: dict[str, Any] | None = None) -> str:
    """Pick the best *default* mode for the current environment (TZ §9).

    Automatic is always the safe recommendation: it uses full LLM rewrite when an
    LLM is available and degrades to rule rewrite with an explicit warning
    otherwise — never a silent quality drop, never a hard crash.
    """
    return MODE_AUTOMATIC


def _feature_flag_strict() -> bool:
    """Strict via the registered feature flag (honors ENV_KEY override)."""
    try:
        from engines.core.feature_flags import is_enabled

        return bool(is_enabled(FEATURE_ID, developer_session=False))
    except Exception:
        # Fall back to a raw env read so developers still have an escape hatch.
        raw = str(os.getenv(ENV_KEY) or "").strip().lower()
        return raw in ("1", "true", "yes", "on")


def resolve_adaptation_mode(
    task_info: dict[str, Any] | None,
    *,
    feature_flag_fn: Callable[[], bool] | None = None,
) -> str:
    """Resolve the effective mode for a job (TZ §1/§2).

    Precedence (highest first):
      1. Per-job UI setting ``task_info['strict_llm_adaptation']`` (automatic/strict)
      2. Registered feature flag ``strict_llm_adaptation`` (env override allowed)
      3. Default: automatic
    """
    info = task_info or {}
    if "strict_llm_adaptation" in info and info.get("strict_llm_adaptation") not in (None, ""):
        return normalize_mode(info.get("strict_llm_adaptation"))
    flag_fn = feature_flag_fn or _feature_flag_strict
    try:
        return MODE_STRICT if flag_fn() else MODE_AUTOMATIC
    except Exception:
        return MODE_AUTOMATIC


def build_stop_diagnostics(
    *,
    mode: str,
    reason: str,
    pending_indices: list[int],
    total_segments: int,
    capabilities: dict[str, Any] | None = None,
    problem_segments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Structured diagnostics persisted whenever the pipeline stops (TZ §7)."""
    cap = capabilities or detect_capabilities()
    recommendations: list[str] = []
    if not cap.get("llm_available"):
        recommendations.append(
            "Установите AI-модуль TubeDub в Настройках → AI Module "
            "для интеллектуальной адаптации текста."
        )
        recommendations.append(
            "Либо переключите режим на «Автоматический», чтобы продолжить дубляж "
            "с упрощённой адаптацией и предупреждением о качестве."
        )
    else:
        recommendations.append(
            "AI-модуль установлен, но не смогла адаптировать сегмент(ы) без потери смысла. "
            "Попробуйте обновить модель в настройках AI Module или ослабьте тайминг "
            "сегмента (склейка/разбиение реплики)."
        )
    return {
        "mode": mode,
        "reason": reason,
        "strict_gate_activated": mode == MODE_STRICT,
        "llm_available": bool(cap.get("llm_available")),
        "llm_provider": cap.get("provider"),
        "llm_model": cap.get("model"),
        "llm_base_url": cap.get("base_url"),
        "rule_rewrite_available": bool(cap.get("rule_rewrite_available")),
        "total_segments": int(total_segments),
        "requires_llm_count": len(pending_indices),
        "problem_segment_indices": list(pending_indices),
        "problem_segments": problem_segments or [],
        "recommendations": recommendations,
    }
