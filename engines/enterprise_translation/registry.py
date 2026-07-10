"""Placeholder Registry — centralized entity storage."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from engines.enterprise_translation.config import REGISTRY_FILE
from engines.enterprise_translation.types import EntityRecord, EntityType

_COUNTERS: dict[str, int] = {}


def _registry_path(app_dir: Path) -> Path:
    return app_dir / "data" / REGISTRY_FILE


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _next_id(entity_type: EntityType) -> str:
    key = entity_type.value
    _COUNTERS[key] = _COUNTERS.get(key, 0) + 1
    return f"{key}_{_COUNTERS[key]}"


class PlaceholderRegistry:
    """In-memory registry for one segment/job; optionally persisted per app."""

    def __init__(self, app_dir: Path | None = None):
        self.app_dir = app_dir
        self._by_id: dict[str, EntityRecord] = {}
        self._by_original: dict[str, str] = {}

    def register(
        self,
        original: str,
        entity_type: EntityType,
        *,
        display: str = "",
        aliases: list[str] | None = None,
        meta: dict | None = None,
    ) -> EntityRecord:
        norm = _normalize(original)
        if norm in self._by_original:
            return self._by_id[self._by_original[norm]]

        eid = _next_id(entity_type)
        rec = EntityRecord(
            entity_id=eid,
            entity_type=entity_type,
            original=original.strip(),
            normalized=norm,
            display=display or original.strip(),
            aliases=list(aliases or []),
            restore_variants=[original.strip()] + list(aliases or []),
            meta=dict(meta or {}),
        )
        self._by_id[eid] = rec
        self._by_original[norm] = eid
        return rec

    def get(self, entity_id: str) -> EntityRecord | None:
        return self._by_id.get(entity_id)

    def all_records(self) -> list[EntityRecord]:
        return list(self._by_id.values())

    def token_to_entity_id(self, token: str) -> str | None:
        """Map serialized token back to entity id."""
        t = str(token or "").strip()
        for eid in self._by_id:
            if eid in t or t.endswith(eid) or t.strip("[](){}<>") == eid:
                return eid
        return None

    def save_session(self) -> None:
        if not self.app_dir or not self._by_id:
            return
        path = _registry_path(self.app_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "entities": [r.to_dict() for r in self._by_id.values()],
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load_session(cls, app_dir: Path) -> PlaceholderRegistry:
        reg = cls(app_dir)
        path = _registry_path(app_dir)
        if not path.is_file():
            return reg
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for row in data.get("entities") or []:
                rec = EntityRecord.from_dict(row)
                reg._by_id[rec.entity_id] = rec
                reg._by_original[rec.normalized] = rec.entity_id
        except Exception:
            pass
        return reg
