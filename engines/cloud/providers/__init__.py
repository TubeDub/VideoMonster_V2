"""Cloud storage provider adapters."""

from engines.cloud.providers.base import CloudProviderAdapter, ProviderCapabilities
from engines.cloud.providers.local import LocalProvider
from engines.cloud.providers.s3 import S3Provider
from engines.cloud.providers.stubs import DropboxProvider, GoogleDriveProvider, OneDriveProvider, TubeDubCloudProvider

PROVIDER_REGISTRY: dict[str, type[CloudProviderAdapter]] = {
    "local": LocalProvider,
    "google_drive": GoogleDriveProvider,
    "onedrive": OneDriveProvider,
    "dropbox": DropboxProvider,
    "tubedub_cloud": TubeDubCloudProvider,
    "s3": S3Provider,
}

__all__ = [
    "CloudProviderAdapter",
    "PROVIDER_REGISTRY",
    "ProviderCapabilities",
]
