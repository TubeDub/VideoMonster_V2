"""P714–P716 Cloud Projects / Assets / Backup façade."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from engines.platform_sdk.event_bus import get_platform_bus
from engines.platform_sdk.types import PlatformEvent

ROOT = Path(__file__).resolve().parents[2]
CLOUD_DIR = ROOT / "data" / "platform_cloud"


class CloudFacade:
    """Safe sync surface — wraps engines.cloud when available, else local store."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root or CLOUD_DIR)
        self.root.mkdir(parents=True, exist_ok=True)
        self.projects_dir = self.root / "projects"
        self.assets_dir = self.root / "assets"
        self.backups_dir = self.root / "backups"
        for d in (self.projects_dir, self.assets_dir, self.backups_dir):
            d.mkdir(parents=True, exist_ok=True)

    def save_project(self, project_id: str, payload: dict[str, Any]) -> Path:
        path = self.projects_dir / f"{project_id}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        get_platform_bus().publish(PlatformEvent.PROJECT_OPENED, {"project_id": project_id, "action": "save"})
        try:
            from engines.cloud.service import CloudPlatformService

            # Best-effort remote push if cloud platform enabled
            svc = CloudPlatformService()
            if hasattr(svc, "save_project_meta"):
                svc.save_project_meta(project_id, payload)
        except Exception:
            pass
        return path

    def open_project(self, project_id: str) -> dict[str, Any] | None:
        path = self.projects_dir / f"{project_id}.json"
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        get_platform_bus().publish(PlatformEvent.PROJECT_OPENED, {"project_id": project_id})
        return data

    def sync_project(self, project_id: str) -> dict[str, Any]:
        local = self.open_project(project_id)
        return {
            "ok": local is not None,
            "project_id": project_id,
            "synced_at": time.time(),
            "mode": "local",
        }

    def sync_assets(self, kind: str, items: dict[str, Any]) -> Path:
        """P715 — dictionaries, voices, configs, terminology, profiles."""
        path = self.assets_dir / f"{kind}.json"
        existing = {}
        if path.is_file():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                existing = {}
        existing.update(items)
        path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def backup_project(self, project_id: str) -> Path:
        """P716 — versioned backup with rollback support."""
        src = self.projects_dir / f"{project_id}.json"
        if not src.is_file():
            raise FileNotFoundError(project_id)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        dest_dir = self.backups_dir / project_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{stamp}.json"
        shutil.copy2(src, dest)
        # Keep history index
        hist = dest_dir / "history.json"
        rows = []
        if hist.is_file():
            try:
                rows = json.loads(hist.read_text(encoding="utf-8"))
            except Exception:
                rows = []
        rows.append({"stamp": stamp, "path": str(dest), "ts": time.time()})
        hist.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        return dest

    def list_backups(self, project_id: str) -> list[dict[str, Any]]:
        hist = self.backups_dir / project_id / "history.json"
        if not hist.is_file():
            return []
        return json.loads(hist.read_text(encoding="utf-8"))

    def rollback(self, project_id: str, stamp: str) -> Path:
        src = self.backups_dir / project_id / f"{stamp}.json"
        if not src.is_file():
            raise FileNotFoundError(stamp)
        dest = self.projects_dir / f"{project_id}.json"
        shutil.copy2(src, dest)
        return dest


_CLOUD: CloudFacade | None = None


def get_cloud_facade(**kwargs: Any) -> CloudFacade:
    global _CLOUD
    if _CLOUD is None or kwargs:
        _CLOUD = CloudFacade(**kwargs)
    return _CLOUD
