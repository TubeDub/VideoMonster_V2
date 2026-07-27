"""
Regression tests for the professional audio mix pipeline (TZ: Dub Engine /
Audio Pipeline — intelligent voice ducking + stem mixing).

Covers:
  * AudioMixConfig defaults, clamping, dB→sidechain mapping
  * resolve_mix_config precedence (explicit > request > profile > default)
  * DubEngine command ROUTING for every dub mode / stem availability combo
  * That the professional filter graphs actually contain sidechaincompress and
    the right number of amix inputs (music/SFX preserved, only voice ducked).

No FFmpeg or media required — we assert on generated command lists / filter
strings and use tiny temp files to satisfy stem-existence checks.
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engines.audio_mix_config import (  # noqa: E402
    AudioMixConfig,
    resolve_mix_config,
    is_voice_ducking_enabled,
)
from engines.dub_engine import DubEngine  # noqa: E402


def _touch(path: str) -> str:
    with open(path, "wb") as fh:
        fh.write(b"\x00")
    return path


def _filter_of(cmd: list[str]) -> str:
    """Return the -filter_complex / -af argument from a command list."""
    for flag in ("-filter_complex", "-af"):
        if flag in cmd:
            return cmd[cmd.index(flag) + 1]
    return ""


# ── AudioMixConfig ───────────────────────────────────────────────────────────

def test_config_defaults_and_clamp():
    cfg = AudioMixConfig()
    assert cfg.dub_volume == 1.0
    assert cfg.background_volume == 1.0
    assert cfg.ducking_enabled is True

    # Clamping
    hot = AudioMixConfig(dub_volume=99, ducking_db=-999, fade_ms=999999)
    assert 0.0 <= hot.dub_volume <= 2.0
    assert hot.ducking_db >= -60.0
    assert hot.fade_ms <= 1500
    print("OK test_config_defaults_and_clamp")


def test_sidechain_params_scale_with_db():
    shallow = AudioMixConfig(ducking_db=-4).sidechain_params()
    deep = AudioMixConfig(ducking_db=-20).sidechain_params()
    assert deep["ratio"] > shallow["ratio"], "deeper ducking → higher ratio"
    assert AudioMixConfig(ducking_db=-12).ducked_gain() < 1.0
    print("OK test_sidechain_params_scale_with_db")


def test_resolve_precedence():
    class _Prof:
        ducking_enabled = True
        ducking_level_db = -6.0
        ducking_fade_out_ms = 300

    # explicit beats request beats profile
    cfg = resolve_mix_config(
        ducking_db=-15.0,
        content_mode_profile=_Prof(),
        request={"ducking_db": -9.0},
    )
    assert cfg.ducking_db == -15.0
    # request beats profile when no explicit
    cfg2 = resolve_mix_config(content_mode_profile=_Prof(), request={"ducking_db": -9.0})
    assert cfg2.ducking_db == -9.0
    # profile used when neither
    cfg3 = resolve_mix_config(content_mode_profile=_Prof(), request={})
    assert cfg3.ducking_db == -6.0
    print("OK test_resolve_precedence")


# ── DubEngine routing ────────────────────────────────────────────────────────

def _engine(tmp, *, with_stems: bool, ducking: bool):
    video = _touch(os.path.join(tmp, "v.mp4"))
    dub = _touch(os.path.join(tmp, "dub.wav"))
    bg = dlg = ""
    if with_stems:
        bg = _touch(os.path.join(tmp, "music_sfx.wav"))
        dlg = _touch(os.path.join(tmp, "dialogue.wav"))
    cfg = AudioMixConfig(ducking_enabled=ducking, original_voice_volume=0.38)
    return DubEngine(
        video_path=video,
        timed_audio=dub,
        background_audio_path=bg,
        dialogue_audio_path=dlg,
        mix_config=cfg,
    ), dub


def test_route_stem_underlay_voiceduck():
    """Stem + underlay + voice stem → 3-input mix with sidechain ducking of voice."""
    with tempfile.TemporaryDirectory() as tmp:
        eng, dub = _engine(tmp, with_stems=True, ducking=True)
        cmd = eng._select_mix_command(
            dub, os.path.join(tmp, "out.mp4"), "custom", 0.38, 1.0, False, []
        )
        filt = _filter_of(cmd)
        assert "sidechaincompress" in filt, "voice must be ducked via sidechain"
        assert "amix=inputs=3" in filt, "bg + voice + dub = 3 inputs"
        # music/SFX stem is a distinct input, not ducked
        assert eng.background_audio_path in cmd
        assert eng.dialogue_audio_path in cmd
    print("OK test_route_stem_underlay_voiceduck")


def test_route_stem_full_dub():
    """Stem + muted original → 2-input stem mix (music/SFX + dub, no voice)."""
    with tempfile.TemporaryDirectory() as tmp:
        eng, dub = _engine(tmp, with_stems=True, ducking=True)
        cmd = eng._select_mix_command(
            dub, os.path.join(tmp, "out.mp4"), "full_dub", 0.0, 1.0, False, []
        )
        filt = _filter_of(cmd)
        assert "amix=inputs=2" in filt
        # original voice stem is NOT mixed in full dub
        assert eng.dialogue_audio_path not in cmd
    print("OK test_route_stem_full_dub")


def test_route_no_stem_underlay_ducks():
    """No stem + underlay → full original ducked by dub via sidechain."""
    with tempfile.TemporaryDirectory() as tmp:
        eng, dub = _engine(tmp, with_stems=False, ducking=True)
        cmd = eng._select_mix_command(
            dub, os.path.join(tmp, "out.mp4"), "custom", 0.38, 1.0, False, []
        )
        filt = _filter_of(cmd)
        assert "sidechaincompress" in filt, "original must return between lines"
        assert "amix=inputs=2" in filt
    print("OK test_route_no_stem_underlay_ducks")


def test_route_no_stem_full_dub_replace():
    """No stem + muted original → plain replace (dub only)."""
    with tempfile.TemporaryDirectory() as tmp:
        eng, dub = _engine(tmp, with_stems=False, ducking=True)
        cmd = eng._select_mix_command(
            dub, os.path.join(tmp, "out.mp4"), "full_dub", 0.0, 1.0, False, []
        )
        # replace path maps original audio out, no amix
        assert "-map" in cmd and "-0:a" in cmd
        assert "sidechaincompress" not in " ".join(cmd)
    print("OK test_route_no_stem_full_dub_replace")


def test_ducking_off_static_underlay():
    """Ducking disabled → underlay is static (no sidechaincompress)."""
    with tempfile.TemporaryDirectory() as tmp:
        eng, dub = _engine(tmp, with_stems=True, ducking=False)
        cmd = eng._select_mix_command(
            dub, os.path.join(tmp, "out.mp4"), "custom", 0.38, 1.0, False, []
        )
        filt = _filter_of(cmd)
        assert "sidechaincompress" not in filt
        assert "amix=inputs=3" in filt, "voice still underlaid, just not ducked"
    print("OK test_ducking_off_static_underlay")


def test_feature_flag_probe_never_raises():
    # must not raise regardless of flag backend availability
    assert isinstance(is_voice_ducking_enabled(), bool)
    print("OK test_feature_flag_probe_never_raises")


def main() -> int:
    tests = [
        test_config_defaults_and_clamp,
        test_sidechain_params_scale_with_db,
        test_resolve_precedence,
        test_route_stem_underlay_voiceduck,
        test_route_stem_full_dub,
        test_route_no_stem_underlay_ducks,
        test_route_no_stem_full_dub_replace,
        test_ducking_off_static_underlay,
        test_feature_flag_probe_never_raises,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
