"""Feature Flags Manager — registration, enable/disable, safe bootstrap."""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engines.feature_flags.modes import UserMode, normalize_mode, visible_for_mode

VALID_STATUSES = frozenset(
    {
        "READY",
        "BETA",
        "ALPHA",
        "DEVELOPMENT",
        "EXPERIMENTAL",
        "DISABLED",
        "NOT_IMPLEMENTED",
        "stable",
        "beta",
        "development",
        "disabled",
    }
)

STATUS_COLORS = {
    "READY": "#3ecf8e",
    "BETA": "#f6ad55",
    "ALPHA": "#fb923c",
    "DEVELOPMENT": "#f56565",
    "EXPERIMENTAL": "#a78bfa",
    "DISABLED": "#6b7280",
    "NOT_IMPLEMENTED": "#374151",
}

_LOCK = threading.RLock()
_MANAGERS: dict[str, "FeatureManager"] = {}


@dataclass
class FeatureRecord:
    id: str
    label: str
    env_key: str
    enabled: bool = False
    status: str = "DISABLED"
    tier: str = "experimental"
    load_priority: int = 100
    dependencies: list[str] = field(default_factory=list)
    readiness_pct: int = 0
    version: str = "0.0"
    module_path: str = ""
    blueprint: str = ""
    modes: list[str] = field(default_factory=lambda: ["developer"])
    release_channel: str = ""
    auto_disabled: bool = False
    auto_disable_reason: str = ""
    last_error: str = ""
    loaded_at_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "env_key": self.env_key,
            "enabled": self.enabled and not self.auto_disabled,
            "configured_enabled": self.enabled,
            "auto_disabled": self.auto_disabled,
            "auto_disable_reason": self.auto_disable_reason,
            "status": self.status,
            "status_color": STATUS_COLORS.get(self.status.upper(), "#888"),
            "tier": self.tier,
            "load_priority": self.load_priority,
            "dependencies": list(self.dependencies),
            "readiness_pct": self.readiness_pct,
            "version": self.version,
            "module_path": self.module_path,
            "blueprint": self.blueprint,
            "modes": list(self.modes),
            "release_channel": self.release_channel or self._derived_release_channel(),
            "last_error": self.last_error,
            "loaded_at_ms": self.loaded_at_ms,
        }

    def _derived_release_channel(self) -> str:
        if self.auto_disabled or not self.enabled:
            return "DISABLED"
        st = (self.status or "").upper()
        if st in ("READY", "STABLE"):
            return "RELEASE"
        return "DEVELOPER"


