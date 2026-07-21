"""RASM R0 — dual playback foundation: settings, paths, API wiring."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_rasm_default_settings():
    from engines.rasm.config import default_settings

    s = default_settings()
    assert 0.10 <= s.reference_audio_volume <= 0.20
    assert s.dub_volume == 1.0
    assert s.listen_mode == "dual"
    assert s.yellow_reserve_ms == 150


def test_rasm_settings_roundtrip(tmp_path):
    from engines.rasm.config import RasmSettings, load_rasm_settings, save_rasm_settings

    s = RasmSettings(reference_audio_volume=0.12, dub_volume=0.9, listen_mode="original")
    save_rasm_settings(s, app_dir=tmp_path)
    loaded = load_rasm_settings(app_dir=tmp_path)
    assert loaded.reference_audio_volume == pytest.approx(0.12)
    assert loaded.dub_volume == pytest.approx(0.9)
    assert loaded.listen_mode == "original"


def test_rasm_settings_clamp_invalid_mode():
    from engines.rasm.config import RasmSettings

    s = RasmSettings(listen_mode="nope", reference_audio_volume=2.5).clamp()
    assert s.listen_mode == "dual"
    assert s.reference_audio_volume == 1.0


def test_resolve_original_audio_path(tmp_path):
    from engines.rasm.audio_paths import resolve_original_audio_path

    out = tmp_path / "output"
    out.mkdir()
    audio = out / "clip_original.mp3"
    audio.write_bytes(b"ID3")
    state = {"original_audio": "clip_original.mp3"}
    found = resolve_original_audio_path(
        "task1", state=state, output_dir=out, uploads_dir=tmp_path / "uploads"
    )
    assert found is not None
    assert found.name == "clip_original.mp3"


def test_resolve_original_missing():
    from engines.rasm.audio_paths import original_available

    assert not original_available("no-such-task", state={})


def test_feature_flag_rasm_present():
    import json

    flags = json.loads((ROOT / "data" / "feature_flags.json").read_text(encoding="utf-8"))
    ids = {f["id"] for f in flags["features"]}
    assert "rasm" in ids
    rasm = next(f for f in flags["features"] if f["id"] == "rasm")
    assert rasm.get("enabled") is True


def test_studio_api_rasm_routes_registered():
    from api import studio_api as mod

    assert hasattr(mod, "api_studio_original")
    assert hasattr(mod, "api_rasm_settings_get")
    assert hasattr(mod, "api_rasm_settings_post")
    assert hasattr(mod, "api_rasm_status")


def test_rasm_player_js_exists():
    js = (ROOT / "static" / "js" / "rasm_player.js").read_text(encoding="utf-8")
    assert "RasmPlayer" in js
    assert "alignToMaster" in js
    assert "KeyB" in js


def test_studio_html_has_sync_qc_button():
    html = (ROOT / "templates" / "studio.html").read_text(encoding="utf-8")
    assert "se-btn-sync-qc" in html
    assert "rasm_player.js" in html
    assert "rasm-panel-host" in html
