"""P614 Voice Cloning — universal adapter interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from engines.voice_platform.types import SynthesisResult


class VoiceCloneAdapter(ABC):
    """Any cloning-capable engine plugs in here — Dub Engine stays unaware."""

    adapter_id: str = "clone-base"

    @abstractmethod
    def is_available(self) -> bool:
        ...

    @abstractmethod
    def clone_synthesize(
        self,
        text: str,
        reference_wav: str,
        output_path: str,
        *,
        language: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> SynthesisResult:
        ...


class NullCloneAdapter(VoiceCloneAdapter):
    adapter_id = "clone-null"

    def is_available(self) -> bool:
        return False

    def clone_synthesize(
        self,
        text: str,
        reference_wav: str,
        output_path: str,
        *,
        language: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> SynthesisResult:
        return SynthesisResult(
            ok=False,
            error="No cloning adapter available",
            provider=self.adapter_id,
        )


class ProviderCloneBridge(VoiceCloneAdapter):
    """Bridge cloning-capable VoiceProviders (XTTS / OpenVoice / …)."""

    def __init__(self, provider_id: str) -> None:
        self.adapter_id = f"clone-{provider_id}"
        self._provider_id = provider_id

    def is_available(self) -> bool:
        from engines.voice_platform.tts_registry import get_provider

        p = get_provider(self._provider_id)
        return bool(p.supports_cloning() and p.health_check().get("ok"))

    def clone_synthesize(
        self,
        text: str,
        reference_wav: str,
        output_path: str,
        *,
        language: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> SynthesisResult:
        from engines.voice_platform.tts_registry import get_provider

        if not Path(reference_wav).is_file():
            return SynthesisResult(ok=False, error="reference_wav missing", provider=self.adapter_id)
        p = get_provider(self._provider_id)
        result = p.synthesize(
            text,
            voice_external_id=reference_wav,
            output_path=output_path,
            language=language,
            meta={"clone_ref": reference_wav, **(meta or {})},
        )
        result.meta["cloning"] = True
        result.provider = self.adapter_id
        return result


_CLONE_ADAPTERS: list[VoiceCloneAdapter] = []


def register_clone_adapter(adapter: VoiceCloneAdapter) -> None:
    _CLONE_ADAPTERS.append(adapter)


def get_clone_adapter() -> VoiceCloneAdapter:
    if not _CLONE_ADAPTERS:
        # Auto-discover cloning providers
        for pid in ("xtts", "openvoice", "fishspeech", "cosyvoice"):
            bridge = ProviderCloneBridge(pid)
            if bridge.is_available():
                register_clone_adapter(bridge)
        if not _CLONE_ADAPTERS:
            register_clone_adapter(NullCloneAdapter())
    for a in _CLONE_ADAPTERS:
        if a.is_available():
            return a
    return _CLONE_ADAPTERS[-1]


def clone_voice(
    text: str,
    reference_wav: str,
    output_path: str,
    *,
    language: str | None = None,
) -> SynthesisResult:
    return get_clone_adapter().clone_synthesize(
        text, reference_wav, output_path, language=language
    )
