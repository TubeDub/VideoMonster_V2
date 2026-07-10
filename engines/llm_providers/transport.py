"""LLM transport layer — cloud-ready OpenAI-compatible backend (TZ #1 §9)."""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from typing import Any

logger = logging.getLogger("tubedub.llm_providers.transport")

_CLOUD_PROFILES: dict[str, dict[str, str]] = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "default_model": "gpt-4o-mini",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "default_model": "openai/gpt-4o-mini",
    },
    "deepseek_api": {
        "base_url": "https://api.deepseek.com/v1",
        "api_key_env": "DEEPSEEK_API_KEY",
        "default_model": "deepseek-chat",
    },
    "github": {
        "base_url": "https://models.inference.ai.azure.com",
        "api_key_env": "GITHUB_MODELS_TOKEN",
        "default_model": "gpt-4o-mini",
        "alt_key_env": "GITHUB_TOKEN",
    },
    "anthropic_compat": {
        "base_url": "https://api.anthropic.com",
        "api_key_env": "ANTHROPIC_API_KEY",
        "default_model": "claude-3-5-sonnet-latest",
    },
}


def _profile_key(prof: dict[str, str]) -> str:
    key = os.getenv(prof.get("api_key_env") or "") or ""
    if not key and prof.get("alt_key_env"):
        key = os.getenv(prof["alt_key_env"]) or ""
    return key


def list_cloud_profiles() -> list[dict[str, str]]:
    out = []
    for pid, prof in _CLOUD_PROFILES.items():
        key = _profile_key(prof)
        out.append(
            {
                "id": pid,
                "base_url": prof["base_url"],
                "default_model": prof["default_model"],
                "configured": bool(key),
            }
        )
    return out


def resolve_transport() -> dict[str, Any]:
    """Active transport: AI Router source mode first, then discovery / cloud."""
    # Prefer Production AI Router when configured (never blocks free local MT).
    try:
        mode = (os.getenv("VM_AI_SOURCE_MODE") or "").strip().lower()
        if mode in ("user_api", "tubedub_cloud"):
            base = os.getenv("VM_LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL") or ""
            key = os.getenv("VM_LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
            model = os.getenv("VM_TRANSLATE_MODEL") or os.getenv("VM_OPENAI_MODEL") or ""
            if base or key:
                return {
                    "kind": "cloud" if mode != "local" else "local",
                    "base_url": base,
                    "provider": os.getenv("VM_AI_ROUTE_PROVIDER") or mode,
                    "model": model,
                    "api_key": key,
                }
        if mode == "local" or not mode:
            from engines.llm_adaptation_mode import resolve_llm_endpoint, resolve_llm_model

            ep = resolve_llm_endpoint()
            if ep.get("available"):
                return {
                    "kind": "local",
                    "base_url": ep.get("base_url"),
                    "provider": ep.get("provider"),
                    "model": resolve_llm_model(
                        ep.get("models"), provider=ep.get("provider", "")
                    ),
                    "api_key": ep.get("api_key"),
                }
            if mode == "local":
                # Explicit local — do not silently fall through to paid API.
                return {"kind": "none", "provider": "none", "model": ""}
    except Exception:
        pass

    try:
        from engines.llm_adaptation_mode import resolve_llm_endpoint, resolve_llm_model

        ep = resolve_llm_endpoint()
        if ep.get("available"):
            return {
                "kind": "local",
                "base_url": ep.get("base_url"),
                "provider": ep.get("provider"),
                "model": resolve_llm_model(ep.get("models"), provider=ep.get("provider", "")),
                "api_key": ep.get("api_key"),
            }
    except Exception:
        pass

    for pid, prof in _CLOUD_PROFILES.items():
        key = _profile_key(prof)
        if key:
            return {
                "kind": "cloud",
                "profile": pid,
                "base_url": prof["base_url"],
                "provider": pid,
                "model": prof["default_model"],
                "api_key": key,
            }
    return {"kind": "none", "provider": "none", "model": ""}


def chat_completion(
    prompt: str,
    *,
    system: str | None = None,
    model: str | None = None,
    max_tokens: int = 512,
    temperature: float = 0.2,
    timeout: float = 120.0,
    transport: dict[str, Any] | None = None,
) -> str | None:
    """Low-level chat via resolved transport (used by llm_gateway fallback)."""
    tr = transport or resolve_transport()
    base = str(tr.get("base_url") or "").rstrip("/")
    if not base:
        return None
    mdl = model or tr.get("model") or ""
    headers = {"Content-Type": "application/json"}
    key = tr.get("api_key") or os.getenv("OPENAI_API_KEY")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    body = json.dumps(
        {
            "model": mdl,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
    ).encode("utf-8")
    req = urllib.request.Request(f"{base}/chat/completions", data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        choice = (data.get("choices") or [{}])[0]
        return ((choice.get("message") or {}).get("content") or "").strip() or None
    except Exception as exc:
        logger.debug("transport chat failed: %s", exc)
        return None
