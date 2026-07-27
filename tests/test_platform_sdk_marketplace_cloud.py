"""Platform SDK marketplace + local cloud façade HTTP smoke."""

from __future__ import annotations

from flask import Flask


def test_platform_sdk_marketplace_and_cloud_local(tmp_path, monkeypatch):
    import api.platform_sdk_api as sdk_api
    from engines.platform_sdk import marketplace as market_mod

    market_root = tmp_path / "marketplace"
    monkeypatch.setattr(market_mod, "ROOT", tmp_path)
    market_mod._MARKET = None  # type: ignore[attr-defined]

    # Curated plugins catalog sibling file
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "plugin_marketplace_catalog.json").write_text(
        '{"version":1,"plugins":[{"id":"demo","name":"demo","version":"1.0.0"}]}',
        encoding="utf-8",
    )

    from engines.platform_sdk.marketplace import MarketplaceCatalog

    cat = MarketplaceCatalog(root=market_root)
    market_mod._MARKET = cat  # type: ignore[attr-defined]

    app = Flask("sdk_smoke")
    app.register_blueprint(sdk_api.bp)
    client = app.test_client()

    r = client.get("/api/platform_sdk/marketplace")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["store"] == "local"
    assert "kinds" in body
    assert body["plugins_catalog"]["configured"] is True
    assert any(p.get("id") == "demo" for p in body["plugins_catalog"]["plugins"])

    kinds = client.get("/api/platform_sdk/marketplace/kinds").get_json()
    assert kinds["ok"] is True
    assert "TTS" in kinds["kinds"]

    pub = client.post(
        "/api/platform_sdk/marketplace/publish",
        json={
            "plugin_id": "pack-local-1",
            "version": "0.1.0",
            "kind": "Voice Packs",
            "description": "test",
        },
    )
    assert pub.status_code == 200
    assert pub.get_json()["ok"] is True

    # Local cloud façade
    from engines.platform_sdk import cloud as cloud_mod

    cloud_mod._CLOUD = None  # type: ignore[attr-defined]
    cloud_mod.CLOUD_DIR = tmp_path / "platform_cloud"
    status = client.get("/api/platform_sdk/cloud/status").get_json()
    assert status["ok"] is True
    assert status["mode"] == "local_mirror"

    save = client.post(
        "/api/platform_sdk/cloud/projects/p1",
        json={"title": "Local", "v": 1},
    )
    assert save.status_code == 200
    opened = client.get("/api/platform_sdk/cloud/projects/p1").get_json()
    assert opened["ok"] is True
    assert opened["project"]["v"] == 1
    backup = client.post("/api/platform_sdk/cloud/projects/p1/backup").get_json()
    assert backup["ok"] is True
