"""Termbase — LOCKED entity registry (broadcast-grade)."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class EntityStatus(str, Enum):
    LOCKED = "LOCKED"


class EntityKind(str, Enum):
    PERSON = "PERSON"
    ORG = "ORG"
    TITLE = "TITLE"
    PLACE = "PLACE"
    PRODUCT = "PRODUCT"
    COMPANY = "COMPANY"
    EVENT = "EVENT"
    DATE = "DATE"
    OTHER = "OTHER"


@dataclass
class TermEntry:
    term_id: int
    kind: EntityKind
    original: str
    display: str = ""
    status: EntityStatus = EntityStatus.LOCKED
    aliases: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def token(self) -> str:
        from engines.broadcast.config import TOKEN_PREFIX, TOKEN_SUFFIX

        return f"{TOKEN_PREFIX}{self.term_id}{TOKEN_SUFFIX}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "term_id": self.term_id,
            "kind": self.kind.value,
            "original": self.original,
            "display": self.display or self.original,
            "status": self.status.value,
            "aliases": self.aliases,
            "meta": self.meta,
        }


class Termbase:
    """Single source of truth for sacred entities — all LOCKED before MT."""

    def __init__(self, app_dir: Path | None = None):
        self.app_dir = app_dir
        self._entries: dict[int, TermEntry] = {}
        self._by_original: dict[str, int] = {}
        self._next_id = 1

    def _normalize(self, text: str) -> str:
        return re.sub(r"\s+", " ", str(text or "").strip().lower())

    def register(
        self,
        original: str,
        kind: EntityKind,
        *,
        display: str = "",
        aliases: list[str] | None = None,
    ) -> TermEntry:
        norm = self._normalize(original)
        if norm in self._by_original:
            return self._entries[self._by_original[norm]]

        tid = self._next_id
        self._next_id += 1
        entry = TermEntry(
            term_id=tid,
            kind=kind,
            original=original.strip(),
            display=display or original.strip(),
            status=EntityStatus.LOCKED,
            aliases=list(aliases or []) + [original.strip()],
        )
        self._entries[tid] = entry
        self._by_original[norm] = tid
        return entry

    def get(self, term_id: int) -> TermEntry | None:
        return self._entries.get(term_id)

    def all_locked(self) -> list[TermEntry]:
        return [e for e in self._entries.values() if e.status == EntityStatus.LOCKED]

    def save(self) -> None:
        if not self.app_dir:
            return
        path = self.app_dir / "data" / "broadcast_termbase.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "entries": [e.to_dict() for e in self._entries.values()],
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
