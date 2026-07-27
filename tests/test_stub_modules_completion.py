"""Tests for production completion of previously stubbed modules."""

from __future__ import annotations

import io
import wave
from pathlib import Path

import pytest


def test_coming_soon_redirects_exist():
    from app import _SOON_REDIRECTS, app

    client = app.test_client()
    for module_id, target in _SOON_REDIRECTS.items():
        r = client.get(f"/soon/{module_id}", follow_redirects=False)
        assert r.status_code in (301, 302), module_id
        assert target in (r.headers.get("Location") or ""), module_id


def test_director_page_and_validate():
    from app import app

    client = app.test_client()
    r = client.get("/director")
    assert r.status_code == 200
    assert b"AI Director" in r.data

    r = client.post(
        "/api/director/validate",
        json={
            "source_segments": ["Hello world"],
            "translated_segments": ["Привіт світ"],
            "timing_map": [{"start": 0, "end": 1500}],
        },
    )
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] is True
    assert "quality" in j


def test_recording_punch_in_out(tmp_path, monkeypatch):
    import api.recording_api as rec

    monkeypatch.setattr(rec, "RECORDINGS_DIR", tmp_path)
    monkeypatch.setattr(rec, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(rec, "_allowed", lambda: True)

    from flask import Flask

    app = Flask("rec_test")
    app.register_blueprint(rec.bp)

    with app.test_client() as client:
        r = client.post("/api/recording/punch-in", json={})
        data = r.get_json()
        assert data["ok"] is True
        sid = data["session"]["session_id"]
        r2 = client.post("/api/recording/punch-out", json={"session_id": sid})
        data2 = r2.get_json()
        assert data2["ok"] is True
        assert data2["session"]["status"] in ("stopped", "done", "completed") or data2.get("path")


def test_recording_mic_upload_punch_out(tmp_path, monkeypatch):
    """End-to-end backend path used by browser getUserMedia → MediaRecorder upload."""
    import io
    import wave

    import api.recording_api as rec

    monkeypatch.setattr(rec, "RECORDINGS_DIR", tmp_path)
    monkeypatch.setattr(rec, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(rec, "_allowed", lambda: True)

    from flask import Flask

    app = Flask("rec_mic_test")
    app.register_blueprint(rec.bp)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 1600)
    audio = buf.getvalue()

    with app.test_client() as client:
        r = client.post("/api/recording/punch-in", json={"source": "browser_mic"})
        data = r.get_json()
        assert data["ok"] is True
        sid = data["session"]["session_id"]
        r2 = client.post(
            "/api/recording/punch-out",
            data={
                "session_id": sid,
                "apply_fx": "0",
                "file": (io.BytesIO(audio), "mic_punch.wav"),
            },
            content_type="multipart/form-data",
        )
        data2 = r2.get_json()
        assert data2["ok"] is True
        assert data2["session"]["has_audio"] is True
        assert data2["readiness"] == "GREEN"


def test_cloud_mirror_providers(tmp_path):
    from engines.cloud.providers.stubs import GoogleDriveProvider, DropboxProvider

    p = GoogleDriveProvider(tmp_path, {})
    st = p.connect()
    assert st.connected is True
    src = tmp_path / "a.txt"
    src.write_text("hello", encoding="utf-8")
    entry = p.upload_file(src, "docs/a.txt")
    assert entry.path == "docs/a.txt"
    files = p.list_files()
    assert any(f.path == "docs/a.txt" for f in files)
    dest = tmp_path / "out.txt"
    p.download_file("docs/a.txt", dest)
    assert dest.read_text(encoding="utf-8") == "hello"

    d = DropboxProvider(tmp_path, {})
    assert d.connect().connected is True


def test_lip_sync_and_voice_clone_engines(tmp_path):
    from engines.streamdub.modules.lip_sync import LipSyncEngine
    from engines.streamdub.modules.voice_clone import VoiceCloneEngine

    # Tiny silent wav
    wav = tmp_path / "ref.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 1600)

    lip = LipSyncEngine()
    lip.initialize(app_dir=tmp_path)
    health = lip.health_check()
    assert hasattr(health, "ok")

    out = lip.process({"audio_path": str(wav), "video_path": str(wav)})
    assert "lip_sync" in out
    assert out["lip_sync"]["status"] == "ok"

    vc = VoiceCloneEngine()
    vc.initialize(app_dir=tmp_path)
    health2 = vc.health_check()
    assert health2.ok is True
    out2 = vc.process({"reference_audio": str(wav), "text": "hi"})
    assert out2["voice_clone"]["status"] == "registered"


def test_vst_ffmpeg_host_scan():
    from engines.plugins.vst_host import get_vst_host

    host = get_vst_host()
    plugins = host.scan_plugins()
    assert any(p.format == "ffmpeg" for p in plugins)
    handle = host.load_plugin("ffmpeg://ffmpeg_loudnorm")
    assert handle


def test_adapters_registered():
    from engines.tubedub.adapters.base import ADAPTER_MAP, create_adapter

    for key in (
        "enterprise_translation",
        "word_timing",
        "professional_dubbing",
        "developer_tools",
        "cloud_platform",
        "live_translation",
    ):
        assert key in ADAPTER_MAP
        mod = create_adapter(key, key)
        assert mod is not None
        assert mod.module_id == key


def test_module_registry_no_coming_soon_user_shells():
    import json
    from pathlib import Path

    data = json.loads(
        (Path(__file__).resolve().parents[1] / "data" / "module_registry.json").read_text(
            encoding="utf-8"
        )
    )
    soon = [m for m in data["modules"] if m.get("coming_soon")]
    assert soon == [], f"coming_soon leftovers: {[m['id'] for m in soon]}"
    routes = {m["id"]: m.get("route") for m in data["modules"]}
    assert routes["studio"] == "/studio"
    assert routes["voice"] == "/voice"
    assert routes["plugin_store"] == "/plugins"
    assert routes["cloud_soon"] == "/cloud"
    assert routes["ai_director"] == "/director"


def test_oauth_hard_gate_and_local_mirror(tmp_path, monkeypatch):
    """Missing OAuth secrets must not claim oauth_connected; mirror still works."""
    from engines.cloud.oauth import build_authorize_url, credential_status
    from engines.cloud.providers.stubs import GoogleDriveProvider

    for key in (
        "VM_GOOGLE_CLIENT_ID",
        "VM_GOOGLE_CLIENT_SECRET",
        "VM_ONEDRIVE_CLIENT_ID",
        "VM_ONEDRIVE_CLIENT_SECRET",
        "VM_DROPBOX_APP_KEY",
        "VM_DROPBOX_APP_SECRET",
    ):
        monkeypatch.delenv(key, raising=False)

    st = credential_status("google_drive", app_dir=tmp_path)
    assert st.configured is False
    assert st.oauth_status == "not_configured"
    assert "VM_GOOGLE_CLIENT_ID" in st.missing

    auth = build_authorize_url("google_drive", app_dir=tmp_path)
    assert auth["ok"] is False
    assert auth["error"] == "oauth_credentials_missing"
    assert auth["local_mirror_available"] is True

    p = GoogleDriveProvider(tmp_path, {})
    conn = p.connect()
    assert conn.connected is True  # local mirror
    assert conn.meta.get("oauth_connected") is False
    assert conn.meta.get("oauth_remote_gated") is True
    assert conn.meta.get("local_mirror_available") is True

    # With credentials but no token → needs_auth, still not oauth_connected
    monkeypatch.setenv("VM_GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("VM_GOOGLE_CLIENT_SECRET", "sec")
    st2 = credential_status("google_drive", app_dir=tmp_path)
    assert st2.configured is True
    assert st2.oauth_status == "needs_auth"
    auth2 = build_authorize_url("google_drive", app_dir=tmp_path)
    assert auth2["ok"] is True
    assert "accounts.google.com" in auth2["url"]
    conn2 = GoogleDriveProvider(tmp_path, {}).connect()
    assert conn2.meta.get("oauth_connected") is not True


def test_streaming_capabilities_and_file_pipeline(tmp_path):
    from engines.streaming_studio.session import (
        CaptureSpec,
        StreamingSession,
        probe_streaming_capabilities,
    )

    caps = probe_streaming_capabilities()
    assert "ffmpeg" in caps
    assert "file_to_rtmp" in caps
    # Without RTMP URL, file_to_rtmp must fail honestly
    sess = StreamingSession.create(tmp_path, CaptureSpec(microphone=False))
    fake = tmp_path / "clip.wav"
    fake.write_bytes(b"RIFF" + b"\x00" * 40)
    result = sess.file_to_rtmp(str(fake), rtmp_url="")
    assert result["ok"] is False
    assert "RTMP" in (result.get("error") or "")


def test_live_preflight_structure():
    from engines.live.preflight import preflight_live

    pf = preflight_live(require_stt=False, require_tts=False)
    assert "ok" in pf
    assert "engines" in pf
    assert "ffmpeg" in pf["engines"]
