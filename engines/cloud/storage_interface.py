"""Abstract cloud storage — Local + future Drive/S3 (TZ §14)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class StorageInterface(ABC):
    provider_id: str = "abstract"

    @abstractmethod
    def connect(self) -> dict[str, Any]:
        """Return connection status."""

    @abstractmethod
    def list_files(self, prefix: str = "") -> list[dict[str, Any]]:
        """List remote files."""

    @abstractmethod
    def upload(self, local_path: Path, remote_path: str) -> dict[str, Any]:
        """Upload file."""

    @abstractmethod
    def download(self, remote_path: str, local_path: Path) -> dict[str, Any]:
        """Download file."""

    @abstractmethod
    def delete(self, remote_path: str) -> bool:
        """Delete remote file."""


class LocalStorage(StorageInterface):
    """Local disk mirror — delegates to engines.cloud.providers.local."""

    provider_id = "local"

    def __init__(self, app_dir: Path) -> None:
        self.app_dir = Path(app_dir)

    def connect(self) -> dict[str, Any]:
        from engines.cloud.providers.local import LocalProvider

        prov = LocalProvider(self.app_dir, {})
        st = prov.connect()
        return {
            "provider_id": self.provider_id,
            "connected": st.connected,
            "label": st.label,
            "used_bytes": st.used_bytes,
            "free_bytes": st.free_bytes,
        }

    def list_files(self, prefix: str = "") -> list[dict[str, Any]]:
        from engines.cloud.providers.local import LocalProvider

        prov = LocalProvider(self.app_dir, {})
        prov.connect()
        return [e.to_dict() if hasattr(e, "to_dict") else dict(e) for e in prov.list_files(prefix)]

    def upload(self, local_path: Path, remote_path: str) -> dict[str, Any]:
        from engines.cloud.providers.local import LocalProvider

        prov = LocalProvider(self.app_dir, {})
        prov.connect()
        entry = prov.upload_file(local_path, remote_path)
        return entry.to_dict() if hasattr(entry, "to_dict") else {"path": remote_path}

    def download(self, remote_path: str, local_path: Path) -> dict[str, Any]:
        from engines.cloud.providers.local import LocalProvider

        prov = LocalProvider(self.app_dir, {})
        prov.connect()
        prov.download_file(remote_path, local_path)
        return {"ok": True, "path": str(local_path)}

    def delete(self, remote_path: str) -> bool:
        from engines.cloud.providers.local import LocalProvider

        prov = LocalProvider(self.app_dir, {})
        prov.connect()
        try:
            prov.delete_file(remote_path)
            return True
        except Exception:
            return False


class DriveStorageStub(StorageInterface):
    provider_id = "google_drive"

    def connect(self) -> dict[str, Any]:
        return {"provider_id": self.provider_id, "connected": False, "status": "NOT_IMPLEMENTED"}

    def list_files(self, prefix: str = "") -> list[dict[str, Any]]:
        return []

    def upload(self, local_path: Path, remote_path: str) -> dict[str, Any]:
        return {"ok": False, "error": "Google Drive not implemented"}

    def download(self, remote_path: str, local_path: Path) -> dict[str, Any]:
        return {"ok": False, "error": "Google Drive not implemented"}

    def delete(self, remote_path: str) -> bool:
        return False


class S3StorageStub(StorageInterface):
    provider_id = "s3"

    def connect(self) -> dict[str, Any]:
        return {"provider_id": self.provider_id, "connected": False, "status": "NOT_IMPLEMENTED"}

    def list_files(self, prefix: str = "") -> list[dict[str, Any]]:
        return []

    def upload(self, local_path: Path, remote_path: str) -> dict[str, Any]:
        return {"ok": False, "error": "S3 not implemented"}

    def download(self, remote_path: str, local_path: Path) -> dict[str, Any]:
        return {"ok": False, "error": "S3 not implemented"}

    def delete(self, remote_path: str) -> bool:
        return False
