"""Project Memory — style, characters, context for consistent dubbing."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ProjectMemory:
    project_id: str
    app_dir: Path | None = None
    style: str = "neutral"
    formality: str = "default"
    characters: dict[str, str] = field(default_factory=dict)
    context_notes: list[str] = field(default_factory=list)
    glossary: dict[str, str] = field(default_factory=dict)

    def _path(self) -> Path | None:
        if not self.app_dir:
            return None
        return (
            self.app_dir
            / "data"
            / "streamdub"
            / "projects"
            / self.project_id
            / "project_memory.json"
        )

    def load(self) -> None:
        path = self._path()
        if not path or not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.style = str(data.get("style") or self.style)
            self.formality = str(data.get("formality") or self.formality)
            self.characters = dict(data.get("characters") or {})
            self.context_notes = list(data.get("context_notes") or [])
            self.glossary = dict(data.get("glossary") or {})
        except Exception:
            pass

    def save(self) -> None:
        path = self._path()
        if not path:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "style": self.style,
                    "formality": self.formality,
                    "characters": self.characters,
                    "context_notes": self.context_notes,
                    "glossary": self.glossary,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "style": self.style,
            "formality": self.formality,
            "characters": dict(self.characters),
            "context_notes": list(self.context_notes),
            "glossary": dict(self.glossary),
        }
