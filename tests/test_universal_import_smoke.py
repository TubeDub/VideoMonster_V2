"""Smoke / integration coverage for universal import → dub/studio/voice routes."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from flask import Flask

from api import import_api


@pytest.fixture()
def import_client(tmp_path, monkeypatch):
    imports = tmp_path / "imports"
    imports.mkdir()
    monkeypatch.setattr(import_api, "IMPORT_DIR", imports)
    monkeypatch.setattr(import_api, "APP_DIR", tmp_path)

    app = Flask("import_smoke")
    app.register_blueprint(import_api.bp)
    return app.test_client(), imports


@pytest.mark.parametrize(
    "filename,kind,route,mode",
    [
        ("clip.mp4", "video", "/dub", "dub"),
        ("clip.webm", "video", "/dub", "dub"),
        ("voice.wav", "audio", "/voice", "voice"),
        ("subs.srt", "subtitles", "/studio", "studio"),
        ("note.txt", "text", "/reader", "reader"),
    ],
)
def test_detect_routes_for_modes(filename, kind, route, mode):
    target = import_api.detect_import_target(filename)
    assert target["kind"] == kind
    assert target["route"] == route
    assert target["mode"] == mode


def test_upload_load_delete_video_to_dub(import_client):
    client, imports = import_client
    data = {
        "file": (io.BytesIO(b"fake-mp4-bytes"), "scene.mp4"),
    }
    r = client.post("/api/import/upload", data=data, content_type="multipart/form-data")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["kind"] == "video"
    assert body["route"] == "/dub"
    import_id = body["import_id"]
    assert (imports / f"{import_id}.mp4").is_file()
    assert (imports / f"{import_id}.meta.json").is_file()

    # Querystring contract used by UI: /dub?import=<id>
    qs_route = f"{body['route']}?import={import_id}"
    assert qs_route.startswith("/dub?import=")

    loaded = client.get(f"/api/import/load/{import_id}")
    assert loaded.status_code == 200
    payload = loaded.get_json()
    assert payload["ok"] is True
    assert payload["kind"] == "video"
    assert payload["route"] == "/dub"
    assert payload["path"].replace("\\", "/").endswith(f"uploads/imports/{import_id}.mp4")
    assert payload["upload_filename"] == f"{import_id}.mp4"

    listed = client.get("/api/import/list").get_json()
    assert listed["ok"] is True
    assert any(x["import_id"] == import_id for x in listed["imports"])

    deleted = client.delete(f"/api/import/{import_id}")
    assert deleted.status_code == 200
    assert deleted.get_json()["ok"] is True
    assert not (imports / f"{import_id}.mp4").exists()


def test_upload_load_audio_to_voice(import_client):
    client, _imports = import_client
    r = client.post(
        "/api/import/upload",
        data={"file": (io.BytesIO(b"RIFF....WAVE"), "sample.wav")},
        content_type="multipart/form-data",
    )
    body = r.get_json()
    assert body["route"] == "/voice"
    assert body["kind"] == "audio"
    import_id = body["import_id"]
    loaded = client.get(f"/api/import/load/{import_id}").get_json()
    assert loaded["kind"] == "audio"
    assert loaded["upload_filename"].endswith(".wav")


def test_upload_load_subtitles_to_studio(import_client):
    client, _imports = import_client
    srt = (
        "1\n"
        "00:00:00,000 --> 00:00:01,000\n"
        "Hello world\n"
    ).encode("utf-8")
    r = client.post(
        "/api/import/upload",
        data={"file": (io.BytesIO(srt), "subs.srt")},
        content_type="multipart/form-data",
    )
    body = r.get_json()
    assert body["route"] == "/studio"
    import_id = body["import_id"]
    loaded = client.get(f"/api/import/load/{import_id}").get_json()
    assert loaded["kind"] == "subtitles"
    assert loaded["route"] == "/studio"
    assert "Hello world" in (loaded.get("text") or "")
    assert isinstance(loaded.get("segments"), list)


def test_meta_not_preferred_over_media(import_client):
    client, imports = import_client
    iid = "abcd1234ef00"
    (imports / f"{iid}.mp4").write_bytes(b"video")
    (imports / f"{iid}.meta.json").write_text(
        json.dumps({"original": "clip.mp4", "kind": "video", "route": "/dub", "mode": "dub"}),
        encoding="utf-8",
    )
    loaded = client.get(f"/api/import/load/{iid}").get_json()
    assert loaded["ok"] is True
    assert loaded["upload_filename"] == f"{iid}.mp4"


def test_reject_unknown_and_bad_id(import_client):
    client, _imports = import_client
    r = client.post(
        "/api/import/upload",
        data={"file": (io.BytesIO(b"x"), "evil.exe")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 400
    bad = client.get("/api/import/load/../etc/passwd")
    assert bad.status_code == 400
