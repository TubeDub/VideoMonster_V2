"""Module catalog — declarative registry of all TubeDub modules."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engines.tubedub.release import ReleaseChannel, parse_release_channel


@dataclass
class ModuleCatalogEntry:
    id: str
    label: str
    release_channel: str = ReleaseChannel.DISABLED.value
    feature_id: str = ""
    api_namespace: str = ""
    adapter: str = ""
    dependencies: list[str] = field(default_factory=list)
    pipeline_stages: list[str] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "release_channel": self.release_channel,
            "feature_id": self.feature_id,
            "api_namespace": self.api_namespace,
            "adapter": self.adapter,
            "dependencies": list(self.dependencies),
            "pipeline_stages": list(self.pipeline_stages),
            "description": self.description,
        }


class ModuleCatalog:
    def __init__(self, app_dir: Path):
        self.app_dir = Path(app_dir)
        self._path = self.app_dir / "data" / "tubedub_modules.json"
        self._entries: dict[str, ModuleCatalogEntry] = {}
        self.reload()

    def reload(self) -> None:
        if not self._path.is_file():
            self._entries = {}
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            self._entries = {}
            return
        merged: dict[str, ModuleCatalogEntry] = {}
        for row in raw.get("modules") or []:
            entry = self._parse(row)
            if entry:
                merged[entry.id] = entry
        self._entries = merged

    @staticmethod
    def _parse(row: dict[str, Any]) -> ModuleCatalogEntry | None:
        mid = str(row.get("id") or "").strip()
        if not mid:
            return None
        ch = parse_release_channel(str(row.get("release_channel") or "DISABLED"))
        return ModuleCatalogEntry(
            id=mid,
            label=str(row.get("label") or mid),
            release_channel=ch.value,
            feature_id=str(row.get("feature_id") or mid),
            api_namespace=str(row.get("api_namespace") or mid),
            adapter=str(row.get("adapter") or ""),
            dependencies=[str(x) for x in (row.get("dependencies") or [])],
            pipeline_stages=[str(x) for x in (row.get("pipeline_stages") or [])],
            description=str(row.get("description") or ""),
        )

    def get(self, module_id: str) -> ModuleCatalogEntry | None:
        return self._entries.get(module_id)

    def all(self) -> list[ModuleCatalogEntry]:
        return sorted(self._entries.values(), key=lambda e: e.id)

    def visible(
        self,
        *,
        developer_session: bool,
        user_mode: str = "basic",
    ) -> list[ModuleCatalogEntry]:
        from engines.tubedub.release import channel_visible

        out: list[ModuleCatalogEntry] = []
        for e in self.all():
            ch = parse_release_channel(e.release_channel)
            if channel_visible(ch, developer_session=developer_session, user_mode=user_mode):
                out.append(e)
        return out
