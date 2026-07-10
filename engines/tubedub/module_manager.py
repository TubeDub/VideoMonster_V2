"""Platform module manager — unified lifecycle orchestration."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from engines.tubedub.adapters.base import create_adapter
from engines.tubedub.catalog import ModuleCatalog
from engines.tubedub.lifecycle import ModuleContext, PlatformModule
from engines.tubedub.release import ReleaseChannel, channel_visible, parse_release_channel


class PlatformModuleManager:
    def __init__(self, app_dir: Path):
        self.app_dir = Path(app_dir)
        self._catalog = ModuleCatalog(self.app_dir)
        self._modules: dict[str, PlatformModule] = {}
        self._lock = threading.RLock()
        self._bootstrapped = False

    def catalog(self) -> ModuleCatalog:
        return self._catalog

    def reload_catalog(self) -> None:
        self._catalog.reload()

    def bootstrap(self) -> None:
        with self._lock:
            if self._bootstrapped:
                return
            from engines.tubedub.sync import sync_catalog_with_features
            from engines.tubedub.plugin_host import get_plugin_host
            from engines.tubedub.pipeline.plugins import register_pipeline_stage_plugins

            sync_catalog_with_features(self.app_dir)
            host = get_plugin_host()
            host.import_dub_studio_fx()
            register_pipeline_stage_plugins()
            for entry in self._catalog.all():
                if entry.id in self._modules:
                    continue
                mod = create_adapter(entry.id, entry.adapter)
                if not mod:
                    continue
                ctx = ModuleContext(
                    app_dir=str(self.app_dir),
                    module_id=entry.id,
                    api_namespace=entry.api_namespace or entry.id,
                    config={"feature_id": entry.feature_id},
                )
                mod.initialize(ctx)
                ch = parse_release_channel(entry.release_channel)
                if entry.feature_id:
                    from engines.tubedub.sync import effective_release_channel

                    ch = effective_release_channel(self.app_dir, entry.feature_id)
                if ch != ReleaseChannel.DISABLED:
                    mod.load()
                self._modules[entry.id] = mod
            self._bootstrapped = True

    def get(self, module_id: str) -> PlatformModule | None:
        return self._modules.get(module_id)

    def all_modules(self) -> list[PlatformModule]:
        return list(self._modules.values())

    def health_all(self) -> list[dict[str, Any]]:
        return [m.health_check().to_dict() for m in self.all_modules()]

    def snapshot(
        self,
        *,
        developer_session: bool = False,
        user_mode: str = "basic",
    ) -> dict[str, Any]:
        from engines.tubedub.api_bus import get_api_bus
        from engines.tubedub.plugin_host import get_plugin_host

        entries = []
        for e in self._catalog.all():
            ch = parse_release_channel(e.release_channel)
            mod = self._modules.get(e.id)
            entries.append(
                {
                    **e.to_dict(),
                    "visible": channel_visible(
                        ch, developer_session=developer_session, user_mode=user_mode
                    ),
                    "lifecycle_state": mod.state.value if mod else "unloaded",
                    "health": mod.health_check().to_dict() if mod else None,
                }
            )
        return {
            "modules": entries,
            "api_routes": get_api_bus().list_routes(),
            "plugins": get_plugin_host().list_plugins(),
            "bootstrapped": self._bootstrapped,
        }

    def run_module(self, module_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        mod = self.get(module_id)
        if not mod:
            raise KeyError(module_id)
        return mod.run(dict(payload or {}))

    def stop_all(self) -> None:
        for mod in self.all_modules():
            try:
                if mod.state.value in ("loaded", "running"):
                    mod.stop()
            except Exception:
                pass

    def dispose_all(self) -> None:
        for mod in self.all_modules():
            try:
                mod.dispose()
            except Exception:
                pass
        self._modules.clear()
        self._bootstrapped = False


_MANAGERS: dict[str, PlatformModuleManager] = {}
_MGR_LOCK = threading.Lock()


def get_module_manager(app_dir: Path) -> PlatformModuleManager:
    key = str(Path(app_dir).resolve())
    with _MGR_LOCK:
        if key not in _MANAGERS:
            _MANAGERS[key] = PlatformModuleManager(Path(app_dir))
        return _MANAGERS[key]
