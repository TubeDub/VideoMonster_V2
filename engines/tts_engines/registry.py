"""TTS engine registry."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from engines.tts_engines.base import TTSEngineInfo, TTSResult
from engines.tts_engines.edge_engine import EdgeTTSEngine
from engines.tts_engines.online_stubs import stub_engines

logger = logging.getLogger("tubedub.engines.tts_engines.registry")

_DEFAULT_ENGINE = "edge-offline"
_ENGINE_CACHE: dict[str, Any] = {}


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
    for stub in stub_engines():
        yield stub


def get_engine(engine_id: str | None = None):
    eid = (engine_id or os.getenv("VM_TTS_ENGINE") or _DEFAULT_ENGINE).strip()
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
    return (os.getenv("VM_TTS_ENGINE") or _DEFAULT_ENGINE).strip() or _DEFAULT_ENGINE


def synthesize(
    text: str,
    voice: str,
    output_path: str,
    *,
    engine_id: str | None = None,
    rate: str | None = None,
    pitch: str | None = None,
) -> TTSResult:
    eng = get_engine(engine_id)
    if not eng.is_available():
        if eng.id != EdgeTTSEngine.id:
            logger.warning("[TTS] %s unavailable — fallback edge-offline", eng.id)
            eng = EdgeTTSEngine()
        else:
            return TTSResult(ok=False, error="edge-tts not installed", engine_id=eng.id)
    return eng.synthesize(text, voice, output_path, rate=rate, pitch=pitch)
