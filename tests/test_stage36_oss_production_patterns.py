# -*- coding: utf-8 -*-
"""Stage 36 — OSS production patterns (VideoLingo / pyVideoTrans / SoniTranslate).

Locks the patterns this repo was missing:
- one absolute session segs/ workdir (mux reads only that)
- missing TTS → silence pad, mix continues
- sequential place with gap (no overlay)
- mux pad to original video duration
- lock backend+voice after first successful synth
- duration: text → mild 0.9–1.1 speed → pad (never cartoon first)
"""

from __future__ import annotations

import sys
import wave
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _wav(path: Path, ms: int = 400, sr: int = 24000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(round(ms / 1000.0 * sr))
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(sr)
        fh.writeframes(b"\x00\x00" * frames)
    return path


def test_canonicalize_copies_live_pause_run_not_ghost_g0000(tmp_path):
    from engines.oss_production import canonicalize_session_artifacts
    from engines.pipeline_integrity.audio_presence import resolve_segment_audio_path

    session = tmp_path / "session"
    live = _wav(
        session / "closed_loop" / "t" / "pause" / "pause_run_alive.wav", ms=400
    )
    ghost = session / "task_g0000.mp3"
    ghost.write_bytes(b"tiny")

    segs = [
        {
            "index": 0,
            "start_ms": 0,
            "end_ms": 1000,
            "text": "Привіт",
            "fitted_file": str(live),
            "file": str(live),
            "resolved_path": str(ghost),
        }
    ]
    info = {"session_dir": str(session), "task_id": "t"}
    stamp = canonicalize_session_artifacts(segs, session, task_info=info)
    dest = Path(stamp["oss_segs_dir"]) / "0000.wav"
    assert dest.is_file(), dest
    assert dest.stat().st_size >= 1000
    assert segs[0]["file"] == str(dest.resolve())
    assert segs[0]["resolved_path"] == str(dest.resolve())
    assert segs[0]["oss_segs_path"] == str(dest.resolve())
    hit = resolve_segment_audio_path(segs[0])
    assert Path(hit).resolve() == dest.resolve()
    assert info["oss_segs_dir"] == str(dest.parent)


def test_missing_tts_becomes_silence_pad_mix_continues(tmp_path):
    from engines.oss_production import skip_missing_mix_inputs

    live = _wav(tmp_path / "ok.wav", ms=300)
    missing = tmp_path / "nope.mp3"
    out, padded = skip_missing_mix_inputs(
        [str(live), str(missing)],
        slot_ms_list=[300, 800],
        work_dir=tmp_path / "mix",
    )
    assert padded == 1
    assert Path(out[0]).is_file()
    assert Path(out[1]).is_file()
    assert Path(out[1]).stat().st_size >= 1000


def test_sequential_place_shifts_overlap_does_not_overlay():
    from engines.oss_production import sequential_place_starts

    # clip0 0–1000, clip1 wants 500 (500ms overlay) → shift to 1080
    placed = sequential_place_starts([0, 500], [1000, 400], min_gap_ms=80)
    assert placed[0] == 0
    assert placed[1] >= 1000 + 80
    assert placed[1] > placed[0]
    # already sequential stays put
    keep = sequential_place_starts([0, 2000], [1000, 400], min_gap_ms=80)
    assert keep == [0, 2000]


def test_concat_pads_to_video_duration_not_truncate_tail(tmp_path):
    from engines.oss_production import concat_sequential_track, pad_master_to_video_ms

    a = _wav(tmp_path / "a.wav", ms=300)
    b = _wav(tmp_path / "b.wav", ms=300)
    master = concat_sequential_track(
        [(a, 0, 300), (b, 400, 300)],
        video_ms=2000,
        min_gap_ms=80,
        sample_rate=24000,
    )
    assert abs(len(master) - 2000) <= 20
    padded = pad_master_to_video_ms(master[:800], 2000, sample_rate=24000)
    assert abs(len(padded) - 2000) <= 20


def test_lock_voice_after_first_success_pins_remaining():
    from engines.oss_production import lock_voice_after_first_success

    items = [
        {"index": 1, "voice": "uk-UA-PolinaNeural", "engine_id": "edge-offline"},
        {"index": 2, "voice": "uk-UA-OstapNeural", "engine_id": "tts_uk"},
    ]
    stamp = lock_voice_after_first_success(
        items, voice="uk-UA-OstapNeural", engine_id="edge-offline"
    )
    assert stamp["oss_locked_voice"] == "uk-UA-OstapNeural"
    assert stamp["oss_locked_engine"] == "edge-offline"
    assert all(it["voice"] == "uk-UA-OstapNeural" for it in items)
    assert all(it["engine_id"] == "edge-offline" for it in items)


def test_parallel_warmup_locks_pool_voice(tmp_path):
    from engines.tts_parallel import synthesize_segments_parallel

    calls = []

    def _fake_synth(**kwargs):
        calls.append(dict(kwargs))
        idx = int(kwargs["index"])
        dest = Path(kwargs["out_path"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"\xff\xfb" + b"\x00" * 2000)
        return {
            "index": idx,
            "path": str(dest),
            "cache_hit": False,
            "skipped_existing": False,
            "error": None,
            "retries": 0,
        }

    items = [
        {
            "index": 0,
            "text": "Привіт",
            "voice": "uk-UA-OstapNeural",
            "out_path": str(tmp_path / "out0.mp3"),
            "engine_id": "edge-offline",
        },
        {
            "index": 1,
            "text": "Друзі",
            "voice": "uk-UA-PolinaNeural",
            "out_path": str(tmp_path / "out1.mp3"),
            "engine_id": "tts_uk",
        },
    ]
    with patch(
        "engines.tts_parallel.synthesize_one_with_cache", side_effect=_fake_synth
    ):
        _results, stats = synthesize_segments_parallel(
            items, concurrency=2, warmup=1, cache_dir=tmp_path / "cache", use_cache=False
        )
    assert stats.get("oss_locked_voice") == "uk-UA-OstapNeural"
    pool_call = next(c for c in calls if int(c["index"]) == 1)
    assert pool_call["voice"] == "uk-UA-OstapNeural"
    assert pool_call["engine_id"] == "edge-offline"


def test_duration_lever_text_then_mild_speed_then_pad():
    from engines.oss_production import choose_duration_lever, clamp_oss_speed

    assert choose_duration_lever(tts_ms=3000, slot_ms=1000) == "text"
    assert choose_duration_lever(tts_ms=400, slot_ms=1000) == "text"
    assert choose_duration_lever(tts_ms=1100, slot_ms=1000) == "none"
    assert choose_duration_lever(tts_ms=900, slot_ms=1000) == "pad"
    # 8% over a 2s slot, still in 0.9–1.1 and |delta|>150 → mild speed
    assert choose_duration_lever(tts_ms=2160, slot_ms=2000) == "speed"
    assert clamp_oss_speed(1.4) == 1.10
    assert clamp_oss_speed(0.7) == 0.90
    assert 0.90 <= clamp_oss_speed(1.05) <= 1.10


def test_ghost_filename_rejected_for_tts_out(tmp_path):
    from engines.oss_production import is_ghost_group_filename, resolve_tts_out_path

    assert is_ghost_group_filename("task_g0000.mp3")
    assert not is_ghost_group_filename("pause_run_alive.wav")
    segs = tmp_path / "segs"
    ghost = tmp_path / "abc_g0003.mp3"
    ghost.write_bytes(b"\x00" * 2000)
    out = resolve_tts_out_path(segs, 3, ghost, ext=".mp3")
    assert out == segs / "0003.mp3"


def test_simple_policy_stamps_oss_flags():
    from engines.simple_dub_pipeline import apply_simple_pipeline_policy

    info = {"user_mode": "basic", "target_lang": "uk"}
    apply_simple_pipeline_policy(info)
    assert info["oss_segs_subdir"] == "segs"
    assert info["oss_sequential_place"] is True
    assert info["oss_never_abort_mux"] is True
    assert float(info["oss_speed_min"]) == 0.90
    assert float(info["oss_speed_max"]) == 1.10
    assert float(info["max_atempo"]) <= 1.10
