"""Master Spec Part 8 — Platform SDK / Plugin / Cloud / Ecosystem tests."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_core_protected_and_bootstrap():
    from engines.platform_sdk import bootstrap_platform
    from engines.platform_sdk.security import assert_core_protected

    assert_core_protected()
    status = bootstrap_platform(discover_builtin=False)
    assert status["version"] == "8.0.0"
    assert status["core_protected"] is True
    assert "TTS" in status["extension_points"]


def test_lifecycle_transitions():
    from engines.platform_sdk.lifecycle import LifecycleError, can_transition, transition
    from engines.platform_sdk.types import PluginLifecycle

    assert can_transition(PluginLifecycle.INSTALLED, PluginLifecycle.VERIFIED)
    assert transition(PluginLifecycle.RUNNING, PluginLifecycle.PAUSED) == PluginLifecycle.PAUSED
    with pytest.raises(LifecycleError):
        transition(PluginLifecycle.INSTALLED, PluginLifecycle.RUNNING)


def test_vmplugin_package_signed(tmp_path: Path):
    from engines.platform_sdk.package import build_vmplugin, read_vmplugin
    from engines.platform_sdk.types import PluginDescriptor, TrustLevel

    desc = PluginDescriptor(
        plugin_id="demo-exporter",
        version="1.2.0",
        author="VM",
        description="Test exporter",
        permissions=["Export", "Read Project"],
        capabilities=["export"],
        extension_points=["Export"],
        min_core_version="6.0.0",
        max_core_version="99.0.0",
    )
    secret = "test-secret-key"
    pkg = build_vmplugin(tmp_path / "demo.vmplugin", descriptor=desc, secret=secret)
    info = read_vmplugin(pkg, secret=secret)
    assert info["validation"]["ok"] is True
    assert info["trust"] == TrustLevel.VERIFIED.value


def test_plugin_manager_lifecycle(tmp_path: Path):
    from engines.platform_sdk.manager import PlatformPluginManager
    from engines.platform_sdk.package import build_vmplugin
    from engines.platform_sdk.types import PluginDescriptor, PluginLifecycle

    mgr = PlatformPluginManager(install_dir=tmp_path / "installed", core_version="6.0.0")
    desc = PluginDescriptor(
        plugin_id="hello-tts",
        version="0.1.0",
        permissions=["Generate Audio", "Read Project"],
        capabilities=["tts"],
        extension_points=["TTS"],
    )
    secret = "sec"
    pkg = build_vmplugin(tmp_path / "hello.vmplugin", descriptor=desc, secret=secret)
    rec = mgr.install_from_vmplugin(pkg, secret=secret)
    assert rec["lifecycle"] == PluginLifecycle.VERIFIED.value
    mgr.start("hello-tts")
    assert mgr.get("hello-tts")["lifecycle"] == PluginLifecycle.RUNNING.value
    mgr.pause("hello-tts")
    mgr.stop("hello-tts")
    mgr.remove("hello-tts")
    assert mgr.get("hello-tts") is None


def test_sandbox_permissions():
    from engines.platform_sdk.security import PluginSandbox, SandboxViolation
    from engines.platform_sdk.types import Permission

    sb = PluginSandbox("p1", {Permission.READ_PROJECT.value})
    sb.require(Permission.READ_PROJECT)
    with pytest.raises(SandboxViolation):
        sb.require(Permission.INTERNET)
    with pytest.raises(SandboxViolation):
        sb.assert_not_core_path("engines/dub_engine_v2/engine.py")


def test_event_bus_and_webhooks(tmp_path: Path):
    from engines.platform_sdk.event_bus import PlatformEventBus
    from engines.platform_sdk.types import PlatformEvent
    from engines.platform_sdk.webhooks import WebhookRegistry

    bus = PlatformEventBus()
    seen = []

    def listener(event, record):
        seen.append(event)

    bus.subscribe(PlatformEvent.EXPORT_FINISHED, listener)
    bus.publish(PlatformEvent.EXPORT_FINISHED, {"project": "x"})
    assert seen == [PlatformEvent.EXPORT_FINISHED.value]

    hooks = WebhookRegistry(path=tmp_path / "hooks.json")
    hooks.register("http://127.0.0.1:9/hook", events=[PlatformEvent.EXPORT_FINISHED.value], secret="s")
    results = hooks.dispatch(PlatformEvent.EXPORT_FINISHED.value, {"ok": True}, dry_run=True)
    assert results and results[0]["dry_run"] is True
    assert results[0]["signature"]


def test_cloud_backup_rollback(tmp_path: Path):
    from engines.platform_sdk.cloud import CloudFacade

    cloud = CloudFacade(root=tmp_path / "cloud")
    cloud.save_project("p1", {"v": 1})
    cloud.backup_project("p1")
    cloud.save_project("p1", {"v": 2})
    stamps = cloud.list_backups("p1")
    assert stamps
    cloud.rollback("p1", stamps[0]["stamp"])
    assert cloud.open_project("p1")["v"] == 1


def test_team_tokens_profiles_marketplace(tmp_path: Path):
    from engines.platform_sdk.marketplace import MarketplaceCatalog
    from engines.platform_sdk.settings_profiles import get_profile, list_profiles, save_profile
    from engines.platform_sdk.team import TeamService
    from engines.platform_sdk.tokens import TokenStore
    from engines.platform_sdk.types import MarketplaceKind, PluginDescriptor, TeamRole

    team = TeamService(store=tmp_path / "team.json")
    team.assign_role("proj", "u1", TeamRole.TRANSLATOR)
    assert team.can("proj", "u1", "write_translation")
    assert not team.can("proj", "u1", "export")

    tokens = TokenStore(path=tmp_path / "tokens.json")
    raw = tokens.issue("ci", scopes=["read", "export"])
    assert tokens.verify("ci", raw)
    assert not tokens.verify("ci", "wrong")
    tokens.revoke("ci")
    assert not tokens.verify("ci", raw)

    profiles = list_profiles()
    assert "Movie" in profiles and "Anime" in profiles
    save_profile("CustomTest", {"style": "Movie", "tempo": 1.0})
    # get_profile reads from data/ — just ensure defaults work
    assert get_profile("Documentary")["style"] == "Documentary"

    market = MarketplaceCatalog(root=tmp_path / "market")
    desc = PluginDescriptor(
        plugin_id="voice-pack-a",
        version="1.0.0",
        permissions=["Read Project"],
        description="voices",
    )
    entry = market.publish(desc, kind=MarketplaceKind.VOICE_PACK)
    assert entry["kind"] == MarketplaceKind.VOICE_PACK.value
    assert market.list_items(kind=MarketplaceKind.VOICE_PACK.value)


def test_public_api_extension_points():
    from engines.platform_sdk import get_public_api
    from engines.platform_sdk.public_api import invoke_extensions, register_extension
    from engines.platform_sdk.types import ExtensionPoint

    api = get_public_api()
    assert "Scheduler" in api.list_extension_points()

    register_extension(
        ExtensionPoint.DIAGNOSTICS,
        plugin_id="diag-test",
        handler=lambda: {"ok": True},
    )
    results = invoke_extensions(ExtensionPoint.DIAGNOSTICS)
    assert any(r == {"ok": True} for r in results)


def test_script_engine_stub():
    from engines.platform_sdk.script_engine import ScriptEngine, ScriptJob

    eng = ScriptEngine()
    assert "python" in eng.supported()
    out = eng.run(ScriptJob(language="python", source="print(1)"))
    assert out["ok"] is False
    assert out["error"] == "runtime_not_installed"
