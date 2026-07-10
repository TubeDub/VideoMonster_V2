"""Tests for source separation module and DubEngine stem mix."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from engines.dub_engine import DubEngine
from engines.source_separation import (
    DEFAULT_ACCOMPANIMENT_ATTENUATION_DB,
    FinalMixDiagnostics,
    SeparationResult,
    build_final_mix_diagnostics,
    get_background_mix_params,
    merge_openddf_source_separation,
    try_separate_audio,
)


def test_separation_result_to_dict():
    r = SeparationResult(success=True, method="ffmpeg_center_side", dialogue_path="/a.wav")
    d = r.to_dict()
    assert d["success"] is True
    assert d["method"] == "ffmpeg_center_side"
    assert d["dialogue_path"] == "/a.wav"


def test_get_background_mix_params_success():
    info = {
        "source_separation": {
            "success": True,
            "accompaniment_path": "/tmp/music.wav",
            "accompaniment_attenuation_db": 5.0,
        }
    }
    with patch("engines.source_separation.Path") as mock_path:
        mock_path.return_value.is_file.return_value = True
        path, atten, ok = get_background_mix_params(info)
    assert path == "/tmp/music.wav"
    assert atten == 5.0
    assert ok is True


def test_get_background_mix_params_fallback():
    path, atten, ok = get_background_mix_params({"source_separation": {"success": False}})
    assert path is None
    assert atten == DEFAULT_ACCOMPANIMENT_ATTENUATION_DB
    assert ok is False


def test_try_separate_disabled_flag(tmp_path):
    mono = tmp_path / "mono.mp3"
    mono.write_bytes(b"x")
    with patch("engines.source_separation.is_source_separation_enabled", return_value=False):
        result = try_separate_audio(
            video_path=str(tmp_path / "v.mp4"),
            mono_audio_path=str(mono),
            artifacts_dir=tmp_path,
            base_id="t1",
        )
    assert result.fallback_used is True
    assert result.dialogue_stt_path == str(mono)
    assert result.attempted is False


def test_try_separate_mono_fallback(tmp_path):
    mono = tmp_path / "mono.mp3"
    mono.write_bytes(b"x")
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")

    with patch("engines.source_separation.is_source_separation_enabled", return_value=True):
        with patch("engines.source_separation._ffmpeg_bin", return_value="ffmpeg"):
            with patch("engines.source_separation._extract_stereo_wav", return_value=True):
                with patch("engines.source_separation._probe_audio_channels", return_value=1):
                    result = try_separate_audio(
                        video_path=str(video),
                        mono_audio_path=str(mono),
                        artifacts_dir=tmp_path,
                        base_id="t2",
                    )
    assert result.fallback_used is True
    assert result.error == "mono_source"


def test_build_final_mix_diagnostics_stem_success(tmp_path):
    out_mp4 = tmp_path / "out.mp4"
    out_mp4.write_bytes(b"x")
    sep = {
        "success": True,
        "fallback_used": False,
        "dialogue_path": "/d.wav",
        "accompaniment_path": "/m.wav",
        "accompaniment_attenuation_db": 4.5,
    }
    diag = build_final_mix_diagnostics(
        separation_info=sep,
        final_mp4_path=str(out_mp4),
        mix_success=True,
        used_stem_mix=True,
    )
    assert diag.success is True
    assert diag.used_stem_mix is True
    assert diag.music_detected_in_final is True


def test_merge_openddf_source_separation():
    block = merge_openddf_source_separation(
        {
            "source_separation": {
                "attempted": True,
                "success": True,
                "fallback_used": False,
                "dialogue_path": "/d.wav",
                "accompaniment_path": "/m.wav",
            },
            "source_separation_final_mix": FinalMixDiagnostics(
                success=True, used_stem_mix=True
            ).to_dict(),
        }
    )
    assert block["separation_success"] is True
    assert block["dialogue_path"] == "/d.wav"
    assert block["final_mix"]["used_stem_mix"] is True


def test_dub_engine_stem_mix_command():
    engine = DubEngine(
        video_path="/video.mp4",
        timed_audio="/dub.mp3",
        background_audio_path="/music.wav",
        background_attenuation_db=4.5,
    )
    engine._ffmpeg = "ffmpeg"
    cmd = engine._cmd_stem_mix("/dub.mp3", "/out.mp4", dub_volume=1.0, bg_attenuation_db=4.5)
    assert "ffmpeg" in cmd
    assert "/music.wav" in cmd
    assert "/dub.mp3" in cmd
    assert "amix=inputs=2" in cmd[cmd.index("-filter_complex") + 1]
    assert engine._has_background_stem() is False


def test_dub_engine_has_background_stem(tmp_path):
    bg = tmp_path / "bg.wav"
    bg.write_bytes(b"x")
    engine = DubEngine(background_audio_path=str(bg))
    assert engine._has_background_stem() is True
