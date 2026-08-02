"""TTS engine registry."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from engines.tts_engines.base import TTSEngineInfo, TTSResult
from engines.tts_engines.edge_engine import EdgeTTSEngine
from engines.tts_engines.online_engines import online_engines
from engines.tts_engines.providers import provider_engines

logger = logging.getLogger("tubedub.engines.tts_engines.registry")

_DEFAULT_ENGINE = "edge-offline"
_ENGINE_CACHE: dict[str, Any] = {}

# TZ Stage 5 — local cloner adapters (stubs until backends are installed).
LOCAL_CLONER_ENGINE_IDS = frozenset(
    {"f5-tts", "cosyvoice", "gpt-sovits", "chatterbox", "xtts", "openvoice"}
)


def offline_tts_mode() -> bool:
    """True when Settings/env force offline TTS or dub pipeline locked offline."""
    mode = (os.getenv("VM_TTS_MODE") or os.getenv("VM_DUB_MODE") or "").strip().lower()
    if mode == "offline":
        return True
    try:
        from engines.model_manager.runtime import is_offline_only

        return bool(is_offline_only())
    except Exception:
        return False


def load_engine_catalog(app_dir: Path) -> list[dict[str, Any]]:
    path = app_dir / "data" / "tts_engines.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("engines") if isinstance(data, dict) else []
    except Exception:
        return []


def list_engine_infos(app_dir: Path | None = None) -> list[TTSEngineInfo]:
    app_dir = app_dir or Path(__file__).resolve().parent.parent.parent
    catalog = {e.get("id"): e for e in load_engine_catalog(app_dir) if e.get("id")}
    infos: list[TTSEngineInfo] = []
    for eng in _all_engine_instances():
        meta = catalog.get(eng.id, {})
        infos.append(
            TTSEngineInfo(
                id=eng.id,
                name=str(meta.get("name") or eng.name),
                mode=eng.mode,
                provider=str(meta.get("provider") or eng.id),
                description=str(meta.get("description") or ""),
                supports_stress=bool(getattr(eng, "supports_stress", False)),
                supports_ssml=bool(getattr(eng, "supports_ssml", False)),
                available=eng.is_available(),
                config_keys=list(meta.get("config_keys") or []),
            )
        )
    return infos


def _all_engine_instances():
    yield EdgeTTSEngine()
    seen = {"edge-offline"}
    # Prefer wired online engines over incomplete provider adapters
    for eng in online_engines():
        seen.add(eng.id)
        yield eng
    for eng in provider_engines():
        if eng.id in seen:
            continue
        seen.add(eng.id)
        yield eng


def get_engine(engine_id: str | None = None):
    from engines.tts_backends import normalize_backend_name

    requested = (engine_id or "").strip()
    env_default = (
        os.getenv("TTS_BACKEND") or os.getenv("VM_TTS_ENGINE") or ""
    ).strip()
    eid = normalize_backend_name(requested or env_default) if (requested or env_default) else ""
    if not eid:
        # Offline mode: prefer tts_uk → piper → edge
        if offline_tts_mode():
            for candidate in ("tts_uk", "piper", _DEFAULT_ENGINE):
                for eng in _all_engine_instances():
                    if eng.id == candidate and eng.is_available():
                        _ENGINE_CACHE[candidate] = eng
                        return eng
        eid = _DEFAULT_ENGINE
    eid = normalize_backend_name(eid)
    if eid in _ENGINE_CACHE:
        return _ENGINE_CACHE[eid]
    for eng in _all_engine_instances():
        if eng.id == eid:
            _ENGINE_CACHE[eid] = eng
            return eng
    eng = EdgeTTSEngine()
    _ENGINE_CACHE[eid] = eng
    return eng


def default_engine_id() -> str:
    from engines.tts_backends import normalize_backend_name

    env = (os.getenv("TTS_BACKEND") or os.getenv("VM_TTS_ENGINE") or "").strip()
    if offline_tts_mode() and not env:
        for eng in _all_engine_instances():
            if eng.id == "tts_uk" and eng.is_available():
                return "tts_uk"
        for eng in _all_engine_instances():
            if eng.id == "piper" and eng.is_available():
                return "piper"
    return normalize_backend_name(env or _DEFAULT_ENGINE)


def synthesize(
    text: str,
    voice: str,
    output_path: str,
    *,
    engine_id: str | None = None,
    rate: str | None = None,
    pitch: str | None = None,
) -> TTSResult:
    offline = offline_tts_mode()
    eng = get_engine(engine_id)
    requested = (engine_id or "").strip()

    # Strict offline: never call online engines
    if offline and getattr(eng, "mode", "") == "online":
        return TTSResult(
            ok=False,
            engine_id=eng.id,
            error=(
                f"Online TTS '{eng.id}' blocked in offline mode "
                "(VM_TTS_MODE/VM_DUB_MODE=offline or dub lock). "
                "Use piper / edge-offline / coqui."
            ),
        )

    if not eng.is_available():
        # Explicit offline engine or offline mode: fail clearly, no silent online/edge swap
        if offline or (requested and requested not in ("", _DEFAULT_ENGINE, "edge")):
            hint = ""
            if eng.id == "piper":
                hint = " Install piper CLI/package and set PIPER_MODEL / VM_PIPER_MODEL."
            elif eng.id == "tts_uk":
                hint = " Install with: pip install tts-uk"
            elif eng.id == "coqui":
                hint = " Install Coqui TTS (pip install TTS) and configure VM_COQUI_MODEL."
            return TTSResult(
                ok=False,
                engine_id=eng.id,
                error=(
                    f"TTS engine '{eng.id}' unavailable"
                    + (" (offline mode — no fallback)" if offline else "")
                    + "."
                    + hint
                ),
            )
        if eng.id != EdgeTTSEngine.id:
            logger.warning("[TTS] %s unavailable — fallback edge-offline", eng.id)
            eng = EdgeTTSEngine()
            if not eng.is_available():
                return TTSResult(
                    ok=False,
                    error="edge-tts not installed (and requested engine unavailable)",
                    engine_id=_DEFAULT_ENGINE,
                )
        else:
            return TTSResult(ok=False, error="edge-tts not installed", engine_id=eng.id)
    from engines.tts_backends import resolve_voice_for_backend

    resolved_voice = resolve_voice_for_backend(voice, eng.id)
    return eng.synthesize(text, resolved_voice, output_path, rate=rate, pitch=pitch)
