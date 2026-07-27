"""Plugin Manager — discovery, loading, sandbox, hot-reload (TZ #9 §1, §6–§8).

Manages the open plugin ecosystem without modifying core pipeline modules.
Plugins live in ``plugins/`` with a ``plugin.json`` manifest (§4).
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from core.plugin_api import (
    CORE_API_VERSION,
    Capability,
    ExecutionMode,
    PluginHealth,
    PluginManifest,
    PluginPermissions,
    PluginState,
    VMPlugin,
    parse_version,
    version_compatible,
)

logger = logging.getLogger("tubedub.plugin_manager")


def plugins_enabled() -> bool:
    return str(os.getenv("VM_PLUGINS", "1")).strip().lower() not in (
        "0", "false", "no", "off",
    )


@dataclass
class PluginRecord:
    """Runtime plugin descriptor."""
    manifest: PluginManifest
    path: Path
    state: PluginState = PluginState.DISCOVERED
    instance: VMPlugin | None = None
    error: str = ""
    loaded_at: float = 0.0
    stats: dict[str, Any] = field(default_factory=lambda: {
        "calls": 0, "errors": 0, "total_ms": 0.0,
    })

    def to_dict(self) -> dict[str, Any]:
        health: dict[str, Any] = {"ok": self.state == PluginState.ENABLED}
        if self.instance and self.state == PluginState.ENABLED:
            try:
                health = self.instance.health().to_dict()
            except Exception as exc:
                health = {"ok": False, "message": str(exc)}
        return {
            "name": self.manifest.name,
            "version": self.manifest.version,
            "state": self.state.value,
            "capabilities": self.manifest.capabilities,
            "permissions": self.manifest.permissions.to_dict(),
            "execution_mode": self.manifest.execution_mode,
            "remote_endpoint": self.manifest.remote_endpoint,
            "error": self.error,
            "health": health,
            "stats": dict(self.stats),
            "manifest": self.manifest.to_dict(),
        }


class DependencyResolver:
    """Resolve plugin dependencies and detect conflicts (§6)."""

    def resolve(
        self,
        manifest: PluginManifest,
        available: dict[str, PluginRecord],
    ) -> tuple[bool, list[str]]:
        issues: list[str] = []
        if not version_compatible(manifest.minimum_api):
            issues.append(
                f"API {manifest.minimum_api} required, core is {CORE_API_VERSION}"
            )
        for dep in manifest.dependencies:
            rec = available.get(dep)
            if rec is None or rec.state not in (
                PluginState.LOADED, PluginState.ENABLED,
            ):
                issues.append(f"Missing dependency: {dep}")
        for pkg in manifest.python_packages:
            if importlib.util.find_spec(pkg.split("[")[0]) is None:
                issues.append(f"Missing Python package: {pkg}")
        return len(issues) == 0, issues


class VersionManager:
    """API compatibility and deprecation checks (§12, §16)."""

    DEPRECATED_APIS: dict[str, str] = {
        "0.9.0": "Use minimum_api 1.0.0+",
    }

    def check(self, manifest: PluginManifest) -> tuple[bool, str]:
        if manifest.deprecated:
            return False, "Plugin marked deprecated in manifest"
        if not version_compatible(manifest.minimum_api):
            return False, f"Requires API {manifest.minimum_api}, have {CORE_API_VERSION}"
        return True, ""


class PluginMarketplaceAPI:
    """Local marketplace + optional remote storefront (§9).

    Local dir/zip install is the default. Remote catalog/install activates only
    when ``VM_PLUGIN_MARKETPLACE_URL`` (or ``VM_PLUGIN_CATALOG_URL``) is set;
    otherwise remote actions hard-gate with ``remote_marketplace_not_configured``.
    """

    REMOTE_MAX_BYTES = 50 * 1024 * 1024  # 50 MiB zip cap
    REMOTE_TIMEOUT_S = 30

    def __init__(self, manager: PluginManager) -> None:
        self._mgr = manager

    @staticmethod
    def remote_catalog_url() -> str:
        return (
            str(os.getenv("VM_PLUGIN_MARKETPLACE_URL") or "").strip()
            or str(os.getenv("VM_PLUGIN_CATALOG_URL") or "").strip()
        )

    def remote_configured(self) -> bool:
        return bool(self.remote_catalog_url())

    def catalog(self) -> dict[str, Any]:
        """List installed plugins plus local filesystem packages (+ remote status)."""
        installed = self._mgr.list_plugins()
        packages: list[dict[str, Any]] = []
        root = self._mgr.plugins_dir
        if root.is_dir():
            for child in sorted(root.iterdir()):
                if not child.is_dir():
                    continue
                mf = child / self._mgr.MANIFEST_FILE
                if not mf.is_file():
                    continue
                try:
                    data = json.loads(mf.read_text(encoding="utf-8"))
                except Exception:
                    data = {}
                packages.append(
                    {
                        "name": data.get("name") or child.name,
                        "version": data.get("version"),
                        "capabilities": data.get("capabilities") or [],
                        "path": str(child),
                        "installed": (data.get("name") or child.name)
                        in {p.get("name") for p in installed},
                    }
                )
        remote = self.remote_status(installed=installed)
        return {
            "ok": True,
            "store": "local",
            "installed_count": len(installed),
            "packages": packages,
            "plugins": installed,
            "catalog": self._external_catalog(installed),
            "remote": remote,
        }

    def remote_status(self, *, installed: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Honest remote storefront status — never pretends remote is live."""
        url = self.remote_catalog_url()
        if not url:
            return {
                "configured": False,
                "available": False,
                "url": None,
                "plugins": [],
                "reason": "remote_marketplace_not_configured",
                "hint": "Set VM_PLUGIN_MARKETPLACE_URL to enable remote storefront",
            }
        fetched = self.fetch_remote_catalog()
        if not fetched.get("ok"):
            return {
                "configured": True,
                "available": False,
                "url": url,
                "plugins": [],
                "error": fetched.get("error") or "remote_fetch_failed",
            }
        rows = list(fetched.get("plugins") or [])
        names = {p.get("name") for p in (installed if installed is not None else self._mgr.list_plugins())}
        for row in rows:
            name = row.get("name") or row.get("id")
            row["installed"] = name in names
        return {
            "configured": True,
            "available": True,
            "url": url,
            "plugins": rows,
            "version": fetched.get("version"),
        }

    def fetch_remote_catalog(self) -> dict[str, Any]:
        """Fetch remote catalog JSON. Hard-gates when URL env is unset."""
        url = self.remote_catalog_url()
        if not url:
            return {
                "ok": False,
                "error": "remote_marketplace_not_configured",
                "hint": "Set VM_PLUGIN_MARKETPLACE_URL",
            }
        try:
            raw = self._http_get_bytes(url)
            data = json.loads(raw.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"remote_catalog_fetch_failed: {exc}", "url": url}
        if not isinstance(data, dict):
            return {"ok": False, "error": "remote_catalog_invalid_json", "url": url}
        plugins = data.get("plugins")
        if plugins is None:
            plugins = data.get("packages") or []
        if not isinstance(plugins, list):
            return {"ok": False, "error": "remote_catalog_missing_plugins", "url": url}
        return {
            "ok": True,
            "url": url,
            "version": data.get("version"),
            "plugins": [dict(p) for p in plugins if isinstance(p, dict)],
        }

    def _external_catalog(self, installed: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Optional curated catalog from data/plugin_marketplace_catalog.json."""
        catalog_path = self._mgr.app_dir / "data" / "plugin_marketplace_catalog.json"
        if not catalog_path.is_file():
            return []
        try:
            raw = json.loads(catalog_path.read_text(encoding="utf-8"))
            rows = list(raw.get("plugins") or [])
        except Exception:
            return []
        installed_names = {p.get("name") for p in installed}
        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            name = item.get("name") or item.get("id")
            item["installed"] = name in installed_names
            out.append(item)
        return out

    def _validate_plugin_dir(self, plugin_dir: Path) -> tuple[bool, str, dict[str, Any]]:
        mf = plugin_dir / self._mgr.MANIFEST_FILE
        if not mf.is_file():
            return False, "missing_plugin_json", {}
        try:
            data = json.loads(mf.read_text(encoding="utf-8"))
        except Exception as exc:
            return False, f"invalid_manifest: {exc}", {}
        name = str(data.get("name") or "").strip()
        if not name:
            return False, "manifest_missing_name", data
        entry = str(data.get("entry_point") or "plugin.py")
        if not (plugin_dir / entry).is_file():
            return False, f"missing_entry_point: {entry}", data
        ok, reason = self._mgr.versions.check(PluginManifest.from_json(mf))
        if not ok:
            return False, reason, data
        return True, "", data

    def _extract_zip(self, zip_path: Path, dest_parent: Path) -> Path | None:
        import shutil
        import tempfile
        import zipfile

        from engines.path_safety import safe_extractall, safe_filename

        if not zipfile.is_zipfile(zip_path):
            return None
        with tempfile.TemporaryDirectory(prefix="vm_plugin_") as tmp:
            tmp_path = Path(tmp)
            with zipfile.ZipFile(zip_path, "r") as zf:
                safe_extractall(zf, tmp_path)
            # Prefer nested folder with plugin.json, else root
            candidates = [p for p in tmp_path.rglob(self._mgr.MANIFEST_FILE)]
            if not candidates:
                return None
            src = candidates[0].parent
            try:
                data = json.loads(candidates[0].read_text(encoding="utf-8"))
                dest_name = safe_filename(str(data.get("name") or src.name), default=src.name)
            except Exception:
                dest_name = safe_filename(src.name, default="plugin")
            dest_parent.mkdir(parents=True, exist_ok=True)
            dest = dest_parent / dest_name
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(src, dest)
            return dest

    def _http_get_bytes(self, url: str) -> bytes:
        """Download URL bytes with scheme/size guards (path-safety for remote)."""
        from urllib.error import URLError
        from urllib.parse import urlparse
        from urllib.request import Request, urlopen

        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"unsupported_url_scheme:{parsed.scheme or 'none'}")
        if not parsed.netloc:
            raise ValueError("url_missing_host")
        req = Request(url, headers={"User-Agent": "VideoMonster-PluginMarketplace/1.0"})
        with urlopen(req, timeout=self.REMOTE_TIMEOUT_S) as resp:  # noqa: S310 — scheme gated
            chunks: list[bytes] = []
            total = 0
            while True:
                block = resp.read(64 * 1024)
                if not block:
                    break
                total += len(block)
                if total > self.REMOTE_MAX_BYTES:
                    raise ValueError("remote_download_too_large")
                chunks.append(block)
        return b"".join(chunks)

    def _url_allowed_for_remote(self, download_url: str) -> bool:
        """Allow download URLs that share catalog host, or appear in remote catalog."""
        from urllib.parse import urlparse

        catalog = self.remote_catalog_url()
        if not catalog:
            return False
        cat_host = (urlparse(catalog).hostname or "").lower()
        dl_host = (urlparse(download_url).hostname or "").lower()
        if cat_host and dl_host and cat_host == dl_host:
            return True
        fetched = self.fetch_remote_catalog()
        if not fetched.get("ok"):
            return False
        for row in fetched.get("plugins") or []:
            for key in ("download_url", "url", "source", "zip_url"):
                u = str(row.get(key) or "").strip()
                if u and u == download_url:
                    return True
        return False

    def install_from_url(self, url: str, *, name: str = "") -> dict[str, Any]:
        """Download a remote .zip and install with zip-slip-safe extract."""
        import tempfile

        url = str(url or "").strip()
        if not url:
            return {"ok": False, "error": "url_required"}
        if not self.remote_configured():
            return {
                "ok": False,
                "error": "remote_marketplace_not_configured",
                "hint": "Set VM_PLUGIN_MARKETPLACE_URL before remote install",
            }
        if not self._url_allowed_for_remote(url):
            return {
                "ok": False,
                "error": "remote_url_not_allowed",
                "hint": "Download URL must share catalog host or appear in remote catalog",
                "url": url,
            }
        try:
            payload = self._http_get_bytes(url)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"remote_download_failed: {exc}", "url": url}

        staging_root = self._mgr.plugins_dir.parent / "_plugin_staging"
        staging_root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix="vm_remote_",
            suffix=".zip",
            delete=False,
            dir=str(staging_root),
        ) as tmp:
            tmp.write(payload)
            zpath = Path(tmp.name)
        try:
            if not zpath.is_file() or zpath.stat().st_size == 0:
                return {"ok": False, "error": "remote_download_empty", "url": url}
            result = self.install(str(zpath), name=name)
            if result.get("ok"):
                result["source_url"] = url
                result["store"] = "remote"
            return result
        finally:
            try:
                zpath.unlink(missing_ok=True)
            except Exception:
                pass

    def install_remote(self, plugin_id: str, *, name: str = "") -> dict[str, Any]:
        """Install by id/name from the configured remote catalog."""
        plugin_id = str(plugin_id or "").strip()
        if not plugin_id:
            return {"ok": False, "error": "plugin_id_required"}
        if not self.remote_configured():
            return {
                "ok": False,
                "error": "remote_marketplace_not_configured",
                "hint": "Set VM_PLUGIN_MARKETPLACE_URL before remote install",
            }
        fetched = self.fetch_remote_catalog()
        if not fetched.get("ok"):
            return fetched
        entry = None
        for row in fetched.get("plugins") or []:
            if str(row.get("id") or "") == plugin_id or str(row.get("name") or "") == plugin_id:
                entry = row
                break
        if not entry:
            return {"ok": False, "error": "remote_plugin_not_found", "plugin": plugin_id}
        download = (
            str(entry.get("download_url") or entry.get("url") or entry.get("zip_url") or entry.get("source") or "")
            .strip()
        )
        if not download.startswith(("http://", "https://")):
            return {
                "ok": False,
                "error": "remote_plugin_missing_download_url",
                "plugin": plugin_id,
            }
        dest_name = name or str(entry.get("name") or plugin_id)
        return self.install_from_url(download, name=dest_name)

    def install(self, source: str, *, name: str = "") -> dict[str, Any]:
        """Install plugin from local directory, .zip, or remote http(s) URL."""
        import shutil

        from engines.path_safety import is_under_root, resolve_under_roots

        source_s = str(source or "").strip()
        if source_s.startswith(("http://", "https://")):
            return self.install_from_url(source_s, name=name)

        app_dir = Path(self._mgr.app_dir).resolve()
        allowed_roots = [
            app_dir / "plugins",
            app_dir / "uploads",
            app_dir / "uploads" / "plugins",
            app_dir / "output",
            app_dir / "data" / "marketplace",
            app_dir / "sdk",
        ]
        resolved = resolve_under_roots(source_s, allowed_roots, basename_fallback=True)
        if resolved is None:
            # Allow absolute paths only under app_dir (dev drop folders).
            try:
                cand = Path(source_s).resolve()
            except OSError:
                cand = None
            if cand is not None and is_under_root(cand, app_dir) and cand.exists():
                resolved = cand
        if resolved is None:
            return {
                "ok": False,
                "error": "source_outside_allowlist",
                "source": source_s,
                "hint": "Place plugin zip/dir under uploads/, plugins/, output/, or data/marketplace/",
            }
        src = resolved
        if not src.exists():
            return {"ok": False, "error": "source_not_found", "source": str(src)}

        staging: Path | None = None
        if src.is_file() and src.suffix.lower() == ".zip":
            staging = self._extract_zip(src, self._mgr.plugins_dir.parent / "_plugin_staging")
            if staging is None:
                return {"ok": False, "error": "invalid_zip_or_missing_manifest"}
            src = staging

        if not src.is_dir():
            return {"ok": False, "error": "source_must_be_dir_or_zip"}

        ok, reason, data = self._validate_plugin_dir(src)
        if not ok:
            if staging and staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            return {"ok": False, "error": reason}

        dest_name = name or str(data.get("name") or src.name)
        dest = self._mgr.plugins_dir / dest_name
        if dest.exists() and dest.resolve() != src.resolve():
            if staging and staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            return {"ok": False, "error": "already_installed", "plugin": dest_name}

        self._mgr.plugins_dir.mkdir(parents=True, exist_ok=True)
        if dest.resolve() != src.resolve():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(src, dest)
        if staging and staging.exists() and staging.resolve() != dest.resolve():
            shutil.rmtree(staging, ignore_errors=True)

        rec = self._mgr.discover_one(dest)
        loaded = False
        if rec and plugins_enabled():
            loaded = self._mgr.load(dest_name)
        return {
            "ok": True,
            "plugin": dest_name,
            "state": rec.state.value if rec else "unknown",
            "loaded": loaded,
            "manifest": data,
        }

    def update(self, name: str, source: str) -> dict[str, Any]:
        import shutil

        from engines.path_safety import is_under_root, resolve_under_roots

        name = str(name or "").strip()
        if not name:
            return {"ok": False, "error": "name_required"}
        source_s = str(source or "").strip()
        if not source_s:
            return {"ok": False, "error": "source_required"}

        app_dir = Path(self._mgr.app_dir).resolve()
        allowed_roots = [
            app_dir / "plugins",
            app_dir / "uploads",
            app_dir / "uploads" / "plugins",
            app_dir / "output",
            app_dir / "data" / "marketplace",
            app_dir / "sdk",
        ]
        if source_s.startswith(("http://", "https://")):
            return self.install_from_url(source_s, name=name)

        resolved = resolve_under_roots(source_s, allowed_roots, basename_fallback=True)
        if resolved is None:
            try:
                cand = Path(source_s).resolve()
            except OSError:
                cand = None
            if cand is not None and is_under_root(cand, app_dir) and cand.exists():
                resolved = cand
        if resolved is None:
            return {"ok": False, "error": "source_outside_allowlist", "source": source_s}
        src = resolved
        dest = self._mgr.plugins_dir / name
        if not dest.is_dir():
            return {"ok": False, "error": "not_installed", "plugin": name}

        self._mgr.disable(name)
        backup = dest.with_name(f".{name}.bak")
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        shutil.copytree(dest, backup)

        try:
            if src.is_file() and src.suffix.lower() == ".zip":
                extracted = self._extract_zip(src, dest.parent)
                if extracted is None:
                    raise RuntimeError("invalid_zip_or_missing_manifest")
                if extracted.resolve() != dest.resolve():
                    shutil.rmtree(dest, ignore_errors=True)
                    extracted.rename(dest)
            elif src.is_dir():
                ok, reason, _data = self._validate_plugin_dir(src)
                if not ok:
                    raise RuntimeError(reason)
                for item in list(dest.iterdir()):
                    if item.is_file():
                        item.unlink()
                    elif item.is_dir():
                        shutil.rmtree(item)
                for item in src.iterdir():
                    target = dest / item.name
                    if item.is_dir():
                        shutil.copytree(item, target)
                    else:
                        shutil.copy2(item, target)
            else:
                raise RuntimeError("source_not_found")
        except Exception as exc:
            if backup.exists():
                shutil.rmtree(dest, ignore_errors=True)
                backup.rename(dest)
            return {"ok": False, "error": str(exc), "plugin": name, "restored_backup": True}

        shutil.rmtree(backup, ignore_errors=True)
        self._mgr.discover_one(dest)
        ok = self._mgr.reload(name)
        return {"ok": ok, "plugin": name, "reloaded": ok}

    def remove(self, name: str) -> dict[str, Any]:
        import shutil

        name = str(name or "").strip()
        if not name:
            return {"ok": False, "error": "name_required"}
        self._mgr.disable(name)
        dest = self._mgr.plugins_dir / name
        if dest.is_dir():
            shutil.rmtree(dest)
        self._mgr._records.pop(name, None)
        return {"ok": True, "removed": name}

    def enable(self, name: str) -> dict[str, Any]:
        ok = self._mgr.enable(name)
        return {"ok": ok, "plugin": name}

    def disable(self, name: str) -> dict[str, Any]:
        ok = self._mgr.disable(name)
        return {"ok": ok, "plugin": name}


class PluginManager:
    """Central plugin lifecycle manager (§1)."""

    MANIFEST_FILE = "plugin.json"

    def __init__(self, *, app_dir: str | Path | None = None) -> None:
        self.app_dir = Path(app_dir) if app_dir else Path.cwd()
        self.plugins_dir = self._resolve_plugins_dir()
        self._lock = threading.RLock()
        self._records: dict[str, PluginRecord] = {}
        self._capability_index: dict[str, list[str]] = {}
        self._registries: dict[str, dict[str, Any]] = {
            "agents": {},
            "models": {},
            "exporters": {},
            "tts": {},
            "stt": {},
            "translation": {},
            "review": {},
            "events": {},
            "memory_providers": {},
        }
        self._permission_overrides = self._load_permission_overrides()
        self.resolver = DependencyResolver()
        self.versions = VersionManager()
        self.marketplace = PluginMarketplaceAPI(self)
        self._context: dict[str, Any] = {"app_dir": str(self.app_dir)}

    def _resolve_plugins_dir(self) -> Path:
        env = os.getenv("VM_PLUGINS_DIR")
        if env:
            return Path(env)
        return self.app_dir / "plugins"

    def _permissions_path(self) -> Path:
        return self.app_dir / "data" / "plugins" / "permissions.json"

    def _load_permission_overrides(self) -> dict[str, dict[str, bool]]:
        path = self._permissions_path()
        if not path.is_file():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def save_permission_overrides(self) -> None:
        path = self._permissions_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self._permission_overrides, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def set_permissions(self, plugin_name: str, permissions: dict[str, bool]) -> None:
        with self._lock:
            self._permission_overrides[plugin_name] = permissions
        self.save_permission_overrides()

    def permissions_allowed(self, manifest: PluginManifest) -> tuple[bool, str]:
        override = self._permission_overrides.get(manifest.name)
        if not override:
            return True, ""
        declared = manifest.permissions.to_dict()
        for perm, required in declared.items():
            if required and not override.get(perm, True):
                return False, f"Permission denied: {perm}"
        return True, ""

    # ── Discovery (§1) ───────────────────────────────────────────────

    def discover(self, *, auto_load: bool = True) -> list[str]:
        """Scan plugins/ and register all manifests."""
        found: list[str] = []
        with self._lock:
            if not self.plugins_dir.is_dir():
                self.plugins_dir.mkdir(parents=True, exist_ok=True)
            for child in sorted(self.plugins_dir.iterdir()):
                if not child.is_dir():
                    continue
                rec = self.discover_one(child)
                if rec:
                    found.append(rec.manifest.name)
            extra = os.getenv("VM_PLUGIN_PATH", "")
            for part in extra.split(os.pathsep):
                p = Path(part.strip())
                if p.is_dir():
                    rec = self.discover_one(p)
                    if rec:
                        found.append(rec.manifest.name)
        if auto_load and plugins_enabled():
            for name in found:
                self.load(name)
        return found

    def discover_one(self, plugin_dir: Path) -> PluginRecord | None:
        manifest_path = plugin_dir / self.MANIFEST_FILE
        if not manifest_path.is_file():
            return None
        try:
            manifest = PluginManifest.from_json(manifest_path)
        except Exception as exc:
            logger.warning("[PLUGIN] bad manifest %s: %s", plugin_dir, exc)
            return None
        rec = PluginRecord(manifest=manifest, path=plugin_dir)
        ok, reason = self.versions.check(manifest)
        if not ok:
            rec.state = PluginState.INCOMPATIBLE
            rec.error = reason
        self._records[manifest.name] = rec
        return rec

    # ── Load / enable / disable (§1, §7 sandbox) ─────────────────────

    def load(self, name: str) -> bool:
        """Load and initialize a plugin (sandbox-isolated) (§7)."""
        with self._lock:
            rec = self._records.get(name)
            if rec is None:
                return False
            if rec.state == PluginState.ENABLED and rec.instance:
                return True
            if rec.state == PluginState.INCOMPATIBLE:
                return False

            ok, issues = self.resolver.resolve(rec.manifest, self._records)
            if not ok:
                rec.state = PluginState.FAILED
                rec.error = "; ".join(issues)
                logger.warning("[PLUGIN] %s deps failed: %s", name, rec.error)
                return False

            allowed, perm_err = self.permissions_allowed(rec.manifest)
            if not allowed:
                rec.state = PluginState.DISABLED
                rec.error = perm_err
                return False

            instance = self._import_plugin(rec)
            if instance is None:
                rec.state = PluginState.FAILED
                return False

            try:
                instance.initialize(dict(self._context))
            except Exception as exc:
                rec.state = PluginState.FAILED
                rec.error = f"initialize: {exc}"
                rec.instance = None
                logger.error("[PLUGIN] %s init failed: %s", name, exc)
                return False

            rec.instance = instance
            rec.state = PluginState.ENABLED
            rec.loaded_at = time.time()
            rec.error = ""
            self._index_capabilities(rec)
            logger.info("[PLUGIN] loaded %s v%s caps=%s", name, rec.manifest.version, rec.manifest.capabilities)
            return True

    def _import_plugin(self, rec: PluginRecord) -> VMPlugin | None:
        app_root = str(self.app_dir.resolve())
        if app_root not in sys.path:
            sys.path.insert(0, app_root)
        entry = rec.path / rec.manifest.entry_point
        if not entry.is_file():
            rec.error = f"entry point missing: {entry.name}"
            return None
        module_name = f"vm_plugin_{rec.manifest.name.replace('-', '_')}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, entry)
            if spec is None or spec.loader is None:
                rec.error = "import spec failed"
                return None
            mod = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = mod
            spec.loader.exec_module(mod)
            if hasattr(mod, "create_plugin"):
                return mod.create_plugin()
            if hasattr(mod, "Plugin"):
                return mod.Plugin()
            rec.error = "no Plugin class or create_plugin()"
            return None
        except Exception as exc:
            rec.error = f"import: {exc}"
            logger.error("[PLUGIN] import %s: %s", rec.manifest.name, exc)
            return None

    def enable(self, name: str) -> bool:
        return self.load(name)

    def disable(self, name: str) -> bool:
        with self._lock:
            rec = self._records.get(name)
            if rec is None:
                return False
            if rec.instance:
                try:
                    rec.instance.shutdown()
                except Exception as exc:
                    logger.warning("[PLUGIN] shutdown %s: %s", name, exc)
                rec.instance = None
            rec.state = PluginState.DISABLED
            self._remove_from_capability_index(name)
            return True

    def reload(self, name: str) -> bool:
        """Hot reload (§8)."""
        self.disable(name)
        mod_name = f"vm_plugin_{name.replace('-', '_')}"
        sys.modules.pop(mod_name, None)
        rec = self._records.get(name)
        if rec:
            rec.state = PluginState.DISCOVERED
            rec.error = ""
        return self.load(name)

    # ── Capability index (§5) ────────────────────────────────────────

    def _index_capabilities(self, rec: PluginRecord) -> None:
        caps = list(rec.manifest.capabilities)
        if rec.instance:
            try:
                caps = list(set(caps + rec.instance.capabilities()))
            except Exception:
                pass
        for cap in caps:
            self._capability_index.setdefault(cap, [])
            if rec.manifest.name not in self._capability_index[cap]:
                self._capability_index[cap].append(rec.manifest.name)

    def _remove_from_capability_index(self, name: str) -> None:
        for cap, names in list(self._capability_index.items()):
            if name in names:
                names.remove(name)

    def plugins_for_capability(self, capability: str) -> list[str]:
        with self._lock:
            return list(self._capability_index.get(capability, []))

    # ── SDK registration hooks (§11) ─────────────────────────────────

    def register_capability_handler(
        self,
        category: str,
        name: str,
        handler: Any,
        *,
        plugin_name: str = "sdk",
    ) -> None:
        with self._lock:
            bucket = self._registries.setdefault(category, {})
            bucket[name] = {"handler": handler, "plugin": plugin_name}

    def list_registrations(self, category: str = "") -> dict[str, Any]:
        with self._lock:
            if category:
                return {
                    k: {"plugin": v.get("plugin"), "callable": callable(v.get("handler"))}
                    for k, v in self._registries.get(category, {}).items()
                }
            return {k: list(v.keys()) for k, v in self._registries.items()}

    def invoke(
        self,
        category: str,
        name: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Invoke a registered capability handler; records call stats."""
        with self._lock:
            entry = (self._registries.get(category) or {}).get(name)
            if not entry:
                raise KeyError(f"No handler {category}/{name}")
            handler = entry["handler"]
            plugin_name = str(entry.get("plugin") or name)
        t0 = time.perf_counter()
        err = False
        try:
            return handler(*args, **kwargs)
        except Exception:
            err = True
            raise
        finally:
            self.record_call(
                plugin_name,
                duration_ms=(time.perf_counter() - t0) * 1000.0,
                error=err,
            )

    def register_plugin_instance(self, name: str, instance: VMPlugin, manifest: PluginManifest | None = None) -> None:
        mf = manifest or PluginManifest(name=name, version=instance.version(), capabilities=instance.capabilities())
        rec = PluginRecord(manifest=mf, path=self.plugins_dir / name, instance=instance, state=PluginState.ENABLED)
        rec.loaded_at = time.time()
        self._records[name] = rec
        self._index_capabilities(rec)

    # ── Diagnostics (§14) ────────────────────────────────────────────

    def get_diagnostics(self) -> list[dict[str, Any]]:
        with self._lock:
            return [rec.to_dict() for rec in self._records.values()]

    def record_call(self, name: str, *, duration_ms: float = 0.0, error: bool = False) -> None:
        rec = self._records.get(name)
        if not rec:
            return
        rec.stats["calls"] = int(rec.stats.get("calls", 0)) + 1
        rec.stats["total_ms"] = float(rec.stats.get("total_ms", 0)) + duration_ms
        if error:
            rec.stats["errors"] = int(rec.stats.get("errors", 0)) + 1

    # ── Status ─────────────────────────────────────────────────────────

    def list_plugins(self) -> list[dict[str, Any]]:
        with self._lock:
            return [rec.to_dict() for rec in sorted(self._records.values(), key=lambda r: r.manifest.name)]

    def get_plugin(self, name: str) -> dict[str, Any] | None:
        rec = self._records.get(name)
        return rec.to_dict() if rec else None

    def get_capabilities(self) -> dict[str, list[str]]:
        with self._lock:
            return {k: list(v) for k, v in self._capability_index.items()}

    def get_status(self) -> dict[str, Any]:
        plugins = self.list_plugins()
        enabled = sum(1 for p in plugins if p.get("state") == PluginState.ENABLED.value)
        return {
            "enabled": plugins_enabled(),
            "core_api_version": CORE_API_VERSION,
            "plugins_dir": str(self.plugins_dir),
            "total": len(plugins),
            "enabled_count": enabled,
            "capabilities": self.get_capabilities(),
            "plugins": plugins,
        }

    def shutdown_all(self) -> None:
        for name in list(self._records.keys()):
            self.disable(name)


_manager: PluginManager | None = None
_manager_lock = threading.Lock()


def get_plugin_manager(*, app_dir: str | Path | None = None) -> PluginManager:
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = PluginManager(app_dir=app_dir)
                if plugins_enabled():
                    _manager.discover(auto_load=True)
    return _manager


def reset_plugin_manager() -> None:
    global _manager
    with _manager_lock:
        if _manager is not None:
            _manager.shutdown_all()
        _manager = None
