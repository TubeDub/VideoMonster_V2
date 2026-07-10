#!/usr/bin/env python3
"""Smoke tests for Feature Flags system."""

from __future__ import annotations

import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))


def ok(msg: str) -> None:
    print(f"  OK  {msg}")


def fail(msg: str) -> None:
    print(f" FAIL {msg}")
    sys.exit(1)


def main() -> None:
    print("Feature Flags tests")
    from engines.feature_flags.manager import get_feature_manager
    from engines.feature_flags.modes import normalize_mode, visible_for_mode
    from engines.module_registry.registry import get_registry, module_accessible

    fm = get_feature_manager(APP_DIR)
    reg = get_registry(APP_DIR)

    assert normalize_mode("dev") == "developer"
    assert normalize_mode("pro") == "pro"
    assert normalize_mode("simple") == "basic"
    ok("normalize_mode")

    assert fm.get("core") is not None
    assert fm.get("live_translation") is not None
    ok("registry loaded")

    assert fm.is_enabled("core", user_mode="basic") is True
    assert fm.is_enabled("translation", user_mode="basic") is True
    assert fm.is_enabled("live_translation", user_mode="basic") is False
    assert fm.is_enabled("live_translation", user_mode="developer", developer_session=True) is False
    ok("core ON, live_translation OFF by default")

    rec = reg.get("live_translation")
    assert rec is not None
    assert not module_accessible(
        rec,
        developer_mode=False,
        show_beta=False,
        user_mode="basic",
        app_dir=APP_DIR,
    )
    ok("live_translation route blocked for basic user")

    assert not fm.blueprint_enabled("platform_api")
    ok("platform blueprint disabled when all experimental OFF")

    snap = fm.panel_snapshot(user_mode="developer", developer_session=True)
    assert "features" in snap and len(snap["features"]) >= 10
    ok(f"panel snapshot ({len(snap['features'])} features)")

    assert visible_for_mode(
        status="READY",
        enabled=True,
        feature_modes=["basic", "pro", "developer"],
        user_mode="basic",
        developer_session=False,
    )
    assert not visible_for_mode(
        status="EXPERIMENTAL",
        enabled=True,
        feature_modes=["developer"],
        user_mode="basic",
        developer_session=False,
    )
    ok("visible_for_mode rules")

    from engines.platform.config import live_translation_enabled

    assert live_translation_enabled() is False
    assert not fm.is_enabled("live_stream")
    assert not fm.is_enabled("ai_studio")
    ok("platform experimental modules OFF")

    print("\nAll feature flag tests passed.")


if __name__ == "__main__":
    main()
