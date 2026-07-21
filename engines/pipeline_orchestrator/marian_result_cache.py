"""In-memory + disk cache for Marian MT results (identical source text)."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.pipeline_orchestrator.marian_cache")

_MEM: dict[str, tuple[str, float]] = {}
_LOCK = threading.RLock()
_MAX_MEM = 4096
_TTL_S = 86400.0


def _tps_version() -> int:
    try:
        from engines.tps.version import TPS_PIPELINE_VERSION

        return int(TPS_PIPELINE_VERSION)
    except Exception:
        return 1


def _cache_key(text: str, src: str, tgt: str, *, context: str = "") -> str:
    raw = f"tps{_tps_version()}|{src}|{tgt}|{context}|{text}".encode(
        "utf-8", errors="replace"
    )
    return hashlib.sha256(raw).hexdigest()


def _disk_path(app_dir: Path) -> Path:
    return app_dir / "cache" / "marian_results.json"


def get_cached(
    text: str,
    src: str,
    tgt: str,
    *,
    app_dir: Path | None = None,
    context: str = "",
) -> str | None:
    key = _cache_key(text, src, tgt, context=context)
    now = time.time()
    with _LOCK:
        row = _MEM.get(key)
        if row and (now - row[1]) < _TTL_S:
            return row[0]
    if app_dir is not None:
        try:
            path = _disk_path(app_dir)
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8"))
                entry = data.get(key)
                if entry and (now - float(entry.get("at") or 0)) < _TTL_S:
                    val = str(entry.get("text") or "")
                    with _LOCK:
                        _MEM[key] = (val, now)
                    return val
        except Exception:
            pass
    return None


def put_cached(
    text: str,
    src: str,
    tgt: str,
    translated: str,
    *,
    app_dir: Path | None = None,
    context: str = "",
) -> None:
    key = _cache_key(text, src, tgt, context=context)
    val = str(translated or "")
    now = time.time()
    with _LOCK:
        if len(_MEM) >= _MAX_MEM:
            oldest = min(_MEM.items(), key=lambda x: x[1][1])
            _MEM.pop(oldest[0], None)
        _MEM[key] = (val, now)
    if app_dir is None:
        return
    try:
        path = _disk_path(app_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {}
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
        data[key] = {"text": val, "at": now, "src": src, "tgt": tgt}
        if len(data) > 8000:
            items = sorted(data.items(), key=lambda x: float(x[1].get("at") or 0))
            data = dict(items[-6000:])
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.debug("marian cache write failed: %s", exc)
