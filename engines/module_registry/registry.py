"""Module registry — release channels, menu visibility, developer mode."""

from __future__ import annotations

import json
import os
import threading
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

VALID_STATUSES = frozenset({"stable", "beta", "development", "disabled", "coming_soon"})

STATUS_COLORS = {
    "stable": "#3ecf8e",
    "beta": "#f6ad55",
    "development": "#f56565",
    "disabled": "#6b7280",
    "coming_soon": "#9ca3af",
}

STATUS_LABELS = {
    "stable": "Stable",
    "beta": "Beta",
    "development": "Development",
    "disabled": "Disabled",
    "coming_soon": "Coming Soon",
}

STATUS_EMOJI = {
    "stable": "🟢",
    "beta": "🟡",
    "development": "🔴",
    "disabled": "⚫",
    "coming_soon": "🚧",
}

_LOCK = threading.RLock()


@dataclass
class ModuleRecord:
    id: str
    name: dict[str, str]
    route: str = ""
    icon: str = ""
    status: str = "development"
    visible_to_users: bool = False
    show_in_menu: bool = True
    show_experimental_badge: bool = False
    pro_only: bool = False
    kind: str = "page"
    i18n_key: str = ""
    platform_key: str = ""
    action: str = ""
    developer_only: bool = False
    feature_id: str = ""
    coming_soon: bool = False

    def label(self, lang: str = "ru") -> str:
        base = (lang or "ru").split("-")[0].lower()
        return (
            self.name.get(base)
            or self.name.get("ru")
            or self.name.get("en")
            or self.id
        )

    def to_public_dict(self, *, lang: str = "ru", developer_mode: bool = False) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label(lang),
            "route": self.route,
            "icon": self.icon,
            "status": self.status,
            "status_color": STATUS_COLORS.get(self.status, "#888"),
            "status_emoji": STATUS_EMOJI.get(self.status, ""),
            "status_label": STATUS_LABELS.get(self.status, self.status),
            "visible_to_users": self.visible_to_users,
            "show_in_menu": self.show_in_menu,
            "show_experimental_badge": self.show_experimental_badge,
            "pro_only": self.pro_only,
            "kind": self.kind,
            "action": self.action,
            "i18n_key": self.i18n_key,
            "platform_key": self.platform_key,
            "developer_only": self.developer_only,
            "feature_id": self.feature_id,
            "experimental": self.show_experimental_badge
            and self.status in ("beta", "development"),
            "coming_soon": self.coming_soon,
        }


