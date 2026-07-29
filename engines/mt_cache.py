# -*- coding: utf-8 -*-
"""Disk cache for MT / translation text (Simple speedup).

Key: hash(normalized_source + langs + engine + v3_glossary_split)
Stage 11: stricter incomplete reject; VM_MT_NO_CACHE bypass.
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
_SHORT_RATIO = 0.50
_CACHE_KEY_SUFFIX = "v3_glossary_split"

_CRITICAL_ENTITY_CHECKS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("star wars", re.compile(r"зоряні|star\s*wars", re.I)),
    ("george lucas", re.compile(r"лукас|lucas", re.I)),
    ("acceptance", re.compile(r"прийнятт|лист|acceptance", re.I)),
)


def mt_cache_disabled() -> bool:
    return (os.getenv("VM_MT_NO_CACHE") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


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
    # v3: invalidate v2 truncated / pre-glossary cache entries.
    payload = "|".join(
        [
            normalize_mt_cache_text(text),
            str(source_lang or "").strip().lower(),
            str(target_lang or "").strip().lower(),
            str(engine or "auto").strip().lower(),
            _CACHE_KEY_SUFFIX,
        ]
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def cache_path_for_key(cache_dir: Path, key: str) -> Path:
    shard = key[:2] if len(key) >= 2 else "00"
    return Path(cache_dir) / shard / f"{key}.json"


def _missing_critical_entities(src: str, tgt: str) -> bool:
    src_l = src.lower()
    for needle, tgt_pat in _CRITICAL_ENTITY_CHECKS:
        if needle in src_l and not tgt_pat.search(tgt):
            return True
    return False


def is_incomplete_mt_pair(
    source: str,
    translated: str,
    source_lang: str,
    target_lang: str,
) -> bool:
    """True when EN→UK translation is truncated / missing critical entities."""
    src = normalize_mt_cache_text(source)
    dst = str(translated or "").strip()
    if not src or not dst:
        return True
    src_l = str(source_lang or "").strip().lower()
    tgt_l = str(target_lang or "").strip().lower()
    if src_l != "en" or tgt_l != "uk":
        return False
    w_src = len(src.split())
    w_tgt = len(dst.split())
    if w_src <= 0:
        return False

    long_src = w_src > 40
    try:
        from engines.mt.oversized_guard import is_oversized_mt_unit

        long_src = long_src or is_oversized_mt_unit(src)
    except Exception:
        pass

    if long_src and w_tgt < (_SHORT_RATIO * w_src):
        return True
    if w_src > 80 and w_tgt < 60:
        return True
    if _missing_critical_entities(src, dst):
        return True
    return False


def lookup_mt_cache(
    text: str,
    source_lang: str,
    target_lang: str,
    *,
    engine: str = "auto",
    cache_dir: Path | None = None,
) -> str | None:
    if mt_cache_disabled():
        logger.info("mt_cache_bypass VM_MT_NO_CACHE=1")
        return None
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
        if is_incomplete_mt_pair(text, out, source_lang, target_lang):
            logger.warning(
                "mt_cache_reject_incomplete key=%s src_words=%d tgt_words=%d",
                key[:12],
                len(normalize_mt_cache_text(text).split()),
                len(out.split()),
            )
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
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
    if mt_cache_disabled():
        return None
    src = normalize_mt_cache_text(text)
    dst = str(translated or "").strip()
    if not src or not dst:
        return None
    if is_incomplete_mt_pair(src, dst, source_lang, target_lang):
        logger.warning(
            "mt_cache_skip_incomplete src_words=%d tgt_words=%d",
            len(src.split()),
            len(dst.split()),
        )
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
                    "key_suffix": _CACHE_KEY_SUFFIX,
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
        "mt_guard_splits": 0,
        "mt_segment_engines": [],
        "mt_cache_bypassed": False,
    }
