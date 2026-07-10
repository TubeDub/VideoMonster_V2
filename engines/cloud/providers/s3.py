"""S3-compatible storage (Backblaze B2, Cloudflare R2, MinIO)."""

from __future__ import annotations

import time
from pathlib import Path

from engines.cloud.models import CloudFileEntry, ProviderStatus
from engines.cloud.providers.base import CloudProviderAdapter, ProgressCallback, ProviderCapabilities
from engines.cloud.sync.checksum import file_sha256


class S3Provider(CloudProviderAdapter):
    provider_id = "s3"
    label = "S3 Compatible"

    def _client(self):
        try:
            import boto3
        except ImportError as e:
            raise RuntimeError("boto3 required for S3 provider: pip install boto3") from e
        kw = {
            "aws_access_key_id": self.settings.get("access_key") or self.settings.get("key_id"),
            "aws_secret_access_key": self.settings.get("secret_key") or self.settings.get("secret"),
            "region_name": self.settings.get("region") or "auto",
        }
        endpoint = (self.settings.get("endpoint") or self.settings.get("endpoint_url") or "").strip()
        if endpoint:
            return boto3.client("s3", endpoint_url=endpoint, **kw)
        return boto3.client("s3", **kw)

    def _bucket(self) -> str:
        b = (self.settings.get("bucket") or "").strip()
        if not b:
            raise ValueError("S3 bucket not configured")
        return b

    def connect(self) -> ProviderStatus:
        try:
            c = self._client()
            c.head_bucket(Bucket=self._bucket())
            return ProviderStatus(provider_id=self.provider_id, label=self.label, connected=True)
        except Exception as e:
            return ProviderStatus(
                provider_id=self.provider_id,
                label=self.label,
                connected=False,
                error=str(e)[:300],
            )

    def list_files(self, prefix: str = "") -> list[CloudFileEntry]:
        c = self._client()
        bucket = self._bucket()
        out: list[CloudFileEntry] = []
        token = None
        while True:
            kw = {"Bucket": bucket, "Prefix": prefix or ""}
            if token:
                kw["ContinuationToken"] = token
            resp = c.list_objects_v2(**kw)
            for row in resp.get("Contents") or []:
                out.append(
                    CloudFileEntry(
                        path=row["Key"],
                        size_bytes=int(row.get("Size") or 0),
                        modified_ms=int(row["LastModified"].timestamp() * 1000)
                        if row.get("LastModified")
                        else 0,
                    )
                )
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
        return out

    def upload_file(
        self,
        local_path: Path,
        remote_path: str,
        *,
        progress: ProgressCallback | None = None,
        resume_token: str | None = None,
    ) -> CloudFileEntry:
        src = Path(local_path)
        c = self._client()
        bucket = self._bucket()
        if progress:
            progress("upload", 0.0, {"path": remote_path})
        with open(src, "rb") as f:
            c.upload_fileobj(f, bucket, remote_path)
        if progress:
            progress("upload", 1.0, {"path": remote_path})
        digest = file_sha256(src)
        return CloudFileEntry(
            path=remote_path,
            size_bytes=src.stat().st_size,
            sha256=digest,
            modified_ms=int(time.time() * 1000),
        )

    def download_file(
        self,
        remote_path: str,
        local_path: Path,
        *,
        progress: ProgressCallback | None = None,
        resume_token: str | None = None,
    ) -> CloudFileEntry:
        c = self._client()
        dest = Path(local_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        c.download_file(self._bucket(), remote_path, str(dest))
        if progress:
            progress("download", 1.0, {"path": remote_path})
        st = dest.stat()
        return CloudFileEntry(
            path=remote_path,
            size_bytes=st.st_size,
            sha256=file_sha256(dest),
            modified_ms=int(st.st_mtime * 1000),
        )

    def delete_file(self, remote_path: str) -> None:
        self._client().delete_object(Bucket=self._bucket(), Key=remote_path)

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(multipart=True, resume=True, remote_jobs=True)