class ModuleRegistry:
    def __init__(self, app_dir: Path):
        self.app_dir = Path(app_dir)
        self._default_path = self.app_dir / "data" / "module_registry.json"
        self._local_path = self.app_dir / "data" / "module_registry.local.json"
        self._show_beta = False
        self._modules: dict[str, ModuleRecord] = {}
        self.reload()

    def reload(self) -> None:
        with _LOCK:
            raw = self._load_json(self._default_path)
            local = self._load_json(self._local_path) if self._local_path.is_file() else {}
            self._show_beta = bool(local.get("show_beta_to_users", raw.get("show_beta_to_users", False)))
            merged: dict[str, ModuleRecord] = {}
            for row in raw.get("modules") or []:
                rec = self._parse_module(row)
                if not rec:
                    continue
                ov = (local.get("modules") or {}).get(rec.id) or {}
                if ov:
                    rec = self._apply_override(rec, ov)
                merged[rec.id] = rec
            for mid, ov in (local.get("modules") or {}).items():
                if mid not in merged and isinstance(ov, dict):
                    rec = self._parse_module({"id": mid, **ov})
                    if rec:
                        merged[mid] = rec
            self._modules = merged

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        if not path.is_file():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    @staticmethod
    def _parse_module(row: dict[str, Any]) -> ModuleRecord | None:
        mid = str(row.get("id") or "").strip()
        if not mid:
            return None
        status = str(row.get("status") or "development").strip().lower()
        if status not in VALID_STATUSES:
            status = "development"
        name = row.get("name") or {}
        if isinstance(name, str):
            name = {"ru": name, "uk": name, "en": name}
        return ModuleRecord(
            id=mid,
            name={str(k): str(v) for k, v in name.items()},
            route=str(row.get("route") or ""),
            icon=str(row.get("icon") or ""),
            status=status,
            visible_to_users=bool(row.get("visible_to_users", False)),
            show_in_menu=bool(row.get("show_in_menu", True)),
            show_experimental_badge=bool(row.get("show_experimental_badge", False)),
            pro_only=bool(row.get("pro_only", False)),
            kind=str(row.get("kind") or "page"),
            i18n_key=str(row.get("i18n_key") or ""),
            platform_key=str(row.get("platform_key") or ""),
            action=str(row.get("action") or ""),
            developer_only=bool(row.get("developer_only", False)),
            feature_id=str(row.get("feature_id") or ""),
            coming_soon=bool(row.get("coming_soon", False)),
        )

    @staticmethod
    def _apply_override(rec: ModuleRecord, ov: dict[str, Any]) -> ModuleRecord:
        data = rec.__dict__.copy()
        for key in (
            "route",
            "icon",
            "status",
            "visible_to_users",
            "show_in_menu",
            "show_experimental_badge",
            "pro_only",
            "kind",
            "i18n_key",
            "platform_key",
            "action",
            "developer_only",
            "feature_id",
            "coming_soon",
        ):
            if key in ov:
                data[key] = ov[key]
        if "name" in ov and isinstance(ov["name"], dict):
            data["name"] = {**rec.name, **{str(k): str(v) for k, v in ov["name"].items()}}
        if data.get("status") not in VALID_STATUSES:
            data["status"] = rec.status
        return ModuleRecord(**data)

    def get(self, module_id: str) -> ModuleRecord | None:
        return self._modules.get(module_id)

    def all_modules(self) -> list[ModuleRecord]:
        return list(self._modules.values())

    def show_beta_to_users(self) -> bool:
        return self._show_beta

    def set_show_beta_to_users(self, value: bool) -> None:
        self._save_local_patch({"show_beta_to_users": bool(value)})
        self._show_beta = bool(value)

    def update_module(self, module_id: str, patch: dict[str, Any]) -> ModuleRecord | None:
        if module_id not in self._modules:
            return None
        allowed = {
            "status",
            "visible_to_users",
            "show_in_menu",
            "show_experimental_badge",
            "pro_only",
            "name",
            "coming_soon",
        }
        clean = {k: v for k, v in patch.items() if k in allowed}
        if "status" in clean:
            st = str(clean["status"]).lower()
            if st not in VALID_STATUSES:
                del clean["status"]
            else:
                clean["status"] = st
        self._save_local_patch({"modules": {module_id: clean}})
        self.reload()
        return self.get(module_id)

    def _save_local_patch(self, patch: dict[str, Any]) -> None:
        local = self._load_json(self._local_path)
        if "show_beta_to_users" in patch:
            local["show_beta_to_users"] = patch["show_beta_to_users"]
        if "modules" in patch:
            mods = local.setdefault("modules", {})
            for mid, ov in patch["modules"].items():
                cur = mods.get(mid) or {}
                if isinstance(cur, dict) and isinstance(ov, dict):
                    cur.update(ov)
                    mods[mid] = cur
                else:
                    mods[mid] = ov
        self._local_path.parent.mkdir(parents=True, exist_ok=True)
        self._local_path.write_text(
            json.dumps(local, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def nav_modules(
        self,
        *,
        developer_mode: bool,
        lang: str = "ru",
        user_mode: str = "basic",
    ) -> list[dict[str, Any]]:
        from engines.feature_flags.manager import get_feature_manager

        effective_mode = "developer" if developer_mode else user_mode
        fm = get_feature_manager(self.app_dir)
        out: list[dict[str, Any]] = []
        for rec in self._modules.values():
            if not rec.show_in_menu:
                continue
            if rec.developer_only and not developer_mode:
                continue
            if rec.feature_id and not fm.is_visible_in_nav(
                rec.feature_id,
                user_mode=effective_mode,  # type: ignore[arg-type]
                developer_session=developer_mode,
                show_beta=self._show_beta,
            ):
                continue
            if developer_mode:
                row = rec.to_public_dict(lang=lang, developer_mode=True)
                if rec.feature_id:
                    feat = fm.get(rec.feature_id)
                    if feat:
                        row["feature_status"] = feat.status
                        row["feature_enabled"] = fm.is_enabled(
                            rec.feature_id,
                            user_mode="developer",
                            developer_session=True,
                            show_beta=self._show_beta,
                        )
                out.append(row)
                continue
            if not module_visible_to_user(rec, show_beta=self._show_beta):
                continue
            if rec.feature_id and not fm.is_enabled(
                rec.feature_id,
                user_mode=user_mode,  # type: ignore[arg-type]
                developer_session=False,
                show_beta=self._show_beta,
            ):
                continue
            out.append(rec.to_public_dict(lang=lang, developer_mode=False))
        return out

    def route_map(self) -> dict[str, str]:
        """Exact route path -> module id (first stable wins on duplicates)."""
        priority = {"stable": 0, "beta": 1, "development": 2, "disabled": 3}
        pairs: list[tuple[str, str, int]] = []
        for rec in self._modules.values():
            if not rec.route:
                continue
            key = rec.route.rstrip("/") or "/"
            pairs.append((key, rec.id, priority.get(rec.status, 9)))
        pairs.sort(key=lambda x: (x[0], x[2]))
        m: dict[str, str] = {}
        for key, mid, _ in pairs:
            if key not in m:
                m[key] = mid
        return m

    def snapshot(self, *, developer_mode: bool, lang: str = "ru") -> dict[str, Any]:
        return {
            "version": 1,
            "developer_mode": developer_mode,
            "show_beta_to_users": self._show_beta,
            "modules": [
                rec.to_public_dict(lang=lang, developer_mode=developer_mode)
                for rec in self._modules.values()
            ],
            "nav": self.nav_modules(developer_mode=developer_mode, lang=lang),
        }


def module_visible_to_user(rec: ModuleRecord, *, show_beta: bool) -> bool:
    _ = show_beta
    if not rec.visible_to_users:
        return False
    if rec.coming_soon:
        return True
    return rec.status == "stable"


def module_accessible(
    rec: ModuleRecord | None,
    *,
    developer_mode: bool,
    show_beta: bool,
    user_mode: str = "basic",
    app_dir: Path | None = None,
) -> bool:
    if rec is None:
        return True
    if rec.developer_only and not developer_mode:
        return False
    if rec.feature_id:
        from engines.feature_flags.manager import get_feature_manager

        fm = get_feature_manager(app_dir)
        if developer_mode:
            if not fm.is_visible_in_nav(
                rec.feature_id,
                user_mode="developer",
                developer_session=True,
                show_beta=show_beta,
            ):
                return False
            if not fm.is_enabled(
                rec.feature_id,
                user_mode="developer",
                developer_session=True,
                show_beta=show_beta,
            ):
                return False
        elif not fm.is_enabled(
            rec.feature_id,
            user_mode=user_mode,  # type: ignore[arg-type]
            developer_session=False,
            show_beta=show_beta,
        ):
            return False
    if developer_mode:
        return True
    if rec.status in ("development", "disabled"):
        return False
    if rec.status == "beta":
        return False
    if rec.status == "stable":
        return rec.visible_to_users
    return False


def is_developer_session(
    *,
    request_headers: dict | None = None,
    request_cookies: dict | None = None,
) -> bool:
    """Server-side developer session (owner / env / client dev on owner host)."""
    if os.getenv("VM_DEVELOPER_MODE", "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    if os.getenv("VM_DEV_MODE", "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    try:
        from flask import has_request_context, request as flask_request

        if request_headers is None and has_request_context():
            request_headers = dict(flask_request.headers)
        if request_cookies is None and has_request_context():
            request_cookies = dict(flask_request.cookies)
    except Exception:
        pass
    try:
        from engines.owner_first_run import is_owner_host

        if not is_owner_host():
            return False
    except Exception:
        return False
    hdrs = {k.lower(): v for k, v in (request_headers or {}).items()}
    if hdrs.get("x-vm-dev-mode") in ("1", "true", "yes"):
        return True
    if hdrs.get("x-vm-client-dev-mode") in ("1", "true", "yes"):
        return True
    cookies = {k.lower(): v for k, v in (request_cookies or {}).items()}
    if cookies.get("vm_client_dev_mode") in ("1", "true", "yes"):
        return True
    return False


_REGISTRY: dict[str, ModuleRegistry] = {}


def get_registry(app_dir: Path | None = None) -> ModuleRegistry:
    base = Path(app_dir or Path(__file__).resolve().parents[2])
    key = str(base.resolve())
    if key not in _REGISTRY:
        _REGISTRY[key] = ModuleRegistry(base)
    return _REGISTRY[key]


def resolve_module_for_path(path: str, app_dir: Path) -> ModuleRecord | None:
    reg = get_registry(app_dir)
    norm = (path or "/").split("?")[0].rstrip("/") or "/"
    rmap = reg.route_map()
    if norm in rmap:
        return reg.get(rmap[norm])
    for route, mid in sorted(rmap.items(), key=lambda x: len(x[0]), reverse=True):
        if route != "/" and norm.startswith(route + "/"):
            return reg.get(mid)
    if norm.startswith("/api/platform/"):
        return reg.get("media_browser")
    if norm.startswith("/api/cloud/") or norm.startswith("/cloud"):
        return reg.get("cloud_platform")
    if norm.startswith("/api/dub-studio/") or norm.startswith("/dub-studio"):
        return reg.get("dub_studio")
    if norm.startswith("/dev/panel"):
        return reg.get("feature_panel")
    if norm.startswith("/dev/"):
        return reg.get("module_manager")
    return None
