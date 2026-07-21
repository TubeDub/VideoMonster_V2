"""P618 Performance Cache — skip identical synthesis."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any


def cache_key(
    *,
    text: str,
    voice_uuid: str,
    provider: str,
    rate: str | None,
    pitch: str | None,
    emotion: str,
    contract_version: str,
    settings: dict[str, Any] | None = None,
) -> str:
    payload = {
        "text": text,
        "voice_uuid": voice_uuid,
        "provider": provider,
        "rate": rate,
        "pitch": pitch,
        "emotion": emotion,
        "contract": contract_version,
        "settings": settings or {},
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class VoiceCache:
    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root or Path(__file__).resolve().parents[2] / "output" / "voice_cache")
        self.root.mkdir(parents=True, exist_ok=True)
        self._index_path = self.root / "index.json"
        self._index: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if self._index_path.is_file():
            try:
                self._index = json.loads(self._index_path.read_text(encoding="utf-8"))
            except Exception:
                self._index = {}

    def _save(self) -> None:
        self._index_path.write_text(
            json.dumps(self._index, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def lookup(self, key: str) -> Path | None:
        row = self._index.get(key)
        if not row:
            return None
        path = Path(row.get("path") or "")
        if path.is_file():
            return path
        self._index.pop(key, None)
        return None

    def store(self, key: str, src: Path | str, *, meta: dict[str, Any] | None = None) -> Path:
        src_p = Path(src)
        dest = self.root / f"{key[:16]}.wav"
        if src_p.suffix.lower() not in {".wav", ".wave"}:
            dest = self.root / f"{key[:16]}{src_p.suffix or '.bin'}"
        if src_p.resolve() != dest.resolve():
            shutil.copy2(src_p, dest)
        self._index[key] = {"path": str(dest), "meta": meta or {}}
        self._save()
        return dest

    def stats(self) -> dict[str, Any]:
        return {
            "entries": len(self._index),
            "root": str(self.root),
            "bytes": sum(
                Path(v["path"]).stat().st_size
                for v in self._index.values()
                if Path(v.get("path") or "").is_file()
            ),
        }
