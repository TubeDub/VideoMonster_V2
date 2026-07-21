"""Entity Manager — consistent names/brands/terms across a project."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.streamdub.entities")


class EntityManager:
    """Project-scoped entity glossary with auto-extraction from source text."""

    def __init__(self, project_id: str, app_dir: Path | None = None):
        self.project_id = project_id
        self.app_dir = Path(app_dir) if app_dir else None
        self._entities: dict[str, str] = {}
        self._load()

    def _path(self) -> Path | None:
        if not self.app_dir:
            return None
        return (
            self.app_dir
            / "data"
            / "streamdub"
            / "projects"
            / self.project_id
            / "entities.json"
        )

    def _load(self) -> None:
        path = self._path()
        if path and path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._entities = {str(k): str(v) for k, v in data.items()}
            except Exception:
                pass
        if self.app_dir:
            try:
                from engines.proper_nouns_dict import preferred_translations

                prefs = preferred_translations(self.app_dir)
                for src, tgt in (prefs or {}).items():
                    self._entities.setdefault(str(src), str(tgt))
            except Exception:
                pass

    def save(self) -> None:
        path = self._path()
        if not path:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self._entities, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def register(self, source: str, translation: str) -> None:
        src = source.strip()
        tgt = translation.strip()
        if src and tgt:
            self._entities[src] = tgt

    def apply(self, text: str, *, original: str = "") -> str:
        out = text
        for src, tgt in sorted(self._entities.items(), key=lambda kv: -len(kv[0])):
            if len(src) < 2:
                continue
            out = re.sub(re.escape(src), tgt, out, flags=re.IGNORECASE)
        if original.strip() and self.app_dir:
            try:
                from engines.proper_nouns_dict import apply_proper_noun_polish

                out = apply_proper_noun_polish(original, out, app_dir=self.app_dir)
            except Exception:
                pass
        return out

    def extract_from_segments(self, segments: list[str]) -> None:
        for text in segments:
            for m in re.finditer(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3}\b", text):
                name = m.group(0)
                self._entities.setdefault(name, name)
            for m in re.finditer(r"\b[A-Z]{2,}\b", text):
                tok = m.group(0)
                self._entities.setdefault(tok, tok)

    def to_dict(self) -> dict[str, str]:
        return dict(self._entities)
