"""P602 TTS Registry — auto-register engines as VoiceProviders."""

from __future__ import annotations

import logging
from typing import Any

from engines.voice_platform.provider import LegacyEngineAdapter, MockVoiceProvider, VoiceProvider

logger = logging.getLogger("tubedub.voice_platform.tts_registry")

_PROVIDERS: dict[str, VoiceProvider] = {}
_BOOTSTRAPPED = False


def _bootstrap() -> None:
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return
    # Always register mock
    register_provider(MockVoiceProvider())
    try:
        from engines.tts_engines.registry import _all_engine_instances

        for eng in _all_engine_instances():
            adapter = LegacyEngineAdapter(eng)
            register_provider(adapter)
    except Exception as exc:
        logger.warning("TTS engine bootstrap partial: %s", exc)
    _BOOTSTRAPPED = True


def register_provider(provider: VoiceProvider) -> None:
    """P602 — register without changing Dub Engine core."""
    pid = provider.provider_id
    # Prefer native VoiceProvider mock over legacy silent mock
    if pid == "mock" and pid in _PROVIDERS and type(_PROVIDERS[pid]).__name__ == "MockVoiceProvider":
        return
    _PROVIDERS[pid] = provider
    # Common aliases
    aliases = {
        "edge-offline": ["edge", "edge-tts", "edgetts"],
        "elevenlabs": ["eleven"],
        "xtts": ["coqui-xtts"],
    }
    for canon, al in aliases.items():
        if pid == canon:
            for a in al:
                _PROVIDERS.setdefault(a, provider)


def get_provider(provider_id: str | None = None) -> VoiceProvider:
    _bootstrap()
    if not provider_id:
        # Prefer edge if available, else mock
        for candidate in ("edge-offline", "edge", "mock"):
            p = _PROVIDERS.get(candidate)
            if p is not None:
                p.initialize()
                if p.health_check().get("ok") or candidate == "mock":
                    return p
        return MockVoiceProvider()
    p = _PROVIDERS.get(provider_id)
    if p is None:
        logger.warning("Unknown provider %s — using mock", provider_id)
        return _PROVIDERS.get("mock") or MockVoiceProvider()
    p.initialize()
    return p


def list_providers() -> list[dict[str, Any]]:
    _bootstrap()
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for pid, p in _PROVIDERS.items():
        if p.provider_id in seen:
            continue
        seen.add(p.provider_id)
        caps = p.capabilities().to_dict()
        health = p.health_check()
        out.append(
            {
                "id": p.provider_id,
                "name": p.display_name,
                "capabilities": caps,
                "health": health,
            }
        )
    return out


def reset_registry_for_tests() -> None:
    global _BOOTSTRAPPED
    _PROVIDERS.clear()
    _BOOTSTRAPPED = False
