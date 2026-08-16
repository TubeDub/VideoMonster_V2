# -*- coding: utf-8 -*-
"""Stage 30 — census after pad, honest Edge stamp, LAST-RESORT closed_loop.

Closes remaining Stage 28/29 leftovers:
- census padded_count overwritten with stale 0 while files exist
- final_status=degraded when audio_missing==0
- tts_uk stamp after Edge fallback (sidecar keyed on synth path, not copy)
- LAST-RESORT pad_silence_{sid}.wav must be visible to census
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
# Census after pad: missing==0, padded_count matches audio_padded
# --------------------------------------------------------------------------


def test_census_after_pad_missing_zero_padded_count_matches(tmp_path):
    from api.auto_dub_api import _soft_pad_missing_segments
    from engines.segment_timing_qa import _build_openddf_tts_pipeline_block

    session_dir = tmp_path / "session"
    tid = "stage30pad"
    segs = [
        {
            "segment_id": "s0",
            "index": 0,
            "text": "Привіт",
            "final_tts_text": "Привіт",
            "start_ms": 0,
            "end_ms": 1000,
            "slot_ms": 1000,
            "tts_ms": 0,
            "file": None,
        },
        {
            "segment_id": "s1",
            "index": 1,
            "text": "Друзі",
            "final_tts_text": "Друзі",
            "start_ms": 1100,
            "end_ms": 2200,
            "slot_ms": 1100,
            "tts_ms": 0,
            "file": None,
        },
    ]
    task_info = {
        "task_id": tid,
        "session_dir": str(session_dir),
        "target_lang": "uk",
        "padded_count": 0,  # stale — census must ignore this 0
    }
    stats = _soft_pad_missing_segments(
        segs, task_info=task_info, task_id=tid, timing_map=None
    )
    assert stats["padded_count"] == 2

    block = _build_openddf_tts_pipeline_block(task_info, segments_data=segs)
    assert block["audio_missing"] == 0, block
    assert block["audio_present"] == 2
    assert block["padded_count"] == 2
    assert sorted(block["padded_indices"]) == [0, 1]
    assert block["silence_pads"] == 2
    assert block["final_status"] == "ok_with_pads"
    for row in block["segments"]:
        p = Path(str(row["resolved_path"]))
        assert p.is_absolute()
        assert p.is_file()
        assert p.stat().st_size >= 1000
        assert row["audio_padded"] is True


def test_final_status_not_degraded_when_missing_zero(tmp_path):
    from engines.segment_timing_qa import _build_openddf_tts_pipeline_block

    session_dir = tmp_path / "session"
    tid = "stage30ok"
    wav = _wav(session_dir / "closed_loop" / tid / "ok.wav", ms=400)

    # All files present, no pads — must be ok, never degraded.
    segs = [
        {
            "index": 0,
            "text": "a",
            "file": str(wav),
            "resolved_path": str(wav),
            "tts_ms": 400,
        }
    ]
    block = _build_openddf_tts_pipeline_block(
        {
            "task_id": tid,
            "session_dir": str(session_dir),
            "padded_count": 0,
            "final_status": "degraded",  # stale lie
            "segments_data": segs,
        },
        segments_data=segs,
    )
    assert block["audio_missing"] == 0
    assert block["final_status"] != "degraded"
    assert block["final_status"] == "ok"

    # Present + padded — ok_with_pads, never degraded.
    segs2 = [
        {
            "index": 0,
            "text": "b",
            "file": str(wav),
            "resolved_path": str(wav),
            "tts_ms": 400,
            "audio_padded": True,
            "silence_pad": True,
        }
    ]
    block2 = _build_openddf_tts_pipeline_block(
        {
            "task_id": tid,
            "session_dir": str(session_dir),
            "padded_count": 0,  # stale 0 must not win over audio_padded=True
        },
        segments_data=segs2,
    )
    assert block2["audio_missing"] == 0
    assert block2["padded_count"] == 1
    assert block2["final_status"] == "ok_with_pads"
    assert block2["final_status"] != "degraded"


def test_sync_pad_census_never_clobbers_with_zero(tmp_path):
    from api.auto_dub_api import _sync_pad_census_fields

    wav = _wav(tmp_path / "p.wav", ms=400)
    info = {"padded_count": 0, "padded_indices": [], "final_status": "degraded"}
    block = {
        "audio_missing": 0,
        "padded_count": 2,
        "padded_indices": [0, 3],
        "final_status": "ok_with_pads",
        "segments": [{"resolved_path": str(wav), "exists": True}],
    }
    _sync_pad_census_fields(info, block)
    assert block["padded_count"] == 2
    assert info["padded_count"] == 2
    assert info["final_status"] == "ok_with_pads"
    assert block["final_status"] != "degraded"


# --------------------------------------------------------------------------
# Honest Edge stamp (not tts_uk)
# --------------------------------------------------------------------------


def test_stamp_edge_fallback_honest_not_tts_uk(tmp_path):
    from engines.tts_backends import (
        record_last_synth_meta,
        stamp_tts_backend_meta,
        synthesize_with_backend,
    )
    from engines.tts_engines import registry

    out = tmp_path / "seg.wav"

    class _Fail:
        ok = False
        engine_id = "tts_uk"
        error = "tts_uk crashed"
        meta: dict = {}

    class _EdgeOK:
        ok = True
        engine_id = "edge-offline"
        error = None
        meta = {"voice": "uk-UA-OstapNeural"}

    calls = {"n": 0}

    def _fake(text, voice, path, **_kw):
        calls["n"] += 1
        if calls["n"] <= 2:
            return _Fail()
        _wav(Path(path), 300)
        return _EdgeOK()

    real = registry.synthesize
    registry.synthesize = _fake
    try:
        result = synthesize_with_backend(
            "Привіт друзі сьогодні разом",
            "mykyta",
            str(out),
            engine_id="tts_uk",
            target_lang="uk",
        )
    finally:
        registry.synthesize = real

    assert result.ok
    seg: dict = {"final_tts_text": "Привіт друзі сьогодні разом"}
    # Simulate the lie path: caller still thinks requested engine is tts_uk.
    stamp_tts_backend_meta(
        seg,
        engine_id="tts_uk",
        voice="mykyta",
        language="uk",
        audio_path=str(out),
    )
    assert seg["tts_backend"] == "edge-offline", seg
    assert seg["tts_engine"] == "edge-offline"
    assert str(seg["tts_voice"]).startswith("uk-UA-")
    assert seg.get("tts_fallback_reason") == "tts_uk_failed"
    assert seg["tts_backend"] != "tts_uk"


def test_stamp_synth_meta_edge_wins_over_requested_tts_uk():
    from engines.tts_backends import stamp_tts_backend_meta

    seg: dict = {}
    stamp_tts_backend_meta(
        seg,
        engine_id="tts_uk",
        voice="mykyta",
        language="uk",
        synth_meta={
            "tts_engine": "edge-offline",
            "tts_backend": "edge-offline",
            "tts_voice": "uk-UA-OstapNeural",
            "tts_fallback_reason": "tts_uk_failed",
            "tts_engine_requested": "tts_uk",
            "tts_voice_requested": "mykyta",
        },
    )
    assert seg["tts_backend"] == "edge-offline"
    assert seg["tts_voice"] == "uk-UA-OstapNeural"
    assert seg["tts_fallback_reason"] == "tts_uk_failed"


def test_transfer_last_synth_meta_survives_copy(tmp_path):
    from engines.tts_backends import (
        peek_last_synth_meta,
        record_last_synth_meta,
        transfer_last_synth_meta,
    )

    src = tmp_path / "src.wav"
    dest = tmp_path / "dest.wav"
    _wav(src, 300)
    _wav(dest, 300)
    record_last_synth_meta(
        src,
        {
            "tts_engine": "edge-offline",
            "tts_voice": "uk-UA-OstapNeural",
            "tts_fallback_reason": "tts_uk_failed",
        },
    )
    meta = transfer_last_synth_meta(src, dest)
    assert meta.get("tts_engine") == "edge-offline"
    peeked = peek_last_synth_meta(dest)
    assert peeked.get("tts_fallback_reason") == "tts_uk_failed"


# --------------------------------------------------------------------------
# LAST-RESORT pad writes closed_loop and census finds it
# --------------------------------------------------------------------------


def test_last_resort_pad_writes_closed_loop_and_census_finds_it(tmp_path):
    from api.auto_dub_api import _last_resort_pad_missing_segments
    from engines.segment_timing_qa import _build_openddf_tts_pipeline_block

    session_dir = tmp_path / "session"
    tid = "stage30lr"
    seg = {
        "segment_id": "hole",
        "index": 0,
        "text": "Привіт",
        "final_tts_text": "Привіт",
        "start_ms": 0,
        "end_ms": 1500,
        "slot_ms": 1500,
        "tts_ms": 0,
        "file": "missing.wav",
        "resolved_path": "output/sessions/ghost/missing.wav",
    }
    task_info = {
        "task_id": tid,
        "session_dir": str(session_dir),
        "target_lang": "uk",
        "padded_count": 0,
    }
    stats = _last_resort_pad_missing_segments(
        [seg], task_info=task_info, task_id=tid, timing_map=None
    )
    assert stats["padded_count"] == 1
    pad = Path(seg["resolved_path"])
    assert pad.is_file()
    assert pad.is_absolute()
    assert pad.stat().st_size >= 1000
    assert pad.parent == (session_dir / "closed_loop" / tid).resolve()
    assert pad.name == "pad_silence_hole.wav"
    assert seg["audio_padded"] is True

    block = _build_openddf_tts_pipeline_block(task_info, segments_data=[seg])
    assert block["audio_missing"] == 0, block
    assert block["audio_present"] == 1
    assert block["padded_count"] == 1
    assert block["final_status"] == "ok_with_pads"
    assert Path(block["segments"][0]["resolved_path"]).is_file()
