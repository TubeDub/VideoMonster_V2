"""Cloud file manager — browse, search, move, archive."""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from engines.cloud.models import CloudProject, ProjectVersion, StorageMode, SyncState
from engines.cloud.providers import PROVIDER_REGISTRY
from engines.cloud.store import CloudStore
from engines.cloud.sync.checksum import file_sha256


class CloudFileManager:
    def __init__(self, app_dir: Path, store: CloudStore):
        self.app_dir = Path(app_dir)
        self.store = store
        self.output_dir = self.app_dir / "output"

    def provider(self, provider_id: str):
        settings = self.store.load_settings()
        prov_cfg = (settings.get("providers") or {}).get(provider_id) or {}
        cls = PROVIDER_REGISTRY.get(provider_id)
        if not cls:
            raise ValueError(f"Unknown provider: {provider_id}")
        return cls(self.app_dir, prov_cfg)

    def list_providers_status(self) -> list[dict[str, Any]]:
        settings = self.store.load_settings()
        out = []
        for pid, cls in PROVIDER_REGISTRY.items():
            prov_cfg = (settings.get("providers") or {}).get(pid) or {}
            if pid != "local" and not prov_cfg.get("enabled"):
                out.append(
                    {
                        "provider_id": pid,
                        "label": cls.label,
                        "connected": False,
                        "enabled": False,
                    }
                )
                continue
            try:
                st = cls(self.app_dir, prov_cfg).connect()
                out.append({**st.to_dict(), "enabled": True})
            except Exception as e:
                out.append(
                    {
                        "provider_id": pid,
                        "label": cls.label,
                        "connected": False,
                        "enabled": prov_cfg.get("enabled", pid == "local"),
                        "error": str(e)[:200],
                    }
                )
        return out

    def search_files(self, query: str, *, provider_id: str = "local") -> list[dict[str, Any]]:
        q = (query or "").strip().lower()
        files = self.provider(provider_id).list_files()
        rows = [f.to_dict() for f in files]
        if not q:
            return rows
        return [r for r in rows if q in r.get("path", "").lower()]

    def register_output_file(
        self,
        filename: str,
        *,
        title: str = "",
        storage_mode: str = StorageMode.LOCAL_ONLY.value,
        provider_id: str = "local",
    ) -> CloudProject:
        path = self.output_dir / filename
        if not path.is_file():
            raise FileNotFoundError(filename)
        now = int(time.time() * 1000)
        pid = str(uuid.uuid4())
        ver = ProjectVersion(
            version_id=str(uuid.uuid4()),
            created_ms=now,
            label="v1",
            files=[filename],
        )
        project = CloudProject(
            project_id=pid,
            title=title or path.stem,
            storage_mode=storage_mode,
            provider_id=provider_id,
            local_paths=[filename],
            remote_prefix=f"projects/{pid}",
            sync_state=SyncState.IDLE.value,
            versions=[ver],
            created_ms=now,
            updated_ms=now,
            meta={"sha256": file_sha256(path), "size_bytes": path.stat().st_size},
        )
        return self.store.upsert_project(project)

    def add_version(self, project_id: str, filenames: list[str], *, label: str = "") -> CloudProject:
        project = self.store.get_project(project_id)
        if not project:
            raise KeyError(project_id)
        now = int(time.time() * 1000)
        ver = ProjectVersion(
            version_id=str(uuid.uuid4()),
            created_ms=now,
            label=label or f"v{len(project.versions) + 1}",
            files=list(filenames),
        )
        project.versions.append(ver)
        project.local_paths = list(dict.fromkeys(project.local_paths + filenames))
        project.updated_ms = now
        return self.store.upsert_project(project)

    def restore_version(self, project_id: str, version_id: str) -> CloudProject:
        project = self.store.get_project(project_id)
        if not project:
            raise KeyError(project_id)
        ver = next((v for v in project.versions if v.version_id == version_id), None)
        if not ver:
            raise KeyError(version_id)
        project.local_paths = list(ver.files)
        project.updated_ms = int(time.time() * 1000)
        project.meta["restored_version"] = version_id
        return self.store.upsert_project(project)

    def rename_project(self, project_id: str, title: str) -> CloudProject:
        project = self.store.get_project(project_id)
        if not project:
            raise KeyError(project_id)
        project.title = title.strip() or project.title
        project.updated_ms = int(time.time() * 1000)
        return self.store.upsert_project(project)

    def delete_project(self, project_id: str, *, delete_local: bool = False) -> bool:
        project = self.store.get_project(project_id)
        if not project:
            return False
        if delete_local:
            for name in project.local_paths:
                p = self.output_dir / name
                if p.is_file():
                    p.unlink()
            try:
                self.provider(project.provider_id).delete_file(project.remote_prefix)
            except Exception:
                pass
        return self.store.delete_project(project_id)

    def move_between_providers(
        self,
        project_id: str,
        dest_provider_id: str,
    ) -> CloudProject:
        project = self.store.get_project(project_id)
        if not project:
            raise KeyError(project_id)
        src = self.provider(project.provider_id)
        dst = self.provider(dest_provider_id)
        for name in project.local_paths:
            local = self.output_dir / name
            if not local.is_file():
                continue
            remote = f"{project.remote_prefix}/{name}".replace("//", "/")
            src.upload_file(local, remote)
            dst.upload_file(local, remote)
        project.provider_id = dest_provider_id
        project.updated_ms = int(time.time() * 1000)
        return self.store.upsert_project(project)
