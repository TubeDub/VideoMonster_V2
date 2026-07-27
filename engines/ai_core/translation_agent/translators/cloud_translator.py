"""Cloud LLM translation — OpenAI / Anthropic / Gemini when keys present."""

from __future__ import annotations

import json
import logging
import os
import urllib.request

from engines.ai_core.translation_agent.translator_interface import BaseTranslator

logger = logging.getLogger("tubedub.translation_agent.cloud")

_LANG_NAMES = {
    "en": "English",
    "ru": "Russian",
    "uk": "Ukrainian",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "pl": "Polish",
    "it": "Italian",
    "ja": "Japanese",
    "zh": "Chinese",
}


def _has_openai_key() -> bool:
    return bool(
        os.getenv("OPENAI_API_KEY")
        or os.getenv("VM_OPENAI_API_KEY")
        or os.getenv("VM_LLM_API_KEY")
    )


def _has_anthropic_key() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("VM_ANTHROPIC_API_KEY"))


def _has_gemini_key() -> bool:
    return bool(
        os.getenv("GEMINI_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
        or os.getenv("VM_GEMINI_API_KEY")
    )


def _anthropic_chat(system: str, prompt: str, *, max_tokens: int) -> str:
    key = (os.getenv("ANTHROPIC_API_KEY") or os.getenv("VM_ANTHROPIC_API_KEY") or "").strip()
    model = (os.getenv("VM_ANTHROPIC_MODEL") or "claude-3-5-sonnet-latest").strip()
    body = json.dumps(
        {
            "model": model,
            "max_tokens": max(256, max_tokens),
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    parts = data.get("content") or []
    texts = [p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "text"]
    return "\n".join(t for t in texts if t).strip()


def _gemini_chat(system: str, prompt: str, *, max_tokens: int) -> str:
    key = (
        os.getenv("GEMINI_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
        or os.getenv("VM_GEMINI_API_KEY")
        or ""
    ).strip()
    model = (os.getenv("VM_GEMINI_MODEL") or "gemini-1.5-flash").strip()
    body = json.dumps(
        {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": max(256, max_tokens),
            },
        }
    ).encode("utf-8")
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={key}"
    )
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    cands = data.get("candidates") or []
    if not cands:
        raise RuntimeError("gemini_empty_candidates")
    parts = ((cands[0].get("content") or {}).get("parts")) or []
    return "".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()


class CloudTranslator(BaseTranslator):
    name = "cloud"

    def is_available(self) -> bool:
        if _has_openai_key():
            try:
                from engines.ai_core import llm_gateway

                return llm_gateway.is_available()
            except Exception:
                return True
        return _has_anthropic_key() or _has_gemini_key()

    def _provider(self) -> str:
        preferred = (os.getenv("VM_CLOUD_TRANSLATOR") or "").strip().lower()
        if preferred in ("openai", "anthropic", "gemini"):
            if preferred == "openai" and _has_openai_key():
                return "openai"
            if preferred == "anthropic" and _has_anthropic_key():
                return "anthropic"
            if preferred == "gemini" and _has_gemini_key():
                return "gemini"
        if _has_openai_key():
            return "openai"
        if _has_anthropic_key():
            return "anthropic"
        if _has_gemini_key():
            return "gemini"
        return "none"

    def translate(self, text: str, source: str, target: str) -> str:
        clean = str(text or "").strip()
        if not clean:
            return ""

        src_name = _LANG_NAMES.get(source, source)
        tgt_name = _LANG_NAMES.get(target, target)
        system = (
            "You are a professional subtitle translator. "
            "Translate faithfully without shortening, merging, or adding commentary. "
            "Preserve names, numbers, and dates exactly."
        )
        prompt = f"Translate from {src_name} to {tgt_name}:\n\n{clean}"
        max_tokens = max(256, len(clean) * 3)

        provider = self._provider()
        if provider == "openai":
            from engines.ai_core import llm_gateway

            result = llm_gateway.chat(
                prompt,
                system=system,
                max_tokens=max_tokens,
                temperature=0.1,
            )
            if result and str(result).strip():
                return str(result).strip()
            raise RuntimeError("OpenAI/LLM gateway returned empty translation")

        if provider == "anthropic":
            out = _anthropic_chat(system, prompt, max_tokens=max_tokens)
            if not out:
                raise RuntimeError("Anthropic returned empty translation")
            return out

        if provider == "gemini":
            out = _gemini_chat(system, prompt, max_tokens=max_tokens)
            if not out:
                raise RuntimeError("Gemini returned empty translation")
            return out

        raise RuntimeError("no_cloud_provider")
