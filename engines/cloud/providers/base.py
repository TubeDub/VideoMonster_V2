"""Base adapter for cloud storage providers."""

from __future__ import annotations

import abc
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from engines.cloud.models import CloudFileEntry, ProviderStatus


@dataclass
class ProviderCapabilities:
    multipart: bool = True
    resume: bool = True
    direct_stream_write: bool = False
    remote_jobs: bool = False


ProgressCallback = Callable[[str, float, dict[str, Any]], None]


class CloudProviderAdapter(abc.ABC):
    provider_id: str = "base"
    label: str = "Base"

    def __init__(self, app_dir: Path, settings: dict[str, Any] | None = None):
        self.app_dir = Path(app_dir)
        self.settings = settings or {}

    @abc.abstractmethod
    def connect(self) -> ProviderStatus:
        ...

    @abc.abstractmethod
    def list_files(self, prefix: str = "") -> list[CloudFileEntry]:
        ...

    @abc.abstractmethod
    def upload_file(
        self,
        local_path: Path,
        remote_path: str,
        *,
        progress: ProgressCallback | None = None,
        resume_token: str | None = None,
    ) -> CloudFileEntry:
        ...

    @abc.abstractmethod
    def download_file(
        self,
        remote_path: str,
        local_path: Path,
        *,
        progress: ProgressCallback | None = None,
        resume_token: str | None = None,
    ) -> CloudFileEntry:
        ...

    @abc.abstractmethod
    def delete_file(self, remote_path: str) -> None:
        ...

    def rename_file(self, remote_path: str, new_name: str) -> CloudFileEntry:
        raise NotImplementedError(f"{self.provider_id} rename not implemented")

    def move_file(self, remote_path: str, dest_prefix: str) -> CloudFileEntry:
        raise NotImplementedError(f"{self.provider_id} move not implemented")

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities()

    def archive_prefix(self, prefix: str) -> str:
        raise NotImplementedError(f"{self.provider_id} archive not implemented")

    def restore_archive(self, archive_id: str, dest_prefix: str) -> None:
        raise NotImplementedError(f"{self.provider_id} restore not implemented")
