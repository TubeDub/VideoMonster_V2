"""Cloud Platform data models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class StorageMode(str, Enum):
    LOCAL_ONLY = "local_only"
    CLOUD_ONLY = "cloud_only"
    LOCAL_AND_CLOUD = "local_and_cloud"
    AUTO_SYNC = "auto_sync"


class BackupSchedule(str, Enum):
    MANUAL = "manual"
    EVERY_30_MIN = "every_30_min"
    HOURLY = "hourly"
    DAILY = "daily"


class CachePolicy(str, Enum):
    KEEP_ALL = "keep_all"
    DELETE_AFTER_SYNC = "delete_after_sync"
    KEEP_DAYS = "keep_days"
    LAST_PROJECT_ONLY = "last_project_only"


class SyncState(str, Enum):
    IDLE = "idle"
    QUEUED = "queued"
    UPLOADING = "uploading"
    DOWNLOADING = "downloading"
    SYNCED = "synced"
    ERROR = "error"
    PAUSED = "paused"


class RemoteJobKind(str, Enum):
    TRANSLATE = "translate"
    WHISPER = "whisper"
    TTS = "tts"
    RENDER = "render"
    DUB = "dub"
    AUDIO = "audio"


class RemoteJobTarget(str, Enum):
    LOCAL = "local"
    CLOUD = "cloud"


@dataclass
class CloudFileEntry:
    path: str
    size_bytes: int = 0
    sha256: str = ""
    mime: str = ""
    modified_ms: int = 0
    remote_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProjectVersion:
    version_id: str
    created_ms: int
    label: str = ""
    files: list[str] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CloudProject:
    project_id: str
    title: str
    storage_mode: str = StorageMode.LOCAL_ONLY.value
    provider_id: str = "local"
    local_paths: list[str] = field(default_factory=list)
    remote_prefix: str = ""
    sync_state: str = SyncState.IDLE.value
    versions: list[ProjectVersion] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    created_ms: int = 0
    updated_ms: int = 0
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["versions"] = [v.to_dict() if hasattr(v, "to_dict") else v for v in self.versions]
        return d


@dataclass
class ProviderStatus:
    provider_id: str
    label: str
    connected: bool
    total_bytes: int | None = None
    used_bytes: int | None = None
    free_bytes: int | None = None
    upload_bps: float | None = None
    download_bps: float | None = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


PROVIDER_LABELS = {
    "local": "Local Disk",
    "google_drive": "Google Drive",
    "onedrive": "OneDrive",
    "dropbox": "Dropbox",
    "tubedub_cloud": "TubeDub Cloud",
    "s3": "S3 Compatible",
}
