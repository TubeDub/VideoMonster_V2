"""P803 Feature Flags — new capabilities only via flags (never direct prod)."""

from __future__ import annotations

from typing import Any


# Enterprise-controlled capability flags (default OFF for future work)
ENTERPRISE_FLAGS: dict[str, bool] = {
    "Semantic V4": False,
    "Distributed Pipeline": False,
    "Cloud Hybrid": False,
    "Team Collaboration": False,
    "Lip Sync Renderer": False,
    "Enterprise SSO": False,
}


def is_feature_enabled(name: str, *, default: bool = False) -> bool:
    """Resolve via FeatureManager when available, else ENTERPRISE_FLAGS."""
    try:
        from engines.feature_flags.manager import get_feature_manager

        fm = get_feature_manager()
        # Map display name to id-ish key
        key = name.lower().replace(" ", "_")
        if hasattr(fm, "is_enabled"):
            return bool(fm.is_enabled(key, default=default))
        if hasattr(fm, "get"):
            rec = fm.get(key)
            if rec is not None:
                return bool(getattr(rec, "enabled", default))
    except Exception:
        pass
    try:
        from engines.core.feature_flags import is_enabled

        return bool(is_enabled(name.lower().replace(" ", "_")))
    except Exception:
        return bool(ENTERPRISE_FLAGS.get(name, default))


def require_feature_flag(name: str) -> None:
    """P803 — forbid shipping new capability without flag gate."""
    if not is_feature_enabled(name, default=False) and name in ENTERPRISE_FLAGS:
        if ENTERPRISE_FLAGS[name] is False:
            raise RuntimeError(
                f"Feature '{name}' is gated OFF. Enable via Feature Flag before use."
            )


def list_enterprise_flags() -> dict[str, Any]:
    out = {}
    for name, default in ENTERPRISE_FLAGS.items():
        out[name] = {
            "enabled": is_feature_enabled(name, default=default),
            "default": default,
            "rule": "new capabilities must ship behind Feature Flag",
        }
    return out


def assert_no_ungated_production_feature(name: str, *, production: bool = True) -> None:
    if production and name in ENTERPRISE_FLAGS and not is_feature_enabled(name):
        raise RuntimeError(f"Cannot enable {name} directly in Production without Feature Flag")