class FeatureManager:
    def __init__(self, app_dir: Path):
        self.app_dir = Path(app_dir)
        self._default_path = self.app_dir / "data" / "feature_flags.json"
        self._local_path = self.app_dir / "data" / "feature_flags.local.json"
        self._features: dict[str, FeatureRecord] = {}
        self._bootstrapped = False
        self.reload()

    def reload(self) -> None:
        with _LOCK:
            raw = self._load_json(self._default_path)
            local = self._load_json(self._local_path) if self._local_path.is_file() else {}
            merged: dict[str, FeatureRecord] = {}
            for row in raw.get("features") or []:
                rec = self._parse(row)
                if not rec:
                    continue
                ov = (local.get("features") or {}).get(rec.id) or {}
                if ov:
                    rec = self._apply_override(rec, ov)
                merged[rec.id] = rec
            self._features = merged

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        if not path.is_file():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    @staticmethod
    def _parse(row: dict[str, Any]) -> FeatureRecord | None:
        fid = str(row.get("id") or "").strip()
        if not fid:
            return None
        st = str(row.get("status") or "DISABLED").strip().upper()
        rc = str(row.get("release_channel") or "").strip().upper()
        return FeatureRecord(
            id=fid,
            label=str(row.get("label") or fid),
            env_key=str(row.get("env_key") or f"FEATURE_{fid.upper()}"),
            enabled=bool(row.get("enabled", False)),
            status=st if st in {s.upper() for s in VALID_STATUSES} else "DISABLED",
            tier=str(row.get("tier") or "experimental"),
            load_priority=int(row.get("load_priority") or 100),
            dependencies=[str(x) for x in (row.get("dependencies") or [])],
            readiness_pct=int(row.get("readiness_pct") or 0),
            version=str(row.get("version") or "0.0"),
            module_path=str(row.get("module_path") or ""),
            blueprint=str(row.get("blueprint") or ""),
            modes=[str(m).lower() for m in (row.get("modes") or ["developer"])],
            release_channel=rc,
        )

    @staticmethod
    def _apply_override(rec: FeatureRecord, ov: dict[str, Any]) -> FeatureRecord:
        d = rec.to_dict()
        for key in (
            "enabled",
            "status",
            "tier",
            "load_priority",
            "readiness_pct",
            "version",
            "module_path",
            "blueprint",
            "modes",
            "dependencies",
            "label",
            "release_channel",
        ):
            if key in ov:
                d[key] = ov[key]
        d["id"] = rec.id
        d["env_key"] = rec.env_key
        return FeatureManager._parse(
            {
                "id": d["id"],
                "label": d.get("label"),
                "env_key": d["env_key"],
                "enabled": d.get("configured_enabled", d.get("enabled")),
                "status": d.get("status"),
                "tier": d.get("tier"),
                "load_priority": d.get("load_priority"),
                "dependencies": d.get("dependencies"),
                "readiness_pct": d.get("readiness_pct"),
                "version": d.get("version"),
                "module_path": d.get("module_path"),
                "blueprint": d.get("blueprint"),
                "modes": d.get("modes"),
                "release_channel": d.get("release_channel"),
            }
        ) or rec

    def set_release_channel(self, feature_id: str, channel: str) -> FeatureRecord | None:
        """Single flag to switch DISABLED / DEVELOPER / RELEASE."""
        from engines.tubedub.release import ReleaseChannel, parse_release_channel

        rec = self.get(feature_id)
        if not rec:
            return None
        ch = parse_release_channel(channel)
        local = self._load_json(self._local_path)
        feats = local.setdefault("features", {})
        cur = feats.get(feature_id) or {}
        cur["release_channel"] = ch.value
        if ch == ReleaseChannel.DISABLED:
            cur["enabled"] = False
        elif ch == ReleaseChannel.DEVELOPER:
            cur["enabled"] = True
            cur["status"] = cur.get("status") or "DEVELOPMENT"
        elif ch == ReleaseChannel.RELEASE:
            cur["enabled"] = True
            cur["status"] = "READY"
        feats[feature_id] = cur
        self._local_path.write_text(
            json.dumps(local, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.reload()
        return self.get(feature_id)

    def _release_channel_enabled(
        self,
        rec: FeatureRecord,
        *,
        developer_session: bool,
        user_mode: UserMode,
    ) -> bool:
        from engines.tubedub.release import channel_visible, parse_release_channel

        ch = parse_release_channel(rec.release_channel or rec._derived_release_channel())
        if not self._effective_enabled(rec) and ch != parse_release_channel("DISABLED"):
            pass
        if ch == parse_release_channel("DISABLED"):
            return False
        if not channel_visible(ch, developer_session=developer_session, user_mode=user_mode):
            return False
        return True

    def _env_override(self, rec: FeatureRecord) -> bool | None:
        key = rec.env_key
        raw = (os.getenv(key) or "").strip().lower()
        if raw in ("1", "true", "yes", "on"):
            return True
        if raw in ("0", "false", "no", "off"):
            return False
        return None

    def get(self, feature_id: str) -> FeatureRecord | None:
        return self._features.get(feature_id)

    def all_features(self) -> list[FeatureRecord]:
        return sorted(self._features.values(), key=lambda r: (r.load_priority, r.id))

    def _effective_enabled(self, rec: FeatureRecord) -> bool:
        if rec.auto_disabled:
            return False
        env = self._env_override(rec)
        if env is not None:
            return env
        return rec.enabled

    def is_enabled(
        self,
        feature_id: str,
        *,
        user_mode: UserMode = "basic",
        developer_session: bool = False,
        show_beta: bool = False,
        ignore_auto_disabled: bool = True,
    ) -> bool:
        rec = self.get(feature_id)
        if not rec:
            return False
        if rec.auto_disabled and not ignore_auto_disabled:
            return False
        if rec.auto_disabled:
            return False
        if not self._effective_enabled(rec):
            return False
        if not self._release_channel_enabled(
            rec, developer_session=developer_session, user_mode=user_mode
        ):
            return False
        for dep in rec.dependencies:
            if dep and not self.is_enabled(
                dep,
                user_mode=user_mode,
                developer_session=developer_session,
                show_beta=show_beta,
            ):
                return False
        return visible_for_mode(
            status=rec.status,
            enabled=True,
            feature_modes=rec.modes,
            user_mode=user_mode,
            developer_session=developer_session,
            show_beta=show_beta,
        )

    def is_visible_in_nav(
        self,
        feature_id: str,
        *,
        user_mode: UserMode = "basic",
        developer_session: bool = False,
        show_beta: bool = False,
    ) -> bool:
        rec = self.get(feature_id)
        if not rec:
            return True
        eff = self._effective_enabled(rec)
        if not eff or rec.auto_disabled:
            return developer_session and user_mode == "developer"
        return self.is_enabled(
            feature_id,
            user_mode=user_mode,
            developer_session=developer_session,
            show_beta=show_beta,
        )

    def require(self, feature_id: str, **ctx: Any) -> None:
        if not self.is_enabled(feature_id, **ctx):
            rec = self.get(feature_id)
            reason = rec.auto_disable_reason if rec and rec.auto_disabled else "disabled"
            raise PermissionError(
                f"Feature '{feature_id}' is not available ({reason}). "
                f"Set {rec.env_key if rec else 'FEATURE_*'}=OFF or enable in Developer Panel."
            )

    def auto_disable(self, feature_id: str, *, reason: str) -> None:
        rec = self.get(feature_id)
        if not rec:
            return
        rec.auto_disabled = True
        rec.auto_disable_reason = str(reason)[:500]
        rec.last_error = rec.auto_disable_reason
        self._persist_auto_disable(feature_id, reason=rec.auto_disable_reason)
        from engines.feature_flags.dev_log import get_dev_log

        get_dev_log(self.app_dir).log(
            event="auto_disabled",
            feature_id=feature_id,
            message=reason[:200],
            error=reason[:500],
        )

    def _persist_auto_disable(self, feature_id: str, *, reason: str) -> None:
        local = self._load_json(self._local_path)
        feats = local.setdefault("features", {})
        cur = feats.get(feature_id) or {}
        cur["auto_disabled"] = True
        cur["auto_disable_reason"] = reason
        cur["enabled"] = False
        feats[feature_id] = cur
        self._local_path.parent.mkdir(parents=True, exist_ok=True)
        self._local_path.write_text(
            json.dumps(local, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def set_enabled(self, feature_id: str, enabled: bool) -> FeatureRecord | None:
        rec = self.get(feature_id)
        if not rec:
            return None
        local = self._load_json(self._local_path)
        feats = local.setdefault("features", {})
        cur = feats.get(feature_id) or {}
        cur["enabled"] = bool(enabled)
        cur.pop("auto_disabled", None)
        cur.pop("auto_disable_reason", None)
        feats[feature_id] = cur
        self._local_path.write_text(
            json.dumps(local, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.reload()
        rec = self.get(feature_id)
        if rec:
            rec.auto_disabled = False
            rec.auto_disable_reason = ""
        return rec

    def set_status(self, feature_id: str, status: str) -> FeatureRecord | None:
        st = status.strip().upper()
        if st not in {s.upper() for s in VALID_STATUSES}:
            return None
        local = self._load_json(self._local_path)
        feats = local.setdefault("features", {})
        cur = feats.get(feature_id) or {}
        cur["status"] = st
        feats[feature_id] = cur
        self._local_path.write_text(
            json.dumps(local, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.reload()
        return self.get(feature_id)

    def bootstrap(self) -> dict[str, Any]:
        """Load core features first, then experimental (safe)."""
        from engines.feature_flags.dev_log import get_dev_log

        log = get_dev_log(self.app_dir)
        if self._bootstrapped:
            return {"ok": True, "already": True}
        loaded: list[str] = []
        failed: list[str] = []
        t0 = time.perf_counter()
        for rec in self.all_features():
            if not self._effective_enabled(rec):
                log.log(event="skipped", feature_id=rec.id, message="disabled")
                continue
            if rec.tier == "experimental" and not rec.enabled and self._env_override(rec) is None:
                log.log(event="skipped", feature_id=rec.id, message="experimental off")
                continue
            try:
                if rec.module_path and rec.tier != "core":
                    mp = rec.module_path.replace("/", ".").replace(".py", "")
                    if mp.endswith(".py"):
                        mp = mp[:-3]
                    if not mp.startswith("engines") and not mp.startswith("api"):
                        pass
                    elif mp:
                        __import__(mp.split(".")[0])
                rec.loaded_at_ms = int(time.time() * 1000)
                loaded.append(rec.id)
                log.log(event="boot_ok", feature_id=rec.id, message=rec.module_path or rec.tier)
            except Exception as e:
                failed.append(rec.id)
                self.auto_disable(rec.id, reason=str(e))
        self._bootstrapped = True
        summary = {
            "ok": len(failed) == 0,
            "loaded": loaded,
            "failed": failed,
            "duration_ms": round((time.perf_counter() - t0) * 1000, 2),
        }
        log.log(event="bootstrap_done", message=json.dumps(summary), duration_ms=summary["duration_ms"])
        return summary

    def blueprint_enabled(self, blueprint_name: str) -> bool:
        for rec in self._features.values():
            if rec.blueprint == blueprint_name and self._effective_enabled(rec):
                return True
        if blueprint_name == "platform_api":
            experimental_ids = (
                "live_translation",
                "live_stream",
                "ai_studio",
                "voice_trainer",
                "singing_trainer",
            )
            return any(self._effective_enabled(self.get(i)) for i in experimental_ids if self.get(i))
        if blueprint_name == "modules_api":
            return True
        return False

    def panel_snapshot(
        self,
        *,
        user_mode: UserMode = "developer",
        developer_session: bool = True,
        show_beta: bool = False,
    ) -> dict[str, Any]:
        try:
            from engines.app_version import APP_VERSION
        except Exception:
            APP_VERSION = "unknown"
        mem_mb = None
        try:
            import psutil

            mem_mb = round(psutil.Process().memory_info().rss / 1024 / 1024, 1)
        except Exception:
            pass

        rows = []
        for rec in self.all_features():
            rows.append(
                {
                    **rec.to_dict(),
                    "runtime_enabled": self.is_enabled(
                        rec.id,
                        user_mode=user_mode,
                        developer_session=developer_session,
                        show_beta=show_beta,
                    ),
                }
            )
        return {
            "app_version": APP_VERSION,
            "user_mode": user_mode,
            "developer_session": developer_session,
            "memory_mb": mem_mb,
            "features": rows,
        }


def get_feature_manager(app_dir: Path | None = None) -> FeatureManager:
    base = Path(app_dir or Path(__file__).resolve().parents[2])
    key = str(base.resolve())
    with _LOCK:
        if key not in _MANAGERS:
            _MANAGERS[key] = FeatureManager(base)
        return _MANAGERS[key]


def require_feature(feature_id: str, **ctx: Any) -> None:
    get_feature_manager().require(feature_id, **ctx)
