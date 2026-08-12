# -*- coding: utf-8 -*-
"""Disk cache for Edge-TTS audio (Simple speedup).

Key: hash(normalized_text + voice_id + lang + rate + pitch) — Stage 18.
Env VM_TTS_NO_CACHE=1 → never read cache (Simple cold regen).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.tts_cache")

_WS = re.compile(r"\s+")


def default_cache_dir() -> Path:
    raw = (os.getenv("VM_TTS_CACHE_DIR") or "").strip()
    if raw:
        return Path(raw)
    root = Path(__file__).resolve().parent.parent
    return root / "cache" / "tts"


def tts_cache_disabled() -> bool:
    """VM_TTS_NO_CACHE=1 — skip TTS audio cache reads (and prefer miss)."""
    return str(os.getenv("VM_TTS_NO_CACHE") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def normalize_tts_cache_text(text: str) -> str:
    return _WS.sub(" ", str(text or "").strip())


def _voice_lang(voice: str, lang: str = "") -> str:
    lang0 = str(lang or "").strip().lower().split("-")[0]
    if lang0:
        return lang0
    v = str(voice or "").strip()
    parts = v.split("-")
    if len(parts) >= 1 and parts[0]:
        return parts[0].lower()
    return ""


def tts_cache_key(
    text: str,
    voice: str,
    *,
    rate: str = "",
    pitch: str = "",
    engine_id: str = "edge-offline",
    lang: str = "",
    length_scale: str | float | None = "",
) -> str:
    """Cache key: text + backend + voice + lang + rate/pitch/length_scale.

    Stage 24 v3 — old v1/v2 keys without lang/voice/backend never match.
    """
    rate_n = str(rate or "").strip() or "-5%"
    pitch_n = str(pitch or "").strip()
    if pitch_n in ("+0Hz", "0Hz", "+0hz", "0hz"):
        pitch_n = ""
    ls_n = str(length_scale if length_scale is not None else "").strip()
    lang_n = _voice_lang(voice, lang)
    vl = str(voice or "").lower()
    # Empty lang for uk voices → stamp uk so old ambiguous keys never match.
    if not lang_n and (
        vl in ("mykyta", "lada", "tetiana")
        or vl.startswith("uk-ua-")
        or vl.startswith("uk_ua-")
    ):
        lang_n = "uk"
    payload = "|".join(
        [
            "v3",
            normalize_tts_cache_text(text),
            str(voice or "").strip(),
            lang_n,
            rate_n,
            pitch_n,
            ls_n,
            str(engine_id or "edge-offline").strip(),
        ]
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def cache_path_for_key(cache_dir: Path, key: str, *, ext: str = ".mp3") -> Path:
    ext0 = ext if str(ext).startswith(".") else f".{ext}"
    # Shard by first 2 hex chars to keep folders small.
    shard = (key[:2] if len(key) >= 2 else "00")
    return Path(cache_dir) / shard / f"{key}{ext0}"


def is_valid_tts_file(path: str | Path | None, *, min_bytes: int = 256) -> bool:
    if not path:
        return False
    p = Path(path)
    try:
        return p.is_file() and p.stat().st_size >= int(min_bytes)
    except OSError:
        return False


def lookup_tts_cache(
    text: str,
    voice: str,
    *,
    rate: str = "",
    pitch: str = "",
    engine_id: str = "edge-offline",
    cache_dir: Path | None = None,
    ext: str = ".mp3",
    lang: str = "",
    length_scale: str | float | None = "",
) -> Path | None:
    """Return cached audio path if present and valid."""
    if tts_cache_disabled():
        logger.info("tts_cache_disabled VM_TTS_NO_CACHE=1 — miss")
        return None
    # Stage 24: never serve cache without lang+voice+backend in key inputs.
    if not str(voice or "").strip() or not str(engine_id or "").strip():
        logger.info("tts_cache_miss incomplete_key voice/engine empty")
        return None
    cdir = Path(cache_dir) if cache_dir is not None else default_cache_dir()
    key = tts_cache_key(
        text,
        voice,
        rate=rate,
        pitch=pitch,
        engine_id=engine_id,
        lang=lang,
        length_scale=length_scale,
    )
    path = cache_path_for_key(cdir, key, ext=ext)
    if is_valid_tts_file(path):
        logger.info("tts_cache_hit key=%s path=%s", key[:12], path)
        return path
    logger.debug("tts_cache_miss key=%s", key[:12])
    return None


def store_tts_cache(
    src: str | Path,
    text: str,
    voice: str,
    *,
    rate: str = "",
    pitch: str = "",
    engine_id: str = "edge-offline",
    cache_dir: Path | None = None,
    lang: str = "",
    length_scale: str | float | None = "",
) -> Path | None:
    """Copy successful synth into cache. Returns cache path or None."""
    if not is_valid_tts_file(src):
        return None
    if tts_cache_disabled():
        return None
    if not str(voice or "").strip() or not str(engine_id or "").strip():
        return None
    cdir = Path(cache_dir) if cache_dir is not None else default_cache_dir()
    key = tts_cache_key(
        text,
        voice,
        rate=rate,
        pitch=pitch,
        engine_id=engine_id,
        lang=lang,
        length_scale=length_scale,
    )
    ext = Path(src).suffix or ".mp3"
    dest = cache_path_for_key(cdir, key, ext=ext)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.resolve() != Path(src).resolve():
            shutil.copy2(str(src), str(dest))
        logger.info("tts_cache_store key=%s path=%s", key[:12], dest)
        return dest
    except OSError as exc:
        logger.warning("tts_cache_store failed: %s", exc)
        return None


def materialize_cached(
    cached: Path,
    dest: str | Path,
) -> bool:
    """Copy cache entry to destination path."""
    try:
        dest_p = Path(dest)
        dest_p.parent.mkdir(parents=True, exist_ok=True)
        if dest_p.resolve() == Path(cached).resolve():
            return is_valid_tts_file(dest_p)
        shutil.copy2(str(cached), str(dest_p))
        return is_valid_tts_file(dest_p)
    except OSError as exc:
        logger.warning("tts_cache materialize failed: %s", exc)
        return False


def empty_stats() -> dict[str, Any]:
    return {
        "tts_cache_hits": 0,
        "tts_cache_misses": 0,
        "tts_skips_existing": 0,
        "tts_retries": 0,
        "tts_segments_total": 0,
        "tts_concurrency_used": 0,
        "tts_wall_sec": 0.0,
        "tts_rate_limit_backs": 0,
    }
