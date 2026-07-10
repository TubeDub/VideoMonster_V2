#!/usr/bin/env python3
"""Tests for module registry / release channels."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_registry_defaults() -> None:
    from engines.module_registry.registry import get_registry, module_visible_to_user

    reg = get_registry(ROOT)
    dub = reg.get("dub")
    assert dub is not None
    assert dub.status == "stable"
    live = reg.get("live_translation")
    assert live is not None
    assert live.status == "development"
    assert not module_visible_to_user(live, show_beta=False)


def test_nav_production_vs_dev() -> None:
    from engines.module_registry.registry import get_registry

    reg = get_registry(ROOT)
    prod = reg.nav_modules(developer_mode=False, lang="ru")
    prod_ids = {m["id"] for m in prod}
    assert "dub" in prod_ids
    assert "live_translation" not in prod_ids

    dev = reg.nav_modules(developer_mode=True, lang="ru", user_mode="developer")
    dev_ids = {m["id"] for m in dev}
    assert "live_translation" in dev_ids
    assert "module_manager" in dev_ids


def test_local_override_persist() -> None:
    from engines.module_registry.registry import ModuleRegistry

    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        (base / "data").mkdir()
        default = ROOT / "data" / "module_registry.json"
        (base / "data" / "module_registry.json").write_text(
            default.read_text(encoding="utf-8"), encoding="utf-8"
        )
        reg = ModuleRegistry(base)
        reg.update_module("live_translation", {"status": "beta", "visible_to_users": True})
        local = base / "data" / "module_registry.local.json"
        assert local.is_file()
        reg2 = ModuleRegistry(base)
        rec = reg2.get("live_translation")
        assert rec.status == "beta"
        assert rec.visible_to_users is True


def test_module_accessible_beta() -> None:
    from engines.module_registry.registry import ModuleRecord, module_accessible

    rec = ModuleRecord(
        id="x",
        name={"ru": "X"},
        status="beta",
        visible_to_users=True,
    )
    assert not module_accessible(rec, developer_mode=False, show_beta=False)
    assert not module_accessible(rec, developer_mode=False, show_beta=True)


def test_developer_session_client_cookie() -> None:
    from unittest.mock import patch

    from engines.module_registry.registry import is_developer_session

    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("VM_DEV_MODE", None)
        os.environ.pop("VM_DEVELOPER_MODE", None)
        with patch("engines.owner_first_run.is_owner_host", return_value=True):
            assert not is_developer_session(request_cookies={"vm_client_dev_mode": "0"})
            assert is_developer_session(request_cookies={"vm_client_dev_mode": "1"})
            assert is_developer_session(request_headers={"X-VM-Client-Dev-Mode": "1"})


def main() -> None:
    for fn in (
        test_registry_defaults,
        test_nav_production_vs_dev,
        test_local_override_persist,
        test_module_accessible_beta,
        test_developer_session_client_cookie,
    ):
        fn()
        print("OK", fn.__name__)
    print("All module registry tests passed.")


if __name__ == "__main__":
    main()
