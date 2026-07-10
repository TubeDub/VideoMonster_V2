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
    """Marketplace architecture stub (§9) — no store implementation."""

    def __init__(self, manager: PluginManager) -> None:
        self._mgr = manager

    def install(self, source: str, *, name: str = "") -> dict[str, Any]:
        """Install plugin from path or URL (stub — copies local directory)."""
        src = Path(source)
        if not src.is_dir():
            return {"ok": False, "error": "source_not_found"}
        dest_name = name or src.name
        dest = self._mgr.plugins_dir / dest_name
        if dest.exists():
            return {"ok": False, "error": "already_installed"}
        import shutil
        shutil.copytree(src, dest)
        rec = self._mgr.discover_one(dest)
        return {"ok": True, "plugin": dest_name, "state": rec.state.value if rec else "unknown"}

    def update(self, name: str, source: str) -> dict[str, Any]:
        self._mgr.disable(name)
        src = Path(source)
        dest = self._mgr.plugins_dir / name
        if src.is_dir() and dest.is_dir():
            import shutil
            for item in dest.iterdir():
                if item.is_file():
                    item.unlink()
            for item in src.iterdir():
                if item.is_file():
                    shutil.copy2(item, dest / item.name)
        self._mgr.reload(name)
        return {"ok": True, "plugin": name}

    def remove(self, name: str) -> dict[str, Any]:
        self._mgr.disable(name)
        import shutil
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
