"""Storage path resolution (Storage Manager §1)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StoragePaths:
    """All on-disk locations owned by StorageManager."""

    app_dir: Path
    output_dir: Path
    projects_root: Path
    trash_root: Path
    index_path: Path
    trash_index_path: Path
    recovery_path: Path
    locks_dir: Path
    events_journal: Path
    legacy_studio_sessions: Path
    legacy_tdproj_root: Path
    legacy_tdproj_index: Path

    @classmethod
    def resolve(cls, app_dir: str | Path) -> "StoragePaths":
        root = Path(app_dir).resolve()
        output = Path(os.getenv("OUTPUT_DIR", str(root / "output")))
        if not output.is_absolute():
            output = (root / output).resolve()
        projects = root / "projects" / "vm_storage"
        trash = projects / ".trash"
        data = root / "data"
        return cls(
            app_dir=root,
            output_dir=output,
            projects_root=projects,
            trash_root=trash,
            index_path=data / "storage_index.json",
            trash_index_path=data / "storage_trash_index.json",
            recovery_path=data / "storage_recovery.json",
            locks_dir=data / "storage_locks",
            events_journal=data / "storage_events.jsonl",
            legacy_studio_sessions=output / "studio_sessions",
            legacy_tdproj_root=root / "projects" / "tdproj",
            legacy_tdproj_index=data / "tdproj_index.json",
        )

    def project_dir(self, project_id: str, *, trashed: bool = False) -> Path:
        base = self.trash_root if trashed else self.projects_root
        return base / project_id

    def project_json(self, project_id: str, *, trashed: bool = False) -> Path:
        return self.project_dir(project_id, trashed=trashed) / "project.json"

    def project_lock(self, project_id: str) -> Path:
        return self.locks_dir / f"{project_id}.lock"

    def ensure_dirs(self) -> None:
        for d in (
            self.output_dir,
            self.projects_root,
            self.trash_root,
            self.index_path.parent,
            self.locks_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)
