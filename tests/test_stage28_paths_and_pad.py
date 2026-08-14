# -*- coding: utf-8 -*-
"""Stage 28 — path truth, pad enforcement, honest census, UK pre-flight.

Regression for diagnostic ``task_id=4a512fd6``:
- ``audio_present=6, audio_missing=20, padded_count=0`` even after re-pad — the
  census could not find pads sitting under ``session_dir/closed_loop/<task_id>/``.
- Mixed relative ``output\\sessions\\...`` + absolute ``C:\\Users\\...`` paths
  bled through despite Stage 24/26 absolutization.
- ``tts_uk/mykyta`` stamp but Czech/Slovak audio — voice hit synth without the
  UK ban gate rewriting it to Ostap/Polina.
"""

from __future__ import annotations

import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _wav(path: Path, ms: int = 500, sr: int = 24000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(round(ms / 1000.0 * sr))
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(sr)
        fh.writeframes(b"\x00\x00" * frames)
    return path


# --------------------------------------------------------------------------
# §A2 — census + absolutize deep-resolve into closed_loop/<task_id>/
# --------------------------------------------------------------------------


def test_census_finds_pads_under_closed_loop_subtree(tmp_path):
    """`_build_openddf_tts_pipeline_block` MUST report exists:true for a pad
    that only lives under ``session_dir/closed_loop/<task_id>/``.

    Prior bug: census only checked ``session_dir/basename`` and ``basename`` —
    the pad was invisible → audio_missing spuriously counted.
    """
    from engines.segment_timing_qa import _build_openddf_tts_pipeline_block

    session_dir = tmp_path / "session"
    tid = "4a512fd6"
    pad_dir = session_dir / "closed_loop" / tid
    pad_file = _wav(pad_dir / "pad_silence_abc.wav", ms=1000)

    # Segment carries a *stale* relative path — must still resolve.
    seg = {
        "segment_id": "abc",
        "index": 0,
        "text": "test",
        "final_tts_text": "test",
        "file": "pad_silence_abc.wav",  # basename only
        "resolved_path": "output/sessions/old/closed_loop/other/pad_silence_abc.wav",
        "audio_padded": True,
        "silence_pad": True,
        "tts_ms": 1000,
    }
    task_info = {
        "task_id": tid,
        "session_dir": str(session_dir),
        "target_lang": "uk",
    }

    block = _build_openddf_tts_pipeline_block(
        task_info, segments_data=[seg]
    )
    assert block["expected_segments"] == 1
    assert block["audio_missing"] == 0, block
    assert block["audio_present"] == 1
    # resolved_path stamped by census should point at the physical file.
    row = block["segments"][0]
    assert row["exists"] is True
    assert row["size_bytes"] >= 40_000
    assert Path(row["resolved_path"]).resolve() == pad_file.resolve()


def test_absolutize_prefers_closed_loop_over_stale_relative(tmp_path):
    """`_absolutize_segment_audio_paths` rewrites stale relative → absolute
    when the physical file lives under closed_loop/<task_id>/.
    """
    from api.auto_dub_api import _absolutize_segment_audio_paths

    session_dir = tmp_path / "session"
    tid = "4a512fd6"
    pad_dir = session_dir / "closed_loop" / tid
    real_pad = _wav(pad_dir / "pad_silence_seg1.wav")

    seg = {
        "segment_id": "seg1",
        "index": 0,
        "file": "pad_silence_seg1.wav",  # relative basename
        "resolved_path": "output/sessions/OLD/pad_silence_seg1.wav",  # stale
    }
    fixed = _absolutize_segment_audio_paths([seg], str(session_dir), task_id=tid)
    assert fixed >= 1
    for key in ("file", "resolved_path"):
        assert Path(seg[key]).is_file(), seg
        assert Path(seg[key]).resolve() == real_pad.resolve()


# --------------------------------------------------------------------------
# §B — soft-pad always writes into session_dir/closed_loop/<task_id>/
# --------------------------------------------------------------------------


def test_soft_pad_writes_into_closed_loop_task_subdir(tmp_path):
    from api.auto_dub_api import _soft_pad_missing_segments

    session_dir = tmp_path / "session"
    tid = "task28"
    seg = {
        "segment_id": "s0",
        "index": 0,
        "text": "test",
        "final_tts_text": "test",
        "start_ms": 0,
        "end_ms": 1200,
        "slot_ms": 1200,
        "tts_ms": 0,  # missing → force pad
        "file": None,
    }
    task_info = {
        "task_id": tid,
        "session_dir": str(session_dir),
        "target_lang": "uk",
    }
    stats = _soft_pad_missing_segments(
        [seg],
        task_info=task_info,
        task_id=tid,
        timing_map=None,
    )
    assert stats["padded_count"] == 1

    pad_path = Path(seg["resolved_path"])
    assert pad_path.is_file()
    # Must land under closed_loop/<task_id>/ — where census also looks.
    assert pad_path.parent == (session_dir / "closed_loop" / tid).resolve()
    assert seg["audio_padded"] is True
    assert seg["duration_control_used"] == "soft_pad"
    assert task_info["final_status"] == "ok_with_pads"


# --------------------------------------------------------------------------
# §C1 — UK pre-flight ban gate: forbidden voice → safe UK before synth
# --------------------------------------------------------------------------


def test_synth_with_backend_rewrites_forbidden_voice_before_synth(tmp_path):
    """If someone hands us cs-CZ-VlastaNeural for a UK target, we MUST rewrite
    to uk-UA-* before ever calling the backend."""
    from engines import tts_backends
    from engines.tts_engines import registry

    captured: dict = {}

    class _OKResult:
        ok = True
        engine_id = "edge-offline"
        error = None
        meta: dict = {}

    def _fake(text, voice, path, **kw):
        captured["voice"] = voice
        captured["engine_id"] = kw.get("engine_id")
        _wav(Path(path), 300)
        return _OKResult()

    real = registry.synthesize
    registry.synthesize = _fake
    try:
        tts_backends.synthesize_with_backend(
            "Привіт", "cs-CZ-VlastaNeural",
            str(tmp_path / "seg.wav"),
            engine_id="edge-offline",
            target_lang="uk",
        )
    finally:
        registry.synthesize = real

    # Voice must have been rewritten to a safe uk-UA-* — NEVER cs-CZ.
    assert not str(captured["voice"]).startswith("cs-CZ")
    assert str(captured["voice"]).startswith("uk-UA-")


def test_synth_with_backend_never_leaks_short_id_to_edge(tmp_path):
    """Short tts_uk ids (mykyta/lada/tetiana) → Edge must never see them."""
    from engines import tts_backends
    from engines.tts_engines import registry

    captured: dict = {}

    class _OKResult:
        ok = True
        engine_id = "edge-offline"
        error = None
        meta: dict = {}

    def _fake(text, voice, path, **kw):
        captured["voice"] = voice
        _wav(Path(path), 300)
        return _OKResult()

    real = registry.synthesize
    registry.synthesize = _fake
    try:
        tts_backends.synthesize_with_backend(
            "Привіт", "mykyta",
            str(tmp_path / "seg.wav"),
            engine_id="edge-offline",
            target_lang="uk",
        )
    finally:
        registry.synthesize = real

    assert str(captured["voice"]).startswith("uk-UA-")


# --------------------------------------------------------------------------
# §E — softpad_ prefix survives cleanup
# --------------------------------------------------------------------------


def test_softpad_prefix_protected_from_cleanup(tmp_path, monkeypatch):
    from engines import dub_task_state as dts

    monkeypatch.setattr(dts, "OUTPUT_DIR", tmp_path)
    softpad = _wav(tmp_path / "softpad_t1_0001_abc.wav")
    stray = _wav(tmp_path / "abc_seg0000.mp3")
    out_file = tmp_path / "video_OUTPUT_abc.mp4"
    out_file.write_bytes(b"mp4")

    task = {
        "status": "done",
        "output_file": out_file.name,
        "info": {
            "keep_studio_assets": False,
            "segments_data": [{"file": softpad.name}, {"file": stray.name}],
            "tts_files": [softpad.name, stray.name],
            "mux_base_id": "abc",
        },
    }
    dts.cleanup_task_tts_files("abc", task, output_dir=tmp_path)

    assert softpad.is_file(), "softpad_* must be whitelisted like slot_fit_/tts_/pad_silence_"
    assert not stray.exists(), "unprotected legacy mp3 should still be removed"
    assert out_file.is_file()


# --------------------------------------------------------------------------
# §F — UK simple defaults stamp mykyta rate/length_scale/volume
# --------------------------------------------------------------------------


def test_simple_pipeline_uk_defaults_use_mykyta_and_atempo_105():
    from engines.simple_dub_pipeline import apply_simple_pipeline_policy

    info = {"target_lang": "uk", "user_mode": "basic"}
    apply_simple_pipeline_policy(info, user_mode="basic")

    assert info["max_atempo"] <= 1.05 + 1e-6
    assert info.get("mykyta_rate") == 0.97
    assert info.get("mykyta_length_scale") == 1.05
    assert info.get("mykyta_volume") == 1.05


def test_simple_pipeline_non_uk_keeps_legacy_atempo_cap():
    from engines.simple_dub_pipeline import apply_simple_pipeline_policy

    info = {"target_lang": "es", "user_mode": "basic"}
    apply_simple_pipeline_policy(info, user_mode="basic")

    # Non-UK targets keep the legacy 1.15 window — no regression outside Simple UK.
    assert info["max_atempo"] > 1.05 + 1e-6
    assert "mykyta_rate" not in info


# --------------------------------------------------------------------------
# §D1 — strip_slot_pad_fillers removes garbage pads before TTS voicing
# --------------------------------------------------------------------------


def test_strip_slot_pad_fillers_removes_stage5_pads():
    from engines.text_slot_fit import strip_slot_pad_fillers

    dirty = "Привіт всім, ось як це було тоді."
    clean = strip_slot_pad_fillers(dirty)
    assert "ось як це було тоді" not in clean
    assert "Привіт всім" in clean


def test_strip_slot_pad_fillers_removes_echo_restatement():
    from engines.text_slot_fit import strip_slot_pad_fillers

    dirty = "Ми переїхали. Саме так: назавжди."
    clean = strip_slot_pad_fillers(dirty)
    assert "Саме так" not in clean
    assert "Ми переїхали" in clean
