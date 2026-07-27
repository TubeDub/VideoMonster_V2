"""P721 Marketplace architecture + P722 Settings Profiles."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from engines.platform_sdk.package import build_vmplugin, read_vmplugin
from engines.platform_sdk.types import MarketplaceKind, PluginDescriptor

ROOT = Path(__file__).resolve().parents[2]


class MarketplaceCatalog:
    """Architectural marketplace — local catalog of .vmplugin packages."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root or (ROOT / "data" / "marketplace"))
        self.root.mkdir(parents=True, exist_ok=True)
        self.catalog_path = self.root / "catalog.json"
        self._items: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if self.catalog_path.is_file():
            try:
                self._items = json.loads(self.catalog_path.read_text(encoding="utf-8"))
            except Exception:
                self._items = []

    def _save(self) -> None:
        self.catalog_path.write_text(json.dumps(self._items, indent=2), encoding="utf-8")

    def publish(
        self,
        descriptor: PluginDescriptor,
        *,
        kind: MarketplaceKind | str,
        package_path: Path | str | None = None,
        secret: str | None = None,
    ) -> dict[str, Any]:
        kind_v = kind.value if isinstance(kind, MarketplaceKind) else str(kind)
        pkg = Path(package_path) if package_path else self.root / f"{descriptor.plugin_id}.vmplugin"
        if not pkg.is_file():
            build_vmplugin(pkg, descriptor=descriptor, secret=secret)
        entry = {
            "plugin_id": descriptor.plugin_id,
            "version": descriptor.version,
            "kind": kind_v,
            "package": str(pkg),
            "description": descriptor.description,
            "author": descriptor.author,
        }
        self._items = [x for x in self._items if x.get("plugin_id") != descriptor.plugin_id]
        self._items.append(entry)
        self._save()
        return entry

    def list_items(self, *, kind: str | None = None) -> list[dict[str, Any]]:
        rows = self._items
        if kind:
            rows = [x for x in rows if x.get("kind") == kind]
        return list(rows)

    def kinds(self) -> list[str]:
        return [k.value for k in MarketplaceKind]

    def plugins_catalog_snapshot(self) -> dict[str, Any]:
        """Read-only view of data/plugin_marketplace_catalog.json (plugins sibling).

        Never installs or enables plugins — ownership stays on PluginMarketplaceAPI.
        """
        catalog_path = ROOT / "data" / "plugin_marketplace_catalog.json"
        if not catalog_path.is_file():
            return {
                "ok": True,
                "configured": False,
                "plugins": [],
                "reason": "plugin_marketplace_catalog_missing",
            }
        try:
            data = json.loads(catalog_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return {"ok": False, "configured": False, "plugins": [], "error": str(exc)}
        plugins = data.get("plugins") if isinstance(data, dict) else []
        if not isinstance(plugins, list):
            plugins = []
        return {
            "ok": True,
            "configured": True,
            "version": data.get("version") if isinstance(data, dict) else 1,
            "plugins": plugins,
            "path": str(catalog_path),
            "install_via": "/api/plugins/marketplace/install",
        }


_MARKET: MarketplaceCatalog | None = None


def get_marketplace(**kwargs: Any) -> MarketplaceCatalog:
    global _MARKET
    if _MARKET is None or kwargs:
        _MARKET = MarketplaceCatalog(**kwargs)
    return _MARKET


# --- P722 Settings Profiles ---

DEFAULT_SETTINGS_PROFILES: dict[str, dict[str, Any]] = {
    "Movie": {"style": "Movie", "tempo": 1.0, "emotion": "calm", "dub_mode": "cinema"},
    "Anime": {"style": "Anime", "tempo": 1.12, "emotion": "joy", "dub_mode": "expressive"},
    "Podcast": {"style": "Podcast", "tempo": 1.05, "emotion": "calm", "dub_mode": "talk"},
    "YouTube": {"style": "News", "tempo": 1.08, "emotion": "calm", "dub_mode": "web"},
    "Interview": {"style": "Interview", "tempo": 1.02, "emotion": "calm", "dub_mode": "dialogue"},
    "Kids": {"style": "Anime", "tempo": 1.0, "emotion": "joy", "dub_mode": "kids"},
    "Documentary": {"style": "Documentary", "tempo": 0.95, "emotion": "calm", "dub_mode": "doc"},
}


def profiles_path() -> Path:
    return ROOT / "data" / "settings_profiles.json"


def list_profiles() -> dict[str, dict[str, Any]]:
    path = profiles_path()
    custom = {}
    if path.is_file():
        try:
            custom = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            custom = {}
    merged = dict(DEFAULT_SETTINGS_PROFILES)
    if isinstance(custom, dict):
        profiles = custom.get("profiles") if "profiles" in custom else custom
        if isinstance(profiles, dict):
            merged.update(profiles)
    return merged


def save_profile(name: str, settings: dict[str, Any]) -> Path:
    path = profiles_path()
    data = {"profiles": list_profiles()}
    data["profiles"][name] = settings
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def get_profile(name: str) -> dict[str, Any]:
    return list_profiles().get(name) or dict(DEFAULT_SETTINGS_PROFILES["Movie"])
