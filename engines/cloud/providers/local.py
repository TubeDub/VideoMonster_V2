"""Local disk provider — mirrors output/ and projects/cloud/."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from engines.cloud.models import CloudFileEntry, ProviderStatus
from engines.cloud.providers.base import CloudProviderAdapter, ProgressCallback, ProviderCapabilities
from engines.cloud.sync.checksum import file_sha256


class LocalProvider(CloudProviderAdapter):
    provider_id = "local"
    label = "Local Disk"

    def _root(self) -> Path:
        custom = (self.settings.get("root") or "").strip()
        if custom:
            return Path(custom)
        return self.app_dir / "projects" / "cloud"

    def connect(self) -> ProviderStatus:
        root = self._root()
        root.mkdir(parents=True, exist_ok=True)
        used = sum(f.stat().st_size for f in root.rglob("*") if f.is_file())
        try:
            import shutil as sh

            total, used_disk, free = sh.disk_usage(str(self.app_dir))
        except Exception:
            total = used_disk = free = None
        return ProviderStatus(
            provider_id=self.provider_id,
            label=self.label,
            connected=True,
            total_bytes=total,
            used_bytes=used,
            free_bytes=free,
        )

    def list_files(self, prefix: str = "") -> list[CloudFileEntry]:
        root = self._root()
        base = root / prefix if prefix else root
        if not base.exists():
            return []
        out: list[CloudFileEntry] = []
        for f in base.rglob("*"):
            if not f.is_file():
                continue
            rel = str(f.relative_to(root)).replace("\\", "/")
            st = f.stat()
            out.append(
                CloudFileEntry(
                    path=rel,
                    size_bytes=st.st_size,
                    sha256=file_sha256(f),
                    modified_ms=int(st.st_mtime * 1000),
                )
            )
        return sorted(out, key=lambda x: x.path)

    def upload_file(
        self,
        local_path: Path,
        remote_path: str,
        *,
        progress: ProgressCallback | None = None,
        resume_token: str | None = None,
    ) -> CloudFileEntry:
        root = self._root()
        dest = root / remote_path.replace("\\", "/")
        dest.parent.mkdir(parents=True, exist_ok=True)
        src = Path(local_path)
        size = src.stat().st_size
        if progress:
            progress("copy", 0.0, {"path": remote_path})
        shutil.copy2(src, dest)
        if progress:
            progress("copy", 1.0, {"path": remote_path, "bytes": size})
        st = dest.stat()
        return CloudFileEntry(
            path=remote_path,
            size_bytes=st.st_size,
            sha256=file_sha256(dest),
            modified_ms=int(st.st_mtime * 1000),
        )

    def download_file(
        self,
        remote_path: str,
        local_path: Path,
        *,
        progress: ProgressCallback | None = None,
        resume_token: str | None = None,
    ) -> CloudFileEntry:
        root = self._root()
        src = root / remote_path.replace("\\", "/")
        if not src.is_file():
            raise FileNotFoundError(remote_path)
        dest = Path(local_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if progress:
            progress("copy", 0.0, {"path": remote_path})
        shutil.copy2(src, dest)
        if progress:
            progress("copy", 1.0, {"path": remote_path})
        st = dest.stat()
        return CloudFileEntry(
            path=remote_path,
            size_bytes=st.st_size,
            sha256=file_sha256(dest),
            modified_ms=int(st.st_mtime * 1000),
        )

    def delete_file(self, remote_path: str) -> None:
        root = self._root()
        target = root / remote_path.replace("\\", "/")
        if target.is_file():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)

    def rename_file(self, remote_path: str, new_name: str) -> CloudFileEntry:
        root = self._root()
        src = root / remote_path.replace("\\", "/")
        dest = src.parent / new_name
        src.rename(dest)
        st = dest.stat()
        rel = str(dest.relative_to(root)).replace("\\", "/")
        return CloudFileEntry(path=rel, size_bytes=st.st_size, modified_ms=int(st.st_mtime * 1000))

    def move_file(self, remote_path: str, dest_prefix: str) -> CloudFileEntry:
        root = self._root()
        src = root / remote_path.replace("\\", "/")
        dest_dir = root / dest_prefix.strip("/")
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        shutil.move(str(src), str(dest))
        rel = str(dest.relative_to(root)).replace("\\", "/")
        st = dest.stat()
        return CloudFileEntry(path=rel, size_bytes=st.st_size, modified_ms=int(st.st_mtime * 1000))

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(multipart=True, resume=True, direct_stream_write=True)
