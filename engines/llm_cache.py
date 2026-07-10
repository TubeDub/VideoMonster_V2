"""Persistent LLM rewrite cache (TZ §6).

Avoids re-running an LLM rewrite when nothing relevant changed. The cache key is
a hash of everything that can affect the result:

* the full prompt (which already encodes the source/translated text, language and
  target ratio / settings),
* the model name,
* the adaptation algorithm version (bump it to invalidate every entry).

If the text, language, settings, or algorithm change, the key changes and the
rewrite re-runs. Same input → cached result is reused, so a repeated run of an
unchanged project performs zero LLM calls.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any

_LOCK = threading.RLock()
_MEM: dict[str, str] = {}
_DISK_LOADED = False
_DISABLED = str(os.getenv("VM_LLM_CACHE_DISABLE") or "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)


def _cache_path() -> Path:
    base = os.getenv("VM_LLM_CACHE_DIR")
    if base:
        root = Path(base)
    else:
        root = Path(__file__).resolve().parents[1] / "data" / "cache"
    root.mkdir(parents=True, exist_ok=True)
    return root / "llm_rewrite_cache.json"


def _ensure_loaded() -> None:
    global _DISK_LOADED
    if _DISK_LOADED or _DISABLED:
        return
    with _LOCK:
        if _DISK_LOADED:
            return
        try:
            p = _cache_path()
            if p.is_file():
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    _MEM.update({str(k): str(v) for k, v in data.items()})
        except Exception:
            pass
        _DISK_LOADED = True


def make_key(*parts: Any) -> str:
    """Stable hash of all cache-affecting inputs."""
    h = hashlib.sha256()
    for part in parts:
        h.update(b"\x1f")
        h.update(str(part).encode("utf-8", "ignore"))
    return h.hexdigest()


def get(key: str) -> str | None:
    if _DISABLED or not key:
        return None
    _ensure_loaded()
    with _LOCK:
        return _MEM.get(key)


def put(key: str, value: str) -> None:
    if _DISABLED or not key or value is None:
        return
    _ensure_loaded()
    with _LOCK:
        if _MEM.get(key) == value:
            return
        _MEM[key] = value
        try:
            _cache_path().write_text(
                json.dumps(_MEM, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:
            pass


def stats() -> dict[str, Any]:
    _ensure_loaded()
    with _LOCK:
        return {"entries": len(_MEM), "disabled": _DISABLED}
