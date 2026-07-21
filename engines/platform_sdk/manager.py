"""P703 Plugin Manager — install / update / disable / remove / compatibility."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from engines.platform_sdk.event_bus import get_platform_bus
from engines.platform_sdk.lifecycle import transition
from engines.platform_sdk.package import extract_vmplugin, read_vmplugin
from engines.platform_sdk.security import PluginSandbox
from engines.platform_sdk.types import (
    ExtensionPoint,
    PluginDescriptor,
    PluginHealthReport,
    PluginLifecycle,
    PlatformEvent,
    TrustLevel,
)
from engines.platform_sdk.validator import validate_plugin

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PLUGINS_DIR = ROOT / "plugins"
DEFAULT_INSTALLED = ROOT / "data" / "platform_plugins"


class PlatformPluginManager:
    def __init__(
        self,
        install_dir: Path | str | None = None,
        *,
        core_version: str = "6.0.0",
    ) -> None:
        self.install_dir = Path(install_dir or DEFAULT_INSTALLED)
        self.install_dir.mkdir(parents=True, exist_ok=True)
        self.core_version = core_version
        self._index_path = self.install_dir / "index.json"
        self._plugins: dict[str, dict[str, Any]] = {}
        self._load_index()

    def _load_index(self) -> None:
        if self._index_path.is_file():
            try:
                self._plugins = json.loads(self._index_path.read_text(encoding="utf-8"))
            except Exception:
                self._plugins = {}

    def _save_index(self) -> None:
        self._index_path.write_text(
            json.dumps(self._plugins, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def list_plugins(self) -> list[dict[str, Any]]:
        return list(self._plugins.values())

    def get(self, plugin_id: str) -> dict[str, Any] | None:
        return self._plugins.get(plugin_id)

    def install_from_vmplugin(
        self,
        package_path: Path | str,
        *,
        secret: str | None = None,
    ) -> dict[str, Any]:
        info = read_vmplugin(package_path, secret=secret)
        desc = PluginDescriptor.from_dict(info["descriptor"])
        validation = info["validation"]
        if not validation.get("ok"):
            raise ValueError(f"Plugin validation failed: {validation.get('issues')}")

        target = self.install_dir / desc.plugin_id
        if target.exists():
            shutil.rmtree(target)
        extract_vmplugin(package_path, target)

        record = {
            "descriptor": desc.to_dict(),
            "lifecycle": PluginLifecycle.INSTALLED.value,
            "trust": info.get("trust") or TrustLevel.UNKNOWN.value,
            "path": str(target),
            "enabled": False,
        }
        # Installed → Verified
        record["lifecycle"] = transition(
            PluginLifecycle.INSTALLED, PluginLifecycle.VERIFIED
        ).value
        if validation.get("trust") == TrustLevel.VERIFIED.value:
            record["trust"] = TrustLevel.VERIFIED.value
        elif validation.get("trust") == TrustLevel.BLOCKED.value:
            record["trust"] = TrustLevel.BLOCKED.value
            raise ValueError("Plugin blocked by signature")

        self._plugins[desc.plugin_id] = record
        self._save_index()
        get_platform_bus().publish(
            PlatformEvent.PLUGIN_STATE_CHANGED,
            {"plugin_id": desc.plugin_id, "lifecycle": record["lifecycle"]},
        )
        return record

    def install_descriptor(self, descriptor: PluginDescriptor) -> dict[str, Any]:
        """Register a descriptor-only plugin (code already on disk / stub)."""
        validation = validate_plugin(descriptor, core_version=self.core_version)
        if not validation.get("ok"):
            raise ValueError(validation.get("issues"))
        record = {
            "descriptor": descriptor.to_dict(),
            "lifecycle": PluginLifecycle.VERIFIED.value,
            "trust": validation.get("trust") or TrustLevel.UNKNOWN.value,
            "path": "",
            "enabled": False,
        }
        self._plugins[descriptor.plugin_id] = record
        self._save_index()
        return record

    def load(self, plugin_id: str) -> dict[str, Any]:
        rec = self._require(plugin_id)
        rec["lifecycle"] = transition(rec["lifecycle"], PluginLifecycle.LOADED).value
        self._save_index()
        return rec

    def initialize(self, plugin_id: str) -> dict[str, Any]:
        rec = self._require(plugin_id)
        if rec["lifecycle"] == PluginLifecycle.VERIFIED.value:
            rec["lifecycle"] = transition(rec["lifecycle"], PluginLifecycle.LOADED).value
        rec["lifecycle"] = transition(rec["lifecycle"], PluginLifecycle.INITIALIZED).value
        self._save_index()
        return rec

    def start(self, plugin_id: str) -> dict[str, Any]:
        rec = self._require(plugin_id)
        if rec.get("trust") == TrustLevel.BLOCKED.value:
            raise PermissionError("Blocked plugin cannot run")
        # Ensure initialized
        life = rec["lifecycle"]
        if life in {PluginLifecycle.VERIFIED.value, PluginLifecycle.LOADED.value}:
            if life == PluginLifecycle.VERIFIED.value:
                rec["lifecycle"] = transition(life, PluginLifecycle.LOADED).value
                life = rec["lifecycle"]
            if life == PluginLifecycle.LOADED.value:
                rec["lifecycle"] = transition(life, PluginLifecycle.INITIALIZED).value
        rec["lifecycle"] = transition(rec["lifecycle"], PluginLifecycle.RUNNING).value
        rec["enabled"] = True
        self._save_index()
        get_platform_bus().publish(
            PlatformEvent.PLUGIN_STATE_CHANGED,
            {"plugin_id": plugin_id, "lifecycle": rec["lifecycle"]},
        )
        return rec

    def pause(self, plugin_id: str) -> dict[str, Any]:
        rec = self._require(plugin_id)
        rec["lifecycle"] = transition(rec["lifecycle"], PluginLifecycle.PAUSED).value
        self._save_index()
        return rec

    def stop(self, plugin_id: str) -> dict[str, Any]:
        rec = self._require(plugin_id)
        rec["lifecycle"] = transition(rec["lifecycle"], PluginLifecycle.STOPPED).value
        rec["enabled"] = False
        self._save_index()
        return rec

    def disable(self, plugin_id: str) -> dict[str, Any]:
        return self.stop(plugin_id)

    def remove(self, plugin_id: str) -> dict[str, Any]:
        rec = self._require(plugin_id)
        path = rec.get("path")
        rec["lifecycle"] = transition(rec["lifecycle"], PluginLifecycle.REMOVED).value
        if path and Path(path).is_dir():
            shutil.rmtree(path, ignore_errors=True)
        self._plugins.pop(plugin_id, None)
        self._save_index()
        get_platform_bus().publish(
            PlatformEvent.PLUGIN_STATE_CHANGED,
            {"plugin_id": plugin_id, "lifecycle": PluginLifecycle.REMOVED.value},
        )
        return rec

    def update(self, plugin_id: str, package_path: Path | str, *, secret: str | None = None) -> dict[str, Any]:
        if plugin_id in self._plugins:
            try:
                self.remove(plugin_id)
            except Exception:
                self._plugins.pop(plugin_id, None)
        return self.install_from_vmplugin(package_path, secret=secret)

    def sandbox_for(self, plugin_id: str) -> PluginSandbox:
        rec = self._require(plugin_id)
        perms = rec["descriptor"].get("permissions") or []
        # Normalize dict permissions from legacy
        if isinstance(perms, dict):
            perms = [k for k, v in perms.items() if v]
        return PluginSandbox(plugin_id, set(map(str, perms)))

    def health(self, plugin_id: str) -> PluginHealthReport:
        rec = self._require(plugin_id)
        d = rec["descriptor"]
        return PluginHealthReport(
            plugin_id=plugin_id,
            ok=rec.get("lifecycle") == PluginLifecycle.RUNNING.value,
            version=str(d.get("version") or ""),
            errors=[],
            metrics={"enabled": bool(rec.get("enabled"))},
            performance={},
        )

    def discover_builtin(self) -> list[dict[str, Any]]:
        """Bridge existing plugins/ manifests without modifying them."""
        found: list[dict[str, Any]] = []
        root = DEFAULT_PLUGINS_DIR
        if not root.is_dir():
            return found
        for child in root.iterdir():
            manifest = child / "plugin.json"
            if not manifest.is_file():
                continue
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                desc = PluginDescriptor.from_dict(
                    {
                        **data,
                        "plugin_id": data.get("name") or child.name,
                        "permissions": _normalize_perms(data.get("permissions")),
                        "extension_points": _map_capabilities(data.get("capabilities") or []),
                    }
                )
                if desc.plugin_id not in self._plugins:
                    self.install_descriptor(desc)
                found.append(desc.to_dict())
            except Exception:
                continue
        return found

    def _require(self, plugin_id: str) -> dict[str, Any]:
        rec = self._plugins.get(plugin_id)
        if not rec:
            raise KeyError(f"Plugin not found: {plugin_id}")
        return rec


def _normalize_perms(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(x) for x in raw]
    if isinstance(raw, dict):
        mapping = {
            "file": "Filesystem",
            "network": "Internet",
            "audio": "Read Audio",
            "video": "Read Project",
            "memory": "Read Project",
            "gpu": "Generate Audio",
        }
        return [mapping.get(k, k) for k, v in raw.items() if v]
    return ["Read Project"]


def _map_capabilities(caps: list[str]) -> list[str]:
    m = {
        "stt": ExtensionPoint.ASR.value,
        "translation": ExtensionPoint.TRANSLATION.value,
        "tts": ExtensionPoint.TTS.value,
        "export": ExtensionPoint.EXPORT.value,
        "lip_sync": ExtensionPoint.ALIGNMENT.value,
        "timing": ExtensionPoint.SCHEDULER.value,
        "review": ExtensionPoint.STUDIO.value,
    }
    out = []
    for c in caps:
        out.append(m.get(str(c).lower(), ExtensionPoint.DIAGNOSTICS.value))
    return out


_MANAGER: PlatformPluginManager | None = None


def get_plugin_manager(**kwargs: Any) -> PlatformPluginManager:
    global _MANAGER
    if _MANAGER is None or kwargs:
        _MANAGER = PlatformPluginManager(**kwargs)
    return _MANAGER
