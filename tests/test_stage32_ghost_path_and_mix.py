# -*- coding: utf-8 -*-
"""Stage 32 — diagnostic 2286c82f: ghost path, inverted mix, ending.

Zip metrics this suite locks:
- audio_present=7, audio_missing=17, padded_count=0, final_status=degraded
- exists:false while pause_run / tts_regen lived under closed_loop/.../pause/
- ghost resolved_path=…_g0000.mp3 hid the live file key
- relative file=tts_07db01fb_….mp3 (no directory)
- POST /api/studio/mix 500 with place_end < place_start (slot_ms=1)
- last original "and his film franchise was Star Wars" voiced as a photography scene
- mux atempo=1.15 after Stage 31 asked for 1.08
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


def test_census_prefers_live_pause_run_over_ghost_g0000(tmp_path):
    """Diag 2286c82f idx 0: resolved_path=g0000.mp3 missing, pause_run exists."""
    from engines.segment_timing_qa import _build_openddf_tts_pipeline_block

    session_dir = tmp_path / "session"
    tid = "stage32ghost"
    pause = _wav(
        session_dir
        / "closed_loop"
        / tid
        / "pause"
        / "pause_run_stage32ghost_alive.wav",
        ms=800,
    )
    rel = f"output\\sessions\\{tid}\\closed_loop\\{tid}\\pause\\{pause.name}"
    seg = {
        "segment_id": "stage32ghost0",
        "index": 0,
        "text": "Вісімнадцятирічний хлопець.",
        "file": rel,
        "fitted_file": str(
            session_dir / "closed_loop" / tid / "slot_fit_stage32_missing.wav"
        ),
        "resolved_path": str(session_dir / "stage32_g0000.mp3"),
        "tts_ms": 16394,
        "audio_padded": False,
    }
    block = _build_openddf_tts_pipeline_block(
        {
            "task_id": tid,
            "session_dir": str(session_dir),
            "target_lang": "uk",
            "padded_count": 0,
        },
        segments_data=[seg],
    )
    assert block["audio_missing"] == 0, block
    assert block["audio_present"] == 1
    assert block["padded_count"] == 0
    assert block["final_status"] != "degraded"
    row = block["segments"][0]
    assert row["exists"] is True
    assert Path(row["resolved_path"]).resolve() == pause.resolve()


def test_first_existing_resolver_skips_ghost_resolved_path(tmp_path):
    from engines.pipeline_integrity.audio_presence import resolve_segment_audio_path

    live = _wav(tmp_path / "pause_run_alive.wav", ms=400)
    ghost = tmp_path / "2286c82f_g0000.mp3"
    seg = {
        "resolved_path": str(ghost),
        "fitted_file": str(tmp_path / "slot_fit_gone.wav"),
        "file": str(live),
    }
    got = resolve_segment_audio_path(seg)
    assert Path(got).resolve() == live.resolve()


def test_relative_basename_tts_missing_gets_last_resort_pad(tmp_path):
    """Split-child file=tts_07db01fb_….mp3 with no directory → pad, not degraded."""
    from api.auto_dub_api import _last_resort_pad_missing_segments
    from engines.segment_timing_qa import _build_openddf_tts_pipeline_block

    session_dir = tmp_path / "session"
    tid = "stage32padhole"
    seg = {
        "segment_id": "stage32hole",
        "index": 8,
        "text": "Замість цього він взяв камеру.",
        "final_tts_text": "Замість цього він взяв камеру.",
        "start_ms": 0,
        "end_ms": 2000,
        "slot_ms": 2000,
        "tts_ms": 0,
        "file": "tts_stage32_only_missing_07db01fb.mp3",
        "fitted_file": None,
        "resolved_path": str(
            session_dir
            / "closed_loop"
            / tid
            / "tts_regen_stage32_e870b04d482b_missing.mp3"
        ),
    }
    info = {
        "task_id": tid,
        "session_dir": str(session_dir),
        "target_lang": "uk",
        "padded_count": 0,
    }
    stats = _last_resort_pad_missing_segments(
        [seg], task_info=info, task_id=tid, timing_map=None
    )
    assert stats["padded_count"] == 1
    block = _build_openddf_tts_pipeline_block(info, segments_data=[seg])
    assert block["audio_missing"] == 0, block
    assert block["padded_count"] == 1
    assert block["final_status"] == "ok_with_pads"


def test_inverted_merge_adjusted_start_does_not_invert_slot():
    from api.auto_dub_api import _clamp_placement_window

    start, end = _clamp_placement_window(
        45501, 47000, merge_adjusted_start=47000
    )
    assert start < end
    assert end - start >= 200
    start2, end2 = _clamp_placement_window(
        0, 17521, merge_adjusted_start=19352
    )
    # 19352 is past 17521 — ignore inverted stamp
    assert start2 < end2
    assert start2 == 0


def test_studio_segment_audio_name_returns_absolute(tmp_path, monkeypatch):
    from api import studio_api

    wav = _wav(tmp_path / "closed_loop" / "t" / "pause" / "pause_run_x.wav", ms=400)
    monkeypatch.setattr(
        studio_api,
        "_task_info_for",
        lambda task_id=None: {"session_dir": str(tmp_path), "task_id": "t"},
    )
    monkeypatch.setattr(studio_api, "OUTPUT_DIR", tmp_path)
    name = studio_api._segment_audio_name(
        {"index": 0, "file": wav.name, "fitted_file": str(wav)},
        task_id="t",
    )
    assert name
    p = Path(name)
    assert p.is_absolute()
    assert p.is_file()


def test_wrong_scene_last_line_replaced_with_star_wars():
    from engines.text_slot_fit import (
        ending_keeps_franchise_idea,
        ending_restore_targets,
        restore_ending_franchise_meaning,
    )

    src = "and his film franchise was Star Wars"
    recycled = (
        "Тепер Джордж молодший підійшов до подіуму, щоб сфотографувати "
        "переможного гонщика, але коли він проходив туди, цей чоловік "
        "середнього віку підійшов до нього і запитав Джорджа Молодшого "
        "про його фотографії."
    )
    assert ending_keeps_franchise_idea(src, recycled) is False
    restored = restore_ending_franchise_meaning(recycled, src)
    assert ending_keeps_franchise_idea(src, restored) is True
    low = restored.lower()
    assert "зоряні" in low or "франшиз" in low
    assert "подіум" not in low
    assert "гонщик" not in low

    segs = [
        {"index": 8, "start_ms": 1000, "text": "early split child"},
        {
            "index": 23,
            "start_ms": 172144,
            "original_text": src,
            "text": recycled,
            "end_ms": 178773,
        },
    ]
    tail = ending_restore_targets(segs, n=3, video_ms=178773)
    idxs = {t[1].get("index") for t in tail}
    assert 23 in idxs


def test_uk_mux_atempo_cap_is_108():
    from api.auto_dub_api import _UK_MUX_MAX_ATEMPO
    from engines.text_slot_fit import STAGE31_ATEMPO_MAX

    assert _UK_MUX_MAX_ATEMPO == 1.08
    assert STAGE31_ATEMPO_MAX == 1.08
