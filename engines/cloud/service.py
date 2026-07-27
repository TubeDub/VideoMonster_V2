"""Cloud Platform orchestrator."""

from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path
from typing import Any

from engines.cloud.backup import BackupScheduler
from engines.cloud.cache_policy import CachePolicyEngine
from engines.cloud.config import cloud_config, cloud_platform_enabled
from engines.cloud.manager import CloudFileManager
from engines.cloud.models import StorageMode, SyncState
from engines.cloud.remote_jobs import RemoteJobQueue
from engines.cloud.store import CloudStore
from engines.cloud.sync.checksum import verify_sha256
from engines.cloud.sync.multipart import TransferState
from engines.cloud.sync.queue import BackgroundSyncQueue, SyncTask

_SERVICES: dict[str, "CloudPlatformService"] = {}
_LOCK = threading.RLock()


class CloudPlatformService:
    def __init__(self, app_dir: Path):
        self.app_dir = Path(app_dir)
        cfg = cloud_config()
        self.store = CloudStore(self.app_dir)
        self.manager = CloudFileManager(self.app_dir, self.store)
        self.cache = CachePolicyEngine(self.app_dir, self.store)
        self.remote_jobs = RemoteJobQueue(app_dir)
        self.transfer_state = TransferState(self.store.state_dir / "transfers")
        self.queue = BackgroundSyncQueue(self.app_dir, max_workers=cfg["max_workers"])
        self._wire_queue()
        self.backup = BackupScheduler(self.app_dir, self.store, self.enqueue_backup_all)
        self.backup.start()

    def _wire_queue(self) -> None:
        self.queue.register_runner("upload", self._run_upload)
        self.queue.register_runner("download", self._run_download)
        self.queue.register_runner("sync_project", self._run_sync_project)

    def status(self) -> dict[str, Any]:
        settings = self.store.load_settings()
        providers = self.manager.list_providers_status()
        projects = self.store.load_projects()
        return {
            "enabled": cloud_platform_enabled(),
            "default_provider": settings.get("default_provider", "local"),
            "default_storage_mode": settings.get("default_storage_mode", StorageMode.LOCAL_ONLY.value),
            "backup": self.backup.snapshot(),
            "cache_policy": settings.get("cache_policy"),
            "providers": providers,
            "projects_count": len(projects),
            "sync_queue": self.queue.list_tasks(20),
            "storage_modes": [m.value for m in StorageMode],
            "remote_jobs_enabled": cloud_config().get("remote_jobs_enabled"),
        }

    def list_projects(self) -> list[dict[str, Any]]:
        return [p.to_dict() for p in self.store.load_projects().values()]

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        p = self.store.get_project(project_id)
        return p.to_dict() if p else None

    def update_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        saved = self.store.save_settings(patch)
        if patch.get("backup_schedule"):
            self.backup.start()
        return saved

    def post_dub_action(
        self,
        filename: str,
        action: str,
        *,
        provider_id: str | None = None,
        title: str = "",
        subtitle_file: str | None = None,
    ) -> dict[str, Any]:
        """Handle post-dub user choice: keep_local | cloud | both | cloud_and_delete_local."""
        settings = self.store.load_settings()
        provider_id = provider_id or settings.get("default_provider") or "local"
        action = (action or "keep_local").strip().lower()

        if action == "keep_local":
            project = self.manager.register_output_file(
                filename,
                title=title,
                storage_mode=StorageMode.LOCAL_ONLY.value,
                provider_id="local",
            )
            return {"ok": True, "action": action, "project": project.to_dict()}

        files = [filename]
        if subtitle_file:
            files.append(subtitle_file)

        mode = StorageMode.CLOUD_ONLY.value
        if action in ("both", "local_and_cloud"):
            mode = StorageMode.LOCAL_AND_CLOUD.value
        elif action == "cloud_and_delete_local":
            mode = StorageMode.AUTO_SYNC.value

        project = self.manager.register_output_file(
            filename,
            title=title,
            storage_mode=mode,
            provider_id=provider_id,
        )
        if subtitle_file and subtitle_file not in project.local_paths:
            project.local_paths.append(subtitle_file)
            self.store.upsert_project(project)

        task = self.queue.enqueue(
            "sync_project",
            project_id=project.project_id,
            provider_id=provider_id,
            meta={"action": action, "files": files, "delete_local_after": action == "cloud_and_delete_local"},
        )

        proj = self.store.get_project(project.project_id)
        if proj:
            proj.sync_state = SyncState.QUEUED.value
            self.store.upsert_project(proj)

        return {
            "ok": True,
            "action": action,
            "project": project.to_dict(),
            "task_id": task.task_id,
        }

    def enqueue_upload(self, local_path: str, remote_path: str, *, provider_id: str = "local") -> dict[str, Any]:
        task = self.queue.enqueue(
            "upload",
            local_path=local_path,
            remote_path=remote_path,
            provider_id=provider_id,
        )
        return task.to_dict()

    def enqueue_download(self, remote_path: str, local_path: str, *, provider_id: str = "local") -> dict[str, Any]:
        task = self.queue.enqueue(
            "download",
            remote_path=remote_path,
            local_path=local_path,
            provider_id=provider_id,
        )
        return task.to_dict()

    def enqueue_backup_all(self) -> None:
        for project in self.store.load_projects().values():
            self.queue.enqueue(
                "sync_project",
                project_id=project.project_id,
                provider_id=project.provider_id,
                meta={"backup": True},
            )

    def apply_cache_policy(self, synced_files: list[str] | None = None) -> dict:
        return self.cache.apply(synced_files=synced_files)

    def submit_remote_job(self, kind: str, **kwargs: Any) -> dict[str, Any]:
        job = self.remote_jobs.submit(kind, **kwargs)
        return job.to_dict()

    # ── queue runners ─────────────────────────────────────

    def _run_upload(self, task: SyncTask, progress_cb) -> None:
        local = Path(task.local_path)
        if not local.is_file():
            local = self.app_dir / "output" / task.local_path
        if not local.is_file():
            raise FileNotFoundError(task.local_path)
        prov = self.manager.provider(task.provider_id)
        size = local.stat().st_size
        task.bytes_total = size
        t0 = time.perf_counter()
        last = t0

        def _p(phase: str, frac: float, meta: dict) -> None:
            nonlocal last
            now = time.perf_counter()
            task.progress = frac
            task.bytes_done = int(size * frac)
            dt = max(now - last, 0.001)
            task.speed_bps = (task.bytes_done - getattr(task, "_last_done", 0)) / dt
            task._last_done = task.bytes_done  # type: ignore[attr-defined]
            last = now
            progress_cb(task)

        entry = prov.upload_file(local, task.remote_path, progress=_p, resume_token=task.task_id)
        if entry.sha256 and not verify_sha256(local, entry.sha256):
            raise ValueError("Checksum mismatch after upload")
        self.transfer_state.clear(task.task_id)

    def _run_download(self, task: SyncTask, progress_cb) -> None:
        prov = self.manager.provider(task.provider_id)
        dest = Path(task.local_path)
        if not dest.is_absolute():
            dest = self.app_dir / "output" / task.local_path
        entry = prov.download_file(task.remote_path, dest, progress=lambda *_: progress_cb(task))
        task.progress = 1.0
        task.bytes_done = entry.size_bytes
        task.bytes_total = entry.size_bytes

    def _run_sync_project(self, task: SyncTask, progress_cb) -> None:
        project = self.store.get_project(task.project_id)
        if not project:
            raise KeyError(task.project_id)
        prov = self.manager.provider(task.provider_id or project.provider_id)
        files = (task.meta or {}).get("files") or project.local_paths
        total = max(len(files), 1)
        uploaded: list[str] = []

        for i, name in enumerate(files):
            local = self.app_dir / "output" / name
            if not local.is_file():
                continue
            remote = f"{project.remote_prefix}/{name}".replace("//", "/")
            task.local_path = str(local)
            task.remote_path = remote
            task.progress = i / total
            progress_cb(task)
            prov.upload_file(local, remote)
            uploaded.append(name)

        project.sync_state = SyncState.SYNCED.value
        project.updated_ms = int(time.time() * 1000)
        self.store.upsert_project(project)

        if (task.meta or {}).get("delete_local_after"):
            cleanup = self.cache.apply(synced_files=uploaded)
            task.meta["cache_cleanup"] = cleanup

        task.progress = 1.0
        progress_cb(task)


def get_cloud_service(app_dir: Path | None = None) -> CloudPlatformService:
    base = Path(app_dir or Path(__file__).resolve().parents[2])
    key = str(base.resolve())
    with _LOCK:
        if key not in _SERVICES:
            _SERVICES[key] = CloudPlatformService(base)
        return _SERVICES[key]
