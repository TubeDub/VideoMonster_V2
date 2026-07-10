"""Project record model (Storage Manager §2)."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engines.storage.migration import STORAGE_VERSION, migrate_project_data


@dataclass
class ProjectRecord:
    """Canonical on-disk project representation (``project.json``)."""

    project_id: str
    title: str = "New Project"
    storage_version: int = STORAGE_VERSION
    created_at: float = 0.0
    updated_at: float = 0.0
    last_opened_at: float = 0.0
    trashed: bool = False
    trashed_at: float = 0.0
    legacy_task_id: str = ""
    legacy_project_uuid: str = ""
    studio_session: dict[str, Any] = field(default_factory=dict)
    tdproj: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    source: str = "storage_manager"
    source_path: str = ""

    @classmethod
    def create(cls, *, title: str = "New Project", project_id: str | None = None) -> "ProjectRecord":
        now = time.time()
        return cls(
            project_id=project_id or str(uuid.uuid4()),
            title=title,
            storage_version=STORAGE_VERSION,
            created_at=now,
            updated_at=now,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProjectRecord":
        return cls(
            project_id=str(data.get("project_id") or ""),
            title=str(data.get("title") or "New Project"),
            storage_version=int(data.get("storage_version") or STORAGE_VERSION),
            created_at=float(data.get("created_at") or 0),
            updated_at=float(data.get("updated_at") or 0),
            last_opened_at=float(data.get("last_opened_at") or 0),
            trashed=bool(data.get("trashed")),
            trashed_at=float(data.get("trashed_at") or 0),
            legacy_task_id=str(data.get("legacy_task_id") or ""),
            legacy_project_uuid=str(data.get("legacy_project_uuid") or ""),
            studio_session=dict(data.get("studio_session") or {}),
            tdproj=dict(data.get("tdproj") or {}),
            metadata=dict(data.get("metadata") or {}),
            source=str(data.get("source") or ""),
            source_path=str(data.get("source_path") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "title": self.title,
            "storage_version": self.storage_version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_opened_at": self.last_opened_at,
            "trashed": self.trashed,
            "trashed_at": self.trashed_at,
            "legacy_task_id": self.legacy_task_id,
            "legacy_project_uuid": self.legacy_project_uuid,
            "studio_session": self.studio_session,
            "tdproj": self.tdproj,
            "metadata": self.metadata,
            "source": self.source,
            "source_path": self.source_path,
        }

    def load_migrated(self, project_dir: Path) -> "ProjectRecord":
        data = migrate_project_data(self.to_dict(), project_dir)
        return ProjectRecord.from_dict(data)

    def disk_size_bytes(self, project_dir: Path) -> int:
        total = 0
        if project_dir.is_dir():
            for p in project_dir.rglob("*"):
                if p.is_file():
                    try:
                        total += p.stat().st_size
                    except OSError:
                        pass
        return total

    def stats(self, project_dir: Path) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "title": self.title,
            "storage_version": self.storage_version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_opened_at": self.last_opened_at,
            "trashed": self.trashed,
            "trashed_at": self.trashed_at,
            "size_bytes": self.disk_size_bytes(project_dir),
            "legacy_task_id": self.legacy_task_id,
            "legacy_project_uuid": self.legacy_project_uuid,
        }
