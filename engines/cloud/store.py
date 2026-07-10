"""Local persistence for cloud settings and projects."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from engines.cloud.models import CloudProject, ProjectVersion

_LOCK = threading.RLock()


class CloudStore:
    def __init__(self, app_dir: Path):
        self.app_dir = Path(app_dir)
        self.data_dir = self.app_dir / "data"
        self.projects_dir = self.app_dir / "projects" / "cloud"
        self.state_dir = self.app_dir / "output" / "dev" / "cloud"
        self.settings_path = self.data_dir / "cloud.local.json"
        self.projects_index = self.data_dir / "cloud_projects.json"
        for p in (self.data_dir, self.projects_dir, self.state_dir):
            p.mkdir(parents=True, exist_ok=True)

    def load_settings(self) -> dict[str, Any]:
        if not self.settings_path.is_file():
            return self.default_settings()
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            base = self.default_settings()
            base.update(data)
            if isinstance(data.get("providers"), dict):
                base["providers"].update(data["providers"])
            return base
        except Exception:
            return self.default_settings()

    def save_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        with _LOCK:
            cur = self.load_settings()
            for k, v in patch.items():
                if k == "providers" and isinstance(v, dict):
                    cur.setdefault("providers", {}).update(v)
                else:
                    cur[k] = v
            self.settings_path.write_text(
                json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return cur

    @staticmethod
    def default_settings() -> dict[str, Any]:
        return {
            "default_provider": "local",
            "default_storage_mode": "local_only",
            "backup_schedule": "manual",
            "cache_policy": "keep_all",
            "cache_keep_days": 7,
            "providers": {
                "local": {"enabled": True},
                "google_drive": {"enabled": False},
                "onedrive": {"enabled": False},
                "dropbox": {"enabled": False},
                "tubedub_cloud": {"enabled": False},
                "s3": {"enabled": False},
            },
        }

    def load_projects(self) -> dict[str, CloudProject]:
        if not self.projects_index.is_file():
            return {}
        try:
            raw = json.loads(self.projects_index.read_text(encoding="utf-8"))
        except Exception:
            return {}
        out: dict[str, CloudProject] = {}
        for row in raw.get("projects") or []:
            pid = str(row.get("project_id") or "")
            if not pid:
                continue
            versions = [
                ProjectVersion(**v) if isinstance(v, dict) else v
                for v in (row.get("versions") or [])
            ]
            row = {**row, "versions": versions}
            out[pid] = CloudProject(**{k: v for k, v in row.items() if k in CloudProject.__dataclass_fields__})
        return out

    def save_projects(self, projects: dict[str, CloudProject]) -> None:
        with _LOCK:
            payload = {"projects": [p.to_dict() for p in projects.values()]}
            self.projects_index.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    def upsert_project(self, project: CloudProject) -> CloudProject:
        projects = self.load_projects()
        projects[project.project_id] = project
        self.save_projects(projects)
        return project

    def get_project(self, project_id: str) -> CloudProject | None:
        return self.load_projects().get(project_id)

    def delete_project(self, project_id: str) -> bool:
        projects = self.load_projects()
        if project_id not in projects:
            return False
        del projects[project_id]
        self.save_projects(projects)
        return True
