"""Estimate TTS duration (ms) from text — chars/sec heuristic + optional probe cache."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from engines.mt.lang_codes import normalize_lang

logger = logging.getLogger("tubedub.ai_core.timing_agent.duration")

# ~14 chars/sec for Russian; language-adjusted via semantic_adaptation table
_CHARS_PER_SEC: dict[str, float] = {
    "ru": 14.0,
    "uk": 13.5,
    "en": 13.0,
    "de": 12.5,
    "fr": 13.0,
    "es": 13.5,
    "it": 13.0,
    "pt": 13.0,
    "pl": 13.0,
    "zh": 4.5,
    "ja": 5.5,
    "ko": 5.0,
    "ar": 10.0,
    "hi": 11.0,
    "default": 13.5,
}

_CACHE_FILENAME = "timing_duration_probe_cache.json"


def ms_per_char(lang: str) -> float:
    base = normalize_lang(lang)
    cps = _CHARS_PER_SEC.get(base, _CHARS_PER_SEC["default"])
    return 1000.0 / max(cps, 1.0)


def predict_duration_ms(
    text: str,
    lang: str = "ru",
    *,
    use_cache: bool = True,
    app_dir: Path | None = None,
) -> int:
    """
    Predict TTS duration in milliseconds.

    Primary: len(text) * ms_per_char with word-count floor (via semantic_adaptation).
    Optional: cached edge-tts probe result keyed by text hash.
    """
    t = str(text or "").strip()
    if not t:
        return 0

    if use_cache and app_dir is not None:
        cached = _probe_cache_lookup(app_dir, t, lang)
        if cached is not None:
            return int(cached)

    from engines.semantic_adaptation import estimate_tts_duration_ms

    return int(estimate_tts_duration_ms(t, lang))


def store_probe_result(
    text: str,
    lang: str,
    duration_ms: int,
    *,
    app_dir: Path | None = None,
) -> None:
    """Store a measured TTS duration for future predictions."""
    if not app_dir or not text or duration_ms <= 0:
        return
    cache_path = app_dir / "data" / "cache" / _CACHE_FILENAME
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if cache_path.is_file():
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    key = _cache_key(text, lang)
    data[key] = {"ms": int(duration_ms), "lang": normalize_lang(lang)}
    try:
        cache_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.debug("probe cache write failed: %s", exc)


def _cache_key(text: str, lang: str) -> str:
    payload = f"{normalize_lang(lang)}:{text.strip()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _probe_cache_lookup(app_dir: Path, text: str, lang: str) -> int | None:
    cache_path = app_dir / "data" / "cache" / _CACHE_FILENAME
    if not cache_path.is_file():
        return None
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        entry = data.get(_cache_key(text, lang))
        if isinstance(entry, dict) and entry.get("ms"):
            return int(entry["ms"])
    except Exception:
        return None
    return None
