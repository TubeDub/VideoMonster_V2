"""Translation Memory — sentence-level cache, no repeat MT calls."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.streamdub.tm")


class TranslationMemory:
    """Per-project sentence cache stored in memory + optional disk."""

    def __init__(self, project_id: str, app_dir: Path | None = None):
        self.project_id = project_id
        self.app_dir = Path(app_dir) if app_dir else None
        self._hits = 0
        self._misses = 0
        self._store: dict[str, str] = {}
        self._load_disk()

    def _key(self, text: str, src: str, tgt: str, backend: str) -> str:
        raw = f"{src}|{tgt}|{backend}|{text.strip().lower()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    def _disk_path(self) -> Path | None:
        if not self.app_dir:
            return None
        return (
            self.app_dir
            / "data"
            / "streamdub"
            / "projects"
            / self.project_id
            / "translation_memory.json"
        )

    def _load_disk(self) -> None:
        path = self._disk_path()
        if not path or not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._store.update({str(k): str(v) for k, v in data.items()})
        except Exception as exc:
            logger.debug("TM load failed: %s", exc)

    def save(self) -> None:
        path = self._disk_path()
        if not path:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._store, ensure_ascii=False, indent=2), encoding="utf-8")

    def lookup(self, text: str, src: str, tgt: str, backend: str) -> str | None:
        key = self._key(text, src, tgt, backend)
        hit = self._store.get(key)
        if hit:
            self._hits += 1
            return hit
        self._misses += 1
        return None

    def store(self, text: str, translation: str, src: str, tgt: str, backend: str) -> None:
        if not text.strip() or not translation.strip():
            return
        key = self._key(text, src, tgt, backend)
        self._store[key] = translation.strip()

    def stats(self) -> dict[str, Any]:
        total = self._hits + self._misses
        return {
            "hits": self._hits,
            "misses": self._misses,
            "entries": len(self._store),
            "hit_rate": round(self._hits / total, 3) if total else 0.0,
        }
