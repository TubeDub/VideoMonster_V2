"""Tests for Developer SDK + Plugin System (TZ #9)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.plugin_api import (
    CORE_API_VERSION,
    PluginManifest,
    PluginPermissions,
    version_compatible,
)
from core.plugin_manager import PluginManager, plugins_enabled, reset_plugin_manager
from sdk.base import BasePlugin
from sdk.core_api import list_registrations, register_translation


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("VM_PLUGINS_DIR", str(tmp_path))
    monkeypatch.setenv("VM_PLUGINS", "1")
    reset_plugin_manager()
    yield
    reset_plugin_manager()


def _write_plugin(base: Path, name: str, *, caps=None, api="1.0.0", deps=None, code: str = ""):
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": name,
        "version": "1.0.0",
        "minimum_api": api,
        "capabilities": caps or ["utility"],
        "dependencies": deps or [],
        "permissions": {"file": False, "network": False, "gpu": False,
                        "audio": False, "video": False, "memory": False},
        "entry_point": "plugin.py",
    }
    (d / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    default_code = f'''
from sdk.base import BasePlugin
class Plugin(BasePlugin):
    PLUGIN_NAME = "{name}"
    PLUGIN_VERSION = "1.0.0"
    PLUGIN_CAPABILITIES = {caps or ["utility"]}
def create_plugin():
    return Plugin()
'''
    (d / "plugin.py").write_text(code or default_code, encoding="utf-8")
    return d


# ── API & versioning (§12, §16) ──────────────────────────────────────


def test_version_compatible():
    assert version_compatible("1.0.0") is True
    assert version_compatible("2.0.0") is False
    assert version_compatible("1.0.0", "1.1.0") is True


def test_manifest_from_json(tmp_path):
    p = tmp_path / "plugin.json"
    p.write_text(json.dumps({
        "name": "test", "version": "2.0.0", "capabilities": ["tts"],
        "permissions": {"network": True},
    }), encoding="utf-8")
    m = PluginManifest.from_json(p)
    assert m.name == "test"
    assert m.capabilities == ["tts"]
    assert m.permissions.network is True


# ── Discovery & load (§1, §7) ────────────────────────────────────────


def test_discover_and_load(tmp_path):
    _write_plugin(tmp_path, "alpha")
    mgr = PluginManager(app_dir=tmp_path)
    found = mgr.discover(auto_load=True)
    assert "alpha" in found
    assert mgr.get_plugin("alpha")["state"] == "enabled"


def test_incompatible_api_disabled(tmp_path):
    _write_plugin(tmp_path, "old", api="99.0.0")
    mgr = PluginManager(app_dir=tmp_path)
    mgr.discover(auto_load=True)
    rec = mgr.get_plugin("old")
    assert rec["state"] == "incompatible"


def test_sandbox_failed_plugin_does_not_crash(tmp_path):
    bad_code = '''
class Plugin:
    def initialize(self, ctx): raise RuntimeError("boom")
    def shutdown(self): pass
    def health(self): return {"ok": False}
    def capabilities(self): return []
    def version(self): return "1.0.0"
    def dependencies(self): return []
def create_plugin():
    return Plugin()
'''
    _write_plugin(tmp_path, "bad", code=bad_code)
    mgr = PluginManager(app_dir=tmp_path)
    mgr.discover(auto_load=True)
    assert mgr.get_plugin("bad")["state"] == "failed"
    # Other plugins still work
    _write_plugin(tmp_path, "good")
    mgr.discover(auto_load=False)
    assert mgr.load("good") is True


def test_disable_and_reload(tmp_path):
    _write_plugin(tmp_path, "hot")
    mgr = PluginManager(app_dir=tmp_path)
    mgr.discover(auto_load=True)
    assert mgr.disable("hot") is True
    assert mgr.get_plugin("hot")["state"] == "disabled"
    assert mgr.reload("hot") is True
    assert mgr.get_plugin("hot")["state"] == "enabled"


# ── Dependencies (§6) ────────────────────────────────────────────────


def test_missing_dependency_fails(tmp_path):
    _write_plugin(tmp_path, "child", deps=["parent"])
    mgr = PluginManager(app_dir=tmp_path)
    mgr.discover(auto_load=True)
    assert mgr.get_plugin("child")["state"] == "failed"


# ── Capabilities (§5) ────────────────────────────────────────────────


def test_capability_index(tmp_path):
    _write_plugin(tmp_path, "tts1", caps=["tts"])
    _write_plugin(tmp_path, "tr1", caps=["translation"])
    mgr = PluginManager(app_dir=tmp_path)
    mgr.discover(auto_load=True)
    assert "tts1" in mgr.plugins_for_capability("tts")
    assert "tr1" in mgr.plugins_for_capability("translation")


# ── Permissions (§13) ────────────────────────────────────────────────


def test_permissions_override(tmp_path):
    _write_plugin(tmp_path, "net", caps=["utility"])
    # Patch manifest permissions
    mf = json.loads((tmp_path / "net" / "plugin.json").read_text())
    mf["permissions"] = {"network": True}
    (tmp_path / "net" / "plugin.json").write_text(json.dumps(mf))
    mgr = PluginManager(app_dir=tmp_path)
    mgr.set_permissions("net", {"network": False})
    mgr.discover(auto_load=True)
    rec = mgr.get_plugin("net")
    assert rec["state"] in ("disabled", "failed")


# ── SDK (§10–§11) ───────────────────────────────────────────────────


def test_sdk_base_plugin():
    class P(BasePlugin):
        PLUGIN_NAME = "test"
        PLUGIN_CAPABILITIES = ["utility"]
    p = P()
    p.initialize({"app_dir": "/tmp"})
    assert p.health().ok is True
    assert p.capabilities() == ["utility"]
    p.shutdown()


def test_sdk_register_translation(tmp_path):
    _write_plugin(tmp_path, "demo")
    mgr = PluginManager(app_dir=tmp_path)
    mgr.discover(auto_load=True)
    regs = list_registrations("translation")
    assert isinstance(regs, dict)


# ── Marketplace stub (§9) ────────────────────────────────────────────


def test_marketplace_enable_disable(tmp_path):
    _write_plugin(tmp_path, "mp")
    mgr = PluginManager(app_dir=tmp_path)
    mgr.discover(auto_load=False)
    assert mgr.marketplace.enable("mp")["ok"] is True
    assert mgr.marketplace.disable("mp")["ok"] is True


def test_diagnostics(tmp_path):
    _write_plugin(tmp_path, "diag")
    mgr = PluginManager(app_dir=tmp_path)
    mgr.discover(auto_load=True)
    diag = mgr.get_diagnostics()
    assert len(diag) >= 1
    assert diag[0]["name"] == "diag"


def test_flags():
    import os
    os.environ["VM_PLUGINS"] = "1"
    assert plugins_enabled() is True
