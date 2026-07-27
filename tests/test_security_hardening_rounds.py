"""Security regression: storage auth, path allowlists, OAuth gates."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from flask import Flask


def test_require_local_mutating_blocks_non_loopback():
    from engines.request_guards import require_local_mutating

    app = Flask("sec_test")
    with app.app_context():
        req = MagicMock()
        req.remote_addr = "192.168.1.50"
        denied = require_local_mutating(req, action="create")
        assert denied is not None
        body, code = denied
        assert code == 403
        assert body.get_json()["error"] == "localhost_only"


def test_require_local_mutating_allows_loopback():
    from engines.request_guards import require_local_mutating

    req = MagicMock()
    req.remote_addr = "127.0.0.1"
    assert require_local_mutating(req) is None


def test_storage_create_requires_localhost(monkeypatch):
    from engines import request_guards as rg
    import api.storage_api as storage_api

    monkeypatch.setattr(rg, "is_local_request", lambda _req: False)
    app = Flask("storage_sec")
    app.register_blueprint(storage_api.bp)
    with app.test_client() as client:
        r = client.post("/api/storage/projects", json={"title": "x"})
        assert r.status_code == 403
        assert r.get_json()["error"] == "localhost_only"


def test_storage_cleanup_still_needs_confirm_header():
    import api.storage_api as storage_api

    app = Flask("storage_sec2")
    app.register_blueprint(storage_api.bp)
    with app.test_client() as client:
        r = client.post("/api/storage/cleanup", json={"confirm": True, "scope": "temp"})
        assert r.status_code == 403
        assert r.get_json()["error"] == "confirm_header_required"


def test_path_safety_rejects_escape(tmp_path):
    from engines.path_safety import resolve_under_roots

    root = tmp_path / "uploads"
    root.mkdir()
    (root / "ok.txt").write_text("x", encoding="utf-8")
    outside = tmp_path / "secret.txt"
    outside.write_text("nope", encoding="utf-8")
    assert resolve_under_roots(str(outside), [root]) is None
    assert resolve_under_roots("ok.txt", [root]) is not None


def test_get_output_path_blocks_traversal(tmp_path, monkeypatch):
    import engines.tts as tts_mod

    out = tmp_path / "output"
    out.mkdir()
    (out / "a.mp3").write_bytes(b"x")
    monkeypatch.setattr(tts_mod, "OUTPUT_DIR", out)
    assert tts_mod.get_output_path("a.mp3") is not None
    # Basename-only: Path("../../etc/passwd").name == "passwd" → miss
    assert tts_mod.get_output_path("../../etc/passwd") is None


def test_oauth_meta_never_fake_connected_without_token(tmp_path, monkeypatch):
    monkeypatch.delenv("VM_GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("VM_GOOGLE_CLIENT_SECRET", raising=False)
    from engines.cloud.oauth import oauth_meta_for_provider

    meta = oauth_meta_for_provider("google_drive", app_dir=tmp_path)
    assert meta.get("oauth_connected") is False
    assert meta.get("oauth_remote_gated") is True or meta.get("oauth_configured") is False


def test_oauth_authorize_hard_gates_without_env(tmp_path):
    from engines.cloud.oauth import build_authorize_url

    result = build_authorize_url("dropbox", app_dir=tmp_path)
    assert result["ok"] is False
    assert result["error"] == "oauth_credentials_missing"


def test_remote_jobs_reject_outside_media(tmp_path):
    from engines.cloud.remote_jobs import RemoteJobQueue

    q = RemoteJobQueue(tmp_path)
    (tmp_path / "uploads").mkdir()
    outside = tmp_path.parent / "outside.wav"
    outside.write_bytes(b"RIFF")
    with pytest.raises(FileNotFoundError):
        q._safe_media_path(str(outside))


def test_agent_report_path_rejects_outside(tmp_path, monkeypatch):
    import api._agent_report_helpers as helpers

    monkeypatch.setattr(helpers, "_APP_DIR", tmp_path)
    monkeypatch.setattr(helpers, "_MANIFESTS_DIR", tmp_path / "output" / "manifests")
    (tmp_path / "output" / "manifests").mkdir(parents=True)
    evil = tmp_path.parent / "evil.json"
    evil.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        helpers,
        "task_info",
        lambda _tid: {"director_report_path": str(evil)},
    )
    assert (
        helpers.resolve_report_path("t1", info_key="director_report_path", filename="director_report.json")
        is None
    )


def test_director_validate_payload_cap():
    import api.director_api as director_api

    app = Flask("dir_sec")
    app.register_blueprint(director_api.bp)
    with app.test_client() as client:
        r = client.post(
            "/api/director/validate",
            json={
                "source_segments": ["x"] * 5001,
                "translated_segments": ["y"],
                "timing_map": [],
            },
        )
        assert r.status_code == 400
        assert r.get_json()["error"] == "payload_too_large"


def test_live_local_source_allowlist(monkeypatch):
    import api.platform_api as plat

    monkeypatch.setattr(plat, "_guard", lambda _m: None)
    app = Flask("live_sec")
    app.register_blueprint(plat.bp)
    with app.test_client() as client:
        r = client.post(
            "/api/platform/live/start",
            json={"path": "C:/Windows/System32/drivers/etc/hosts", "require_engines": False},
        )
        assert r.status_code == 400
        assert r.get_json()["error"] == "local_source_outside_allowlist"


def test_openddf_dest_clamped(tmp_path, monkeypatch):
    """Export dest must stay under output (path allowlist)."""
    from engines.path_safety import clamp_write_path

    out = tmp_path / "output"
    out.mkdir()
    target = clamp_write_path("../escape.html", out, default_name="report.html")
    assert target.parent == out.resolve() or out.resolve() in target.parents
    assert "escape" in target.name
