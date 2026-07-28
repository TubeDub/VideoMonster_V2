# -*- coding: utf-8 -*-
"""Disk cache for MT / translation text (Simple speedup).

Key: hash(normalized_source + source_lang + target_lang + engine)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.mt_cache")

_WS = re.compile(r"\s+")


def default_cache_dir() -> Path:
    raw = (os.getenv("VM_MT_CACHE_DIR") or "").strip()
    if raw:
        return Path(raw)
    root = Path(__file__).resolve().parent.parent
    return root / "cache" / "mt"


def normalize_mt_cache_text(text: str) -> str:
    return _WS.sub(" ", str(text or "").strip())


def mt_cache_key(
    text: str,
    source_lang: str,
    target_lang: str,
    *,
    engine: str = "auto",
) -> str:
    payload = "|".join(
        [
            normalize_mt_cache_text(text),
            str(source_lang or "").strip().lower(),
            str(target_lang or "").strip().lower(),
            str(engine or "auto").strip().lower(),
        ]
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def cache_path_for_key(cache_dir: Path, key: str) -> Path:
    shard = key[:2] if len(key) >= 2 else "00"
    return Path(cache_dir) / shard / f"{key}.json"


def lookup_mt_cache(
    text: str,
    source_lang: str,
    target_lang: str,
    *,
    engine: str = "auto",
    cache_dir: Path | None = None,
) -> str | None:
    if not normalize_mt_cache_text(text):
        return ""
    cdir = Path(cache_dir) if cache_dir is not None else default_cache_dir()
    key = mt_cache_key(text, source_lang, target_lang, engine=engine)
    path = cache_path_for_key(cdir, key)
    try:
        if not path.is_file() or path.stat().st_size < 8:
            logger.debug("mt_cache_miss key=%s", key[:12])
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        out = str(data.get("translated") or "").strip()
        if not out:
            logger.debug("mt_cache_miss empty key=%s", key[:12])
            return None
        logger.info("mt_cache_hit key=%s", key[:12])
        return out
    except Exception as exc:
        logger.debug("mt_cache_miss read_err key=%s err=%s", key[:12], exc)
        return None


def store_mt_cache(
    text: str,
    translated: str,
    source_lang: str,
    target_lang: str,
    *,
    engine: str = "auto",
    cache_dir: Path | None = None,
) -> Path | None:
    src = normalize_mt_cache_text(text)
    dst = str(translated or "").strip()
    if not src or not dst:
        return None
    cdir = Path(cache_dir) if cache_dir is not None else default_cache_dir()
    key = mt_cache_key(src, source_lang, target_lang, engine=engine)
    path = cache_path_for_key(cdir, key)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "source": src,
                    "translated": dst,
                    "source_lang": source_lang,
                    "target_lang": target_lang,
                    "engine": engine,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return path
    except OSError as exc:
        logger.debug("mt_cache_store failed: %s", exc)
        return None


def empty_mt_stats() -> dict[str, Any]:
    return {
        "mt_wall_sec": 0.0,
        "mt_segments": 0,
        "mt_batch_size": 0,
        "mt_calls": 0,
        "mt_engine": "",
        "mt_cache_hits": 0,
        "mt_cache_misses": 0,
        "mt_concurrency_used": 1,
        "mt_retries": 0,
        "mt_path": "",
    }
