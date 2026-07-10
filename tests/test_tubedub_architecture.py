"""Architecture layer tests."""

from __future__ import annotations

from pathlib import Path


def test_module_lifecycle():
    from engines.tubedub.adapters.base import CoreModule
    from engines.tubedub.lifecycle import ModuleContext, ModuleLifecycleState

    mod = CoreModule()
    mod.initialize(ModuleContext(app_dir=".", module_id="core", api_namespace="core"))
    assert mod.state == ModuleLifecycleState.INITIALIZED
    mod.load()
    assert mod.state == ModuleLifecycleState.LOADED
    out = mod.run({})
    assert out["module_id"] == "core"
    h = mod.health_check()
    assert h.ok
    mod.stop()
    mod.dispose()


def test_api_bus():
    from engines.tubedub.api_bus import ApiBus

    bus = ApiBus()
    bus.register("test", "echo", lambda p: {"echo": p.get("x")}, module_id="test")
    resp = bus.call("test", "echo", {"x": 42})
    assert resp.ok
    assert resp.result["echo"] == 42


def test_tdproj_roundtrip(tmp_path):
    from engines.tubedub.project.model import TdProject
    from engines.tubedub.project.store import TdProjectStore

    store = TdProjectStore(tmp_path)
    p = store.create_empty(title="Test Project")
    loaded = store.load(p.project_id)
    assert loaded is not None
    assert loaded.title == "Test Project"
    assert loaded.format == "tubedub-project"


def test_platform_bootstrap(tmp_path):
    import shutil

    app = tmp_path / "app"
    shutil.copytree(Path(__file__).resolve().parents[1] / "data", app / "data")
    from engines.tubedub.bootstrap import bootstrap_platform

    result = bootstrap_platform(app)
    assert result["ok"]
    assert result["modules_loaded"] >= 5


def test_release_channel():
    from engines.tubedub.release import ReleaseChannel, channel_visible

    assert not channel_visible(ReleaseChannel.DISABLED, developer_session=True, user_mode="developer")
    assert channel_visible(ReleaseChannel.DEVELOPER, developer_session=True, user_mode="developer")
    assert channel_visible(ReleaseChannel.RELEASE, developer_session=False, user_mode="basic")


def test_plugin_host():
    from engines.tubedub.plugin_host import PluginHost, PluginRecord, PluginKind

    host = PluginHost()
    host.register(
        PluginRecord(plugin_id="demo", label="Demo", kind=PluginKind.UTILITY.value),
        processor=lambda p: {"ok": True},
    )
    assert host.invoke("demo", payload={})["ok"]


def test_pipeline_stage_plugins():
    from engines.tubedub.pipeline.plugins import register_pipeline_stage_plugins
    from engines.tubedub.plugin_host import PluginKind, get_plugin_host

    n = register_pipeline_stage_plugins()
    assert n == 9
    plugins = get_plugin_host().list_plugins(kind=PluginKind.PIPELINE_STAGE.value)
    assert len(plugins) >= 9
    assert any(p["plugin_id"] == "pipeline.stt" for p in plugins)


def test_unified_pipeline_bus(tmp_path):
    import shutil

    app = tmp_path / "app"
    shutil.copytree(Path(__file__).resolve().parents[1] / "data", app / "data")
    from engines.tubedub.bootstrap import bootstrap_platform
    from engines.tubedub.pipeline import run_unified_pipeline

    bootstrap_platform(app)
    out = run_unified_pipeline(
        {"segments_data": [{"text": "Hi"}], "translation_audits": [{"index": 0, "final_text": "Hi"}]},
        app_dir=str(app),
    )
    assert out.get("ok")


def test_architecture_dashboard(tmp_path):
    import shutil

    app = tmp_path / "app"
    shutil.copytree(Path(__file__).resolve().parents[1] / "data", app / "data")
    from engines.tubedub.dev_mode.dashboard import build_architecture_dashboard

    dash = build_architecture_dashboard(app, developer_session=True)
    assert dash["pipeline"]["stages"]
    assert dash["modules"]["modules"]
    assert dash["copy_text"]
