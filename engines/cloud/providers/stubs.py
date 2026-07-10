"""Stub providers for OAuth cloud services (Stage 1)."""

from __future__ import annotations

from pathlib import Path

from engines.cloud.models import CloudFileEntry, ProviderStatus
from engines.cloud.providers.base import CloudProviderAdapter, ProgressCallback


class _OAuthStubProvider(CloudProviderAdapter):
    _needs: str = "credentials"

    def connect(self) -> ProviderStatus:
        creds = self.settings.get("credentials") or self.settings.get("token")
        connected = bool(creds)
        return ProviderStatus(
            provider_id=self.provider_id,
            label=self.label,
            connected=connected,
            error="" if connected else f"Configure {self.provider_id} credentials in Cloud settings",
        )

    def list_files(self, prefix: str = "") -> list[CloudFileEntry]:
        self._require_connected()
        return []

    def upload_file(
        self,
        local_path: Path,
        remote_path: str,
        *,
        progress: ProgressCallback | None = None,
        resume_token: str | None = None,
    ) -> CloudFileEntry:
        self._require_connected()
        raise NotImplementedError(f"{self.label} upload adapter pending (Stage 2)")

    def download_file(
        self,
        remote_path: str,
        local_path: Path,
        *,
        progress: ProgressCallback | None = None,
        resume_token: str | None = None,
    ) -> CloudFileEntry:
        self._require_connected()
        raise NotImplementedError(f"{self.label} download adapter pending (Stage 2)")

    def delete_file(self, remote_path: str) -> None:
        self._require_connected()
        raise NotImplementedError(f"{self.label} delete pending (Stage 2)")

    def _require_connected(self) -> None:
        st = self.connect()
        if not st.connected:
            raise PermissionError(st.error or f"{self.label} not connected")


class GoogleDriveProvider(_OAuthStubProvider):
    provider_id = "google_drive"
    label = "Google Drive"


class OneDriveProvider(_OAuthStubProvider):
    provider_id = "onedrive"
    label = "OneDrive"


class DropboxProvider(_OAuthStubProvider):
    provider_id = "dropbox"
    label = "Dropbox"


class TubeDubCloudProvider(_OAuthStubProvider):
    provider_id = "tubedub_cloud"
    label = "TubeDub Cloud"

    def connect(self) -> ProviderStatus:
        from engines.cloud.config import cloud_config

        cfg = cloud_config()
        url = (self.settings.get("url") or cfg.get("tubedub_cloud_url") or "").strip()
        token = self.settings.get("token") or self.settings.get("api_key")
        connected = bool(url and token)
        return ProviderStatus(
            provider_id=self.provider_id,
            label=self.label,
            connected=connected,
            error="" if connected else "Set VM_TUBEDUB_CLOUD_URL and API token",
        )
