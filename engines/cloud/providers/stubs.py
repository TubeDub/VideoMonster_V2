"""Filesystem-backed cloud providers — local mirrors + OAuth scaffolding.

OAuth cloud services (Drive/OneDrive/Dropbox) keep a local mirror under
``projects/cloud/<provider_id>/`` for offline CRUD. Remote OAuth is
env-gated: missing secrets never report ``oauth_connected``; use
``engines.cloud.oauth`` authorize/callback to obtain real tokens.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from engines.cloud.models import CloudFileEntry, ProviderStatus
from engines.cloud.providers.base import CloudProviderAdapter, ProgressCallback
from engines.cloud.sync.checksum import file_sha256


class MirrorFolderProvider(CloudProviderAdapter):
    """Local mirror that satisfies the full CloudProviderAdapter contract."""

    provider_id = "mirror"
    label = "Mirror"
    _folder_name: str = "mirror"
    _supports_oauth: bool = False

    def _root(self) -> Path:
        custom = (self.settings.get("root") or "").strip()
        if custom:
            return Path(custom)
        return self.app_dir / "projects" / "cloud" / self._folder_name

    def connect(self) -> ProviderStatus:
        root = self._root()
        root.mkdir(parents=True, exist_ok=True)
        used = sum(f.stat().st_size for f in root.rglob("*") if f.is_file())
        meta: dict = {"mode": "local_mirror", "root": str(root), "local_mirror_available": True}

        if self._supports_oauth:
            from engines.cloud.oauth import oauth_meta_for_provider

            oauth_meta = oauth_meta_for_provider(self.provider_id, app_dir=self.app_dir)
            meta.update(oauth_meta)
            if oauth_meta.get("oauth_connected"):
                meta["mode"] = "oauth+local_mirror"
            elif oauth_meta.get("oauth_configured"):
                meta["mode"] = "local_mirror_oauth_pending"
            else:
                meta["mode"] = "local_mirror_oauth_gated"
            # ``connected`` = local mirror ready (CRUD works). Remote status is in meta.
            # Never set oauth_connected without a real token (see oauth_meta).
            err = str(oauth_meta.get("message") or "")
            if oauth_meta.get("oauth_remote_gated"):
                missing = oauth_meta.get("oauth_missing") or []
                err = (
                    oauth_meta.get("message")
                    or ("OAuth hard-gated (local mirror only): " + ", ".join(missing))
                )
                meta["message_ru"] = oauth_meta.get("message_ru") or (
                    "OAuth закрыт (только локальное зеркало): нет " + ", ".join(missing)
                )
            elif oauth_meta.get("oauth_status") == "needs_auth":
                err = oauth_meta.get("message") or (
                    "OAuth credentials present — authorize via /api/cloud/oauth/.../authorize"
                )
                meta["message_ru"] = oauth_meta.get("message_ru") or (
                    "Ключи OAuth есть — авторизуйтесь через /api/cloud/oauth/.../authorize"
                )
            return ProviderStatus(
                provider_id=self.provider_id,
                label=self.label,
                connected=True,  # local mirror CRUD only — never means remote OAuth
                used_bytes=used,
                error=err,
                meta=meta,
            )

        creds = self.settings.get("credentials") or self.settings.get("token")
        if creds:
            meta["mode"] = "local_mirror+credentials"
        return ProviderStatus(
            provider_id=self.provider_id,
            label=self.label,
            connected=True,
            used_bytes=used,
            error="",
            meta=meta,
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
        if progress:
            progress("copy", 0.0, {"path": remote_path})
        shutil.copy2(src, dest)
        if progress:
            progress("copy", 1.0, {"path": remote_path})
        st = dest.stat()
        return CloudFileEntry(
            path=remote_path.replace("\\", "/"),
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
            raise FileNotFoundError(f"{self.label}: {remote_path}")
        dest = Path(local_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if progress:
            progress("copy", 0.0, {"path": remote_path})
        shutil.copy2(src, dest)
        if progress:
            progress("copy", 1.0, {"path": remote_path})
        st = dest.stat()
        return CloudFileEntry(
            path=remote_path.replace("\\", "/"),
            size_bytes=st.st_size,
            sha256=file_sha256(dest),
            modified_ms=int(st.st_mtime * 1000),
        )

    def delete_file(self, remote_path: str) -> None:
        root = self._root()
        target = root / remote_path.replace("\\", "/")
        if target.is_file():
            target.unlink()

    def rename_file(self, remote_path: str, new_name: str) -> CloudFileEntry:
        root = self._root()
        src = root / remote_path.replace("\\", "/")
        if not src.is_file():
            raise FileNotFoundError(remote_path)
        dest = src.parent / Path(new_name).name
        src.rename(dest)
        st = dest.stat()
        rel = str(dest.relative_to(root)).replace("\\", "/")
        return CloudFileEntry(
            path=rel,
            size_bytes=st.st_size,
            sha256=file_sha256(dest),
            modified_ms=int(st.st_mtime * 1000),
        )

    def move_file(self, remote_path: str, dest_prefix: str) -> CloudFileEntry:
        root = self._root()
        src = root / remote_path.replace("\\", "/")
        if not src.is_file():
            raise FileNotFoundError(remote_path)
        dest_dir = root / dest_prefix.strip("/").replace("\\", "/")
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        shutil.move(str(src), str(dest))
        st = dest.stat()
        rel = str(dest.relative_to(root)).replace("\\", "/")
        return CloudFileEntry(
            path=rel,
            size_bytes=st.st_size,
            sha256=file_sha256(dest),
            modified_ms=int(st.st_mtime * 1000),
        )

    def archive_prefix(self, prefix: str) -> str:
        import zipfile
        import time as _time

        root = self._root()
        src = root / prefix.replace("\\", "/") if prefix else root
        if not src.exists():
            raise FileNotFoundError(prefix or ".")
        archives = root / "_archives"
        archives.mkdir(parents=True, exist_ok=True)
        aid = f"arch_{int(_time.time())}_{uuid_hex()}"
        zpath = archives / f"{aid}.zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
            if src.is_file():
                zf.write(src, arcname=src.name)
            else:
                for f in src.rglob("*"):
                    if f.is_file():
                        zf.write(f, arcname=str(f.relative_to(root)).replace("\\", "/"))
        return aid

    def restore_archive(self, archive_id: str, dest_prefix: str) -> None:
        import zipfile

        root = self._root()
        zpath = root / "_archives" / f"{Path(archive_id).name}.zip"
        if not zpath.is_file():
            # allow bare id without .zip already
            alt = root / "_archives" / Path(archive_id).name
            zpath = alt if alt.is_file() else zpath
        if not zpath.is_file():
            raise FileNotFoundError(archive_id)
        dest = root / dest_prefix.replace("\\", "/").strip("/")
        dest.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zpath, "r") as zf:
            zf.extractall(dest)


def uuid_hex() -> str:
    import uuid

    return uuid.uuid4().hex[:8]


class GoogleDriveProvider(MirrorFolderProvider):
    provider_id = "google_drive"
    label = "Google Drive"
    _folder_name = "google_drive"
    _supports_oauth = True


class OneDriveProvider(MirrorFolderProvider):
    provider_id = "onedrive"
    label = "OneDrive"
    _folder_name = "onedrive"
    _supports_oauth = True


class DropboxProvider(MirrorFolderProvider):
    provider_id = "dropbox"
    label = "Dropbox"
    _folder_name = "dropbox"
    _supports_oauth = True


class TubeDubCloudProvider(MirrorFolderProvider):
    provider_id = "tubedub_cloud"
    label = "TubeDub Cloud"
    _folder_name = "tubedub_cloud"

    def connect(self) -> ProviderStatus:
        from engines.cloud.config import cloud_config

        cfg = cloud_config()
        url = (self.settings.get("url") or cfg.get("tubedub_cloud_url") or "").strip()
        token = self.settings.get("token") or self.settings.get("api_key")
        st = super().connect()
        mode = "local_mirror"
        err = ""
        msg_ru = ""
        if url and token:
            mode = "local_mirror+remote_configured"
            # Server HTTP client still Stage-2 — do not claim remote jobs ready
            err = (
                "TubeDub Cloud URL set, but remote job execution is not shipped yet "
                "(target=cloud returns 501). Local mirror + target=local work."
            )
            msg_ru = (
                "URL TubeDub Cloud задан, но удалённое выполнение ещё не подключено "
                "(target=cloud → 501). Локальное зеркало и target=local работают."
            )
        elif not url:
            err = (
                "TubeDub Cloud server URL missing (VM_TUBEDUB_CLOUD_URL). "
                "Local mirror only — remote cloud is not connected."
            )
            msg_ru = (
                "Нет URL сервера TubeDub Cloud (VM_TUBEDUB_CLOUD_URL). "
                "Только локальное зеркало — удалённое облако не подключено."
            )
        else:
            err = "TubeDub Cloud token/api_key missing — remote gated; local mirror only."
            msg_ru = "Нет token/api_key TubeDub Cloud — удалённый доступ закрыт; только локальное зеркало."
        st.error = err
        st.meta = {
            **(st.meta or {}),
            "mode": mode,
            "remote_url": url or None,
            "remote_configured": bool(url and token),
            "remote_jobs_ready": False,
            "message": err,
            "message_ru": msg_ru,
        }
        return st
