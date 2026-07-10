"""Local cache cleanup policies."""

from __future__ import annotations

import time
from pathlib import Path

from engines.cloud.models import CachePolicy
from engines.cloud.store import CloudStore


class CachePolicyEngine:
    def __init__(self, app_dir: Path, store: CloudStore):
        self.app_dir = Path(app_dir)
        self.store = store
        self.output_dir = self.app_dir / "output"

    def apply(self, *, synced_files: list[str] | None = None) -> dict:
        settings = self.store.load_settings()
        policy = str(settings.get("cache_policy") or CachePolicy.KEEP_ALL.value)
        removed: list[str] = []

        if policy == CachePolicy.KEEP_ALL.value:
            return {"policy": policy, "removed": removed}

        if policy == CachePolicy.DELETE_AFTER_SYNC.value and synced_files:
            for name in synced_files:
                p = self.output_dir / name
                if p.is_file():
                    p.unlink()
                    removed.append(name)

        elif policy == CachePolicy.KEEP_DAYS.value:
            days = int(settings.get("cache_keep_days") or 7)
            cutoff = time.time() - days * 86400
            for p in self.output_dir.iterdir():
                if p.is_file() and p.stat().st_mtime < cutoff:
                    p.unlink()
                    removed.append(p.name)

        elif policy == CachePolicy.LAST_PROJECT_ONLY.value:
            projects = self.store.load_projects()
            if not projects:
                return {"policy": policy, "removed": removed}
            latest = max(projects.values(), key=lambda x: x.updated_ms)
            keep = set(latest.local_paths)
            for p in self.output_dir.iterdir():
                if p.is_file() and p.name not in keep:
                    p.unlink()
                    removed.append(p.name)

        return {"policy": policy, "removed": removed}
