"""P614 Voice Cloning — universal adapter interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from engines.voice_platform.types import SynthesisResult

# Engines that can satisfy Voice Platform cloning (order = probe preference).
CLONE_PROVIDER_IDS: tuple[str, ...] = (
    "xtts",
    "coqui",
    "openvoice",
    "fishspeech",
    "cosyvoice",
)

_CLONE_MISSING_RU = (
    "Клонирование голоса недоступно — нужен движок "
    "xtts/coqui, openvoice, fishspeech или cosyvoice"
)
_CLONE_HINT_RU = (
    "Установите и настройте один из движков: "
    "xtts / coqui / openvoice / fishspeech / cosyvoice"
)


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
            error=_CLONE_MISSING_RU,
            provider=self.adapter_id,
            meta={"error_code": "CLONE_ENGINE_MISSING", "required_engines": list(CLONE_PROVIDER_IDS)},
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
        # Auto-discover cloning providers (coqui/XTTS first — real synthesize path)
        for pid in CLONE_PROVIDER_IDS:
            bridge = ProviderCloneBridge(pid)
            if bridge.is_available():
                register_clone_adapter(bridge)
        if not _CLONE_ADAPTERS:
            register_clone_adapter(NullCloneAdapter())
    for a in _CLONE_ADAPTERS:
        if a.is_available():
            return a
    return _CLONE_ADAPTERS[-1]


def probe_clone_engines() -> tuple[list[str], list[str]]:
    """Return (available_engine_ids, missing_engine_ids) without mutating registry."""
    available: list[str] = []
    missing: list[str] = []
    for pid in CLONE_PROVIDER_IDS:
        try:
            if ProviderCloneBridge(pid).is_available():
                available.append(pid)
            else:
                missing.append(pid)
        except Exception:
            missing.append(pid)
    return available, missing


def clone_readiness() -> dict[str, Any]:
    """Structured readiness for UI/API — prefer RU message over bare 503."""
    adapter = get_clone_adapter()
    available = bool(adapter.is_available())
    avail_engines, missing_engines = probe_clone_engines()
    return {
        "ok": True,
        "available": available,
        "adapter_id": getattr(adapter, "adapter_id", "unknown"),
        "required_engines": list(CLONE_PROVIDER_IDS),
        "available_engines": avail_engines,
        "missing_engines": missing_engines,
        "error_code": None if available else "CLONE_ENGINE_MISSING",
        "message": None if available else _CLONE_MISSING_RU,
        "hint": None if available else _CLONE_HINT_RU,
    }


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


def clone_voice_with_verification(
    text: str,
    reference_wav: str,
    output_path: str,
    *,
    language: str | None = None,
    threshold: float = 0.75,
    max_attempts: int = 3,
) -> SynthesisResult:
    """Spec v3: clone + cosine verify with up to ``max_attempts`` retries.

    Returns the last-attempted ``SynthesisResult`` with
    ``meta["voice_verification"]`` populated. Never raises — degrades to plain
    ``clone_voice`` output if cosine is unavailable.
    """
    from engines.speaker_verification import (
        DEFAULT_COSINE_THRESHOLD,
        retry_until_verified,
    )

    if threshold is None:
        threshold = DEFAULT_COSINE_THRESHOLD

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    attempts_log: list[dict[str, Any]] = []
    last_result: SynthesisResult | None = None

    def _synth(attempt: int) -> str | None:
        nonlocal last_result
        attempt_out = (
            output
            if attempt == 1
            else output.parent / f"{output.stem}_try{attempt}{output.suffix}"
        )
        res = clone_voice(text, reference_wav, str(attempt_out), language=language)
        last_result = res
        attempts_log.append(
            {
                "attempt": attempt,
                "ok": bool(getattr(res, "ok", False)),
                "provider": getattr(res, "provider", ""),
                "output": str(attempt_out.resolve()) if attempt_out.is_file() else None,
                "error": getattr(res, "error", None),
            }
        )
        if getattr(res, "ok", False) and attempt_out.is_file():
            return str(attempt_out)
        return None

    verdict = retry_until_verified(
        _synth,
        reference_wav,
        threshold=float(threshold),
        max_attempts=int(max_attempts),
    )

    # Elevate the best candidate to ``output_path`` if a later try beat the first.
    best_path = verdict.get("candidate")
    if best_path and Path(best_path).is_file() and Path(best_path) != output:
        try:
            import shutil as _sh

            _sh.copy2(best_path, output)
        except Exception:
            pass

    if last_result is None:
        last_result = SynthesisResult(
            ok=False,
            error="voice_clone_no_attempts",
            provider="clone-verify",
        )

    last_result.meta = dict(getattr(last_result, "meta", None) or {})
    last_result.meta["voice_verification"] = {
        "ok": bool(verdict.get("ok")),
        "similarity": verdict.get("similarity"),
        "threshold": verdict.get("threshold"),
        "method": verdict.get("method"),
        "attempts_used": verdict.get("attempts_used"),
        "all_similarities": verdict.get("all_similarities"),
        "attempts": attempts_log,
    }
    last_result.ok = bool(verdict.get("ok")) and Path(output).is_file()
    return last_result
