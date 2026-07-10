"""Unified .tdproj project storage."""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from engines.tubedub.project.model import TDPROJ_EXTENSION, TdProject

_LOCK = threading.RLock()


class TdProjectStore:
    def __init__(self, app_dir: Path):
        self.app_dir = Path(app_dir)
        self.root = self.app_dir / "projects" / "tdproj"
        self.index_path = self.app_dir / "data" / "tdproj_index.json"
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path.parent.mkdir(parents=True, exist_ok=True)

    def _project_path(self, project_id: str, title: str = "") -> Path:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in (title or project_id))[:64]
        return self.root / project_id / f"{safe}{TDPROJ_EXTENSION}"

    def save(self, project: TdProject) -> TdProject:
        with _LOCK:
            project.updated_ms = int(time.time() * 1000)
            if not project.created_ms:
                project.created_ms = project.updated_ms
            path = self._project_path(project.project_id, project.title)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(project.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            idx = self._load_index()
            idx[project.project_id] = {
                "project_id": project.project_id,
                "title": project.title,
                "path": str(path.relative_to(self.app_dir)),
                "updated_ms": project.updated_ms,
            }
            self.index_path.write_text(
                json.dumps({"projects": idx}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return project

    def load(self, project_id: str) -> TdProject | None:
        idx = self._load_index()
        row = idx.get(project_id)
        if not row:
            return None
        path = self.app_dir / row["path"]
        if not path.is_file():
            alt = self.root / project_id
            files = list(alt.glob(f"*{TDPROJ_EXTENSION}")) if alt.is_dir() else []
            if not files:
                return None
            path = files[0]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return TdProject.from_dict(data)
        except Exception:
            return None

    def load_by_path(self, path: Path) -> TdProject | None:
        p = Path(path)
        if not p.is_file():
            return None
        try:
            return TdProject.from_dict(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            return None

    def list_projects(self) -> list[dict[str, Any]]:
        return list(self._load_index().values())

    def create_empty(self, *, title: str = "New Project") -> TdProject:
        now = int(time.time() * 1000)
        project = TdProject(
            project_id=str(uuid.uuid4()),
            title=title,
            created_ms=now,
            updated_ms=now,
        )
        return self.save(project)

    def delete(self, project_id: str) -> bool:
        with _LOCK:
            idx = self._load_index()
            if project_id not in idx:
                return False
            row = idx.pop(project_id)
            path = self.app_dir / row.get("path", "")
            if path.is_file():
                path.unlink(missing_ok=True)
            self.index_path.write_text(
                json.dumps({"projects": idx}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return True

    def _load_index(self) -> dict[str, Any]:
        if not self.index_path.is_file():
            return {}
        try:
            return dict(json.loads(self.index_path.read_text(encoding="utf-8")).get("projects") or {})
        except Exception:
            return {}


_STORES: dict[str, TdProjectStore] = {}


def get_project_store(app_dir: Path) -> TdProjectStore:
    key = str(Path(app_dir).resolve())
    with _LOCK:
        if key not in _STORES:
            _STORES[key] = TdProjectStore(Path(app_dir))
        return _STORES[key]
