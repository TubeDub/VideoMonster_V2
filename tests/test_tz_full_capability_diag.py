# -*- coding: utf-8 -*-
"""Full TZ capability diagnostic — offline, no GUI, no network.

Covers Stage 40 A–G + identity / review / cleanup / census leftovers:
1) missing TTS → soft_pad, census audio_missing==0, padded_count>0, ok_with_pads
2) cs-CZ / sk-SK → Ostap / mykyta
3) overflow: text_slot_fit before atempo; atempo ≤1.08
4) snapshot identity-bind allowed for Simple AND main
5) cleanup keeps pad_silence_ / tts_ / slot_fit_ / fitted_ / regen_
6) pad_master_to_video_ms when track shorter than video
7) micro-slot 700ms + 10 words merges (850 floor)
8) split archives parent UUID
9) Review GET / populate does not mutate live text
10) UK text with Russian leak → skip/pad, never Czech voice
11) duration stamp not "none" when |delta|>200ms
12) neighbor rate jump clamp ±0.03
13) identity apply-by-id with shuffled DubEngine results
14) text change mints new tts_uuid
"""

from __future__ import annotations

import copy
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _uuid(n: int) -> str:
    return f"c{n:031x}"


def _wav(path: Path, ms: int = 500, sr: int = 24000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(round(ms / 1000.0 * sr))
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(sr)
        fh.writeframes(b"\x00\x00" * frames)
    return path


def test_1_missing_tts_soft_pad_census_ok_with_pads(tmp_path, monkeypatch):
    import api.auto_dub_api as api
    from engines.segment_timing_qa import _build_openddf_tts_pipeline_block

    monkeypatch.setattr(
        api,
        "_repair_missing_tts_files",
        lambda *a, **k: {"repaired": 0, "padded": 0, "failed": 0},
    )
    session = tmp_path / "session"
    tid = "tzdiag1"
    seg = {
        "segment_id": "hole0",
        "index": 0,
        "text": "Привіт",
        "final_tts_text": "Привіт",
        "start_ms": 0,
        "end_ms": 1200,
        "slot_ms": 1200,
        "tts_ms": 0,
        "file": None,
    }
    info = {
        "task_id": tid,
        "session_dir": str(session),
        "target_lang": "uk",
        "simple_pipeline": True,
        "happy_path": True,
    }
    out = api._prepare_segments_audio_before_mux(
        [seg],
        task_info=info,
        task_id=tid,
        timing_map=None,
        voice="uk-UA-OstapNeural",
    )
    assert out["ok"] is True
    assert Path(seg["resolved_path"]).is_file()
    assert int(info.get("padded_count") or 0) >= 1
    block = _build_openddf_tts_pipeline_block(info, segments_data=[seg])
    assert block["audio_missing"] == 0
    assert int(block.get("padded_count") or 0) >= 1
    assert block["final_status"] == "ok_with_pads"
    assert info["final_status"] != "audio_missing_fatal"


def test_2_cs_sk_voices_sanitize_to_uk():
    from engines.simple_voice_lock import DEFAULT_UK_VOICE, resolve_pipeline_voice
    from engines.tts_lang_lock import force_uk_tts_identity

    v = resolve_pipeline_voice({"target_lang": "uk", "voice": "cs-CZ-AntoninNeural"})
    assert v == DEFAULT_UK_VOICE
    assert v.startswith("uk-UA-")
    edge = force_uk_tts_identity(
        target_lang="uk", engine_id="edge", voice="sk-SK-LukasNeural"
    )
    assert str(edge["voice"]).startswith("uk-UA-")
    assert "cs" not in str(edge["voice"]).lower()
    assert "sk" not in str(edge["voice"]).lower()
    uk = force_uk_tts_identity(
        target_lang="uk", engine_id="tts_uk", voice="cs-CZ-AntoninNeural"
    )
    assert uk["engine_id"] == "tts_uk"
    assert uk["voice"] in ("mykyta", "tetiana", "lada")


def test_3_overflow_text_before_atempo():
    from engines.text_slot_fit import (
        STAGE31_ATEMPO_MAX,
        clamp_stage31_tempo,
        stage31_duration_levers,
    )

    levers = stage31_duration_levers(slot_ms=3000, tts_ms=5000)
    assert levers[0] == "text_slot_fit"
    assert "text_shorten" in levers
    assert levers.index("text_slot_fit") < levers.index("atempo")
    assert STAGE31_ATEMPO_MAX == 1.08
    assert clamp_stage31_tempo(1.30) == 1.08
    assert clamp_stage31_tempo(0.70) == 0.92


def test_4_snapshot_bind_simple_and_main():
    from engines.pipeline_integrity.guards import StageSnapshotGuard
    from engines.pipeline_integrity.stage_contracts import allowed_fields_for_stage

    allowed = allowed_fields_for_stage("tts")
    for field in (
        "identity_binding",
        "tts_meta",
        "revision_text_hash",
        "wav_segment_id",
        "final_tts_text",
        "assigned_voice",
    ):
        assert field in allowed, field

    sid = "d67f009d933b4a29b629162ff4b23745"
    before = [
        {
            "segment_id": sid,
            "index": 0,
            "identity_binding": {
                "segment_id": sid,
                "text_hash": "abc",
                "text_revision": "rev",
                "audio_path": "",
                "bound_at_stage": "pre_tts",
                "tts_bound": False,
            },
        }
    ]
    after = [
        {
            "segment_id": sid,
            "index": 0,
            "identity_binding": {
                "segment_id": sid,
                "text_hash": "abc",
                "text_revision": "rev",
                "audio_path": r"C:\tmp\segs\0000.mp3",
                "bound_at_stage": "post_tts",
                "tts_bound": True,
            },
            "final_tts_text": "Привіт",
            "tts_text": "Привіт",
            "tts_meta": {"segment_id": sid},
            "revision_text_hash": "hashhashhashhashhashhash",
            "wav_segment_id": sid,
            "file": r"C:\tmp\segs\0000.mp3",
            "tts_file_path": r"C:\tmp\segs\0000.mp3",
        }
    ]
    StageSnapshotGuard.check(before, after, stage="tts", mutator_module="engines.tts")
    assert StageSnapshotGuard.diff_violations(before, after, stage="tts") == []


def test_5_cleanup_keeps_segment_audio_prefixes(tmp_path):
    from engines.pipeline_cleanup import cleanup_intermediate_work_dirs
    from engines.pipeline_integrity.cleanup_manager import CleanupManager

    session = tmp_path / "sess"
    session.mkdir()
    keep = [
        _wav(session / "pad_silence_x.wav"),
        _wav(session / "tts_0000.wav"),
        _wav(session / "slot_fit_0000.wav"),
        _wav(session / "fitted_0000.wav"),
        _wav(session / "regen_0000.wav"),
    ]
    junk = session / "temp_extract.wav"
    junk.write_bytes(b"RIFF" + b"\x00" * 8)
    cleanup_intermediate_work_dirs(session, keep_segment_audio=True)
    for p in keep:
        assert p.is_file(), p.name
    info = {"pipeline_state": "SPEECH_READY", "session_dir": str(session)}
    CleanupManager(info).cleanup_session(session, success=False, mux_inputs_live=True)
    for p in keep:
        assert p.is_file(), p.name


def test_6_pad_master_to_video_ms(tmp_path):
    from engines.oss_production import pad_master_to_video_ms
    from pydub import AudioSegment

    short = AudioSegment.silent(duration=800, frame_rate=24000)
    padded = pad_master_to_video_ms(short, 2000, sample_rate=24000)
    assert abs(len(padded) - 2000) <= 20
    exact = pad_master_to_video_ms(padded, 2000, sample_rate=24000)
    assert abs(len(exact) - 2000) <= 20


def test_7_micro_slot_700ms_ten_words_merges():
    from engines.pipeline_integrity.segment_normalizer import (
        MIN_SLOT_MS,
        is_micro_or_fragment,
        merge_micro_slots,
    )

    assert MIN_SLOT_MS == 850
    text = "one two three four five six seven eight nine ten"
    assert is_micro_or_fragment(text, 700) is True
    texts, timing, report = merge_micro_slots(
        [text, "Neighbor sentence continues here."],
        [{"start": 0, "end": 700}, {"start": 700, "end": 2500}],
    )
    assert report.get("merged", 0) >= 1
    assert len(texts) == 1
    assert int(timing[0]["end"] - timing[0]["start"]) >= 850


def test_8_split_archives_parent_uuid():
    from engines.pipeline_integrity.identity_guard import reissue_split_children

    parent_id = _uuid(20)
    parent = {
        "segment_id": parent_id,
        "plain_text": "Parent line one. Parent line two.",
        "text": "Parent line one. Parent line two.",
        "file": "parent_tts.wav",
        "tts_uuid": "old-tts",
    }
    children = [
        {**parent, "plain_text": "Child unique A here.", "text": "Child unique A here."},
        {**parent, "plain_text": "Child unique B here.", "text": "Child unique B here."},
    ]
    archived, fresh = reissue_split_children(parent, children)
    assert archived["archived"] is True
    assert archived["segment_id"] == parent_id
    ids = {c["segment_id"] for c in fresh}
    assert parent_id not in ids
    assert len(ids) == 2


def test_9_review_get_does_not_mutate_live_text(monkeypatch):
    from api import auto_dub_api as api

    sid = _uuid(11)
    live_seg = {
        "segment_id": sid,
        "text": "Живий текст",
        "plain_text": "Живий текст",
        "final_text": "Живий текст",
        "final_tts_text": "Живий текст",
        "file": "tts_keep.wav",
        "tts_uuid": "tts-old",
    }
    task_id = "tz-review-ro"
    task = {
        "status": "translation_review",
        "output_file": None,
        "info": {
            "task_id": task_id,
            "target_lang": "uk",
            "source_segments": ["Live text"],
            "segments_data": [copy.deepcopy(live_seg)],
            "translation_audits": [
                {
                    "index": 0,
                    "final_text": "Живий текст",
                    "tts_text": "Живий текст",
                    "raw_translation": "Живий текст",
                }
            ],
        },
    }
    monkeypatch.setitem(api.AUTO_TASKS, task_id, task)
    before = copy.deepcopy(task["info"]["segments_data"])
    api._populate_translation_review_data(task_id, ["Живий текст"])
    assert task["info"]["segments_data"] == before
    assert task["info"]["segments_data"][0]["final_text"] == "Живий текст"


def test_10_ru_leak_skip_or_rewrite_never_czech_voice():
    from engines.simple_voice_lock import DEFAULT_UK_VOICE
    from engines.tts_lang_lock import (
        force_uk_tts_identity,
        guard_uk_tts_text,
        pre_mux_tts_integrity,
        uk_text_has_russian_leak,
    )

    ru = "Мне жаль что это так хорошо."
    assert uk_text_has_russian_leak(ru)
    out, meta = guard_uk_tts_text(ru, source_text="", allow_remt=False)
    assert meta.get("skipped") or meta.get("ruism_rewrite") or not uk_text_has_russian_leak(out)
    ident = force_uk_tts_identity(
        target_lang="uk", engine_id="edge", voice="cs-CZ-AntoninNeural"
    )
    assert ident["voice"].startswith("uk-UA-")
    segs = [
        {
            "assigned_voice": "cs-CZ-AntoninNeural",
            "voice": "cs-CZ-AntoninNeural",
            "final_tts_text": out or "Привіт друзі сьогодні українською мовою.",
            "tts_duration": 1.0,
            "skip_tts": bool(meta.get("skipped")),
            "tts_skip_reason": meta.get("fail_reason") or "",
        }
    ]
    pre = pre_mux_tts_integrity(segs, target_lang="uk", simple_mode=True)
    assert segs[0]["voice"] == DEFAULT_UK_VOICE
    assert "cs" not in str(segs[0]["voice"]).lower()
    assert pre.get("rerouted_default_uk") is True or segs[0]["voice"].startswith("uk-UA-")


def test_11_duration_stamp_not_none_when_delta_gt_200():
    from engines.text_slot_fit import backstop_duration_control_used

    overflow = {
        "slot_ms": 2000,
        "tts_ms": 2300,
        "duration_control_used": "none",
    }
    used = backstop_duration_control_used(overflow)
    assert used != "none"
    assert "text_slot_fit" in used or used in ("atempo", "length_scale", "soft_pad")

    under = {
        "start_ms": 0,
        "end_ms": 3000,
        "tts_ms": 2400,
        "duration_control_used": "",
        "audio_padded": True,
    }
    assert backstop_duration_control_used(under) == "soft_pad"

    tiny = {"slot_ms": 2000, "tts_ms": 2050, "duration_control_used": "none"}
    assert backstop_duration_control_used(tiny) == "none"


def test_12_neighbor_rate_jump_clamped_pm_003():
    from engines.closed_loop_timing import equalize_segment_tempos
    from engines.text_slot_fit import STAGE31_NEIGHBOR_JUMP_MAX

    assert STAGE31_NEIGHBOR_JUMP_MAX == 0.03
    segs = [
        {"index": 0, "atempo": 0.95, "tts_length_scale": 1.0},
        {"index": 1, "atempo": 1.20, "tts_length_scale": 1.0},
        {"index": 2, "atempo": 1.00, "tts_length_scale": 1.0},
    ]
    stats = equalize_segment_tempos(segs)
    assert stats["adjusted"] >= 1
    values = [float(s["atempo"]) for s in segs]
    assert all(0.92 <= v <= 1.08 for v in values)
    for a, b in zip(values, values[1:]):
        assert abs(a - b) <= 0.03 + 1e-9, values


def test_13_shuffled_engine_results_bind_by_segment_id():
    from engines.dubbing_engine.types import DubbingResult
    from engines.pipeline_integrity.identity_guard import apply_engine_text_results

    a_id, b_id = _uuid(1), _uuid(2)
    rows = [
        {"segment_id": a_id, "index": 0, "text": "OLD-A", "plain_text": "OLD-A"},
        {"segment_id": b_id, "index": 1, "text": "OLD-B", "plain_text": "OLD-B"},
    ]
    results = [
        DubbingResult(
            index=0,
            original_text="en-a",
            input_text="OLD-A",
            output_text="NEW-B-TEXT",
            passed_validation=True,
            segment_id=b_id,
        ),
        DubbingResult(
            index=1,
            original_text="en-b",
            input_text="OLD-B",
            output_text="NEW-A-TEXT",
            passed_validation=True,
            segment_id=a_id,
        ),
    ]
    stats = apply_engine_text_results(rows, results)
    assert stats["applied_by_id"] == 2
    assert stats["applied_by_index"] == 0
    assert rows[0]["text"] == "NEW-A-TEXT"
    assert rows[1]["text"] == "NEW-B-TEXT"


def test_14_text_change_mints_new_tts_uuid(monkeypatch):
    from engines.pipeline_integrity.psa_flags import VM_FLAG_REVISION_MANAGER

    monkeypatch.setenv(VM_FLAG_REVISION_MANAGER, "1")
    from engines.pipeline_integrity.revision_manager import (
        ensure_revision_uuids,
        note_text_change,
    )
    from engines.pipeline_integrity.uuid_chain import ensure_tts_uuid

    seg = {"segment_id": _uuid(9), "plain_text": "hello", "text": "hello"}
    ensure_revision_uuids(seg)
    old_tts = seg["tts_uuid"]
    note_text_change(seg, "hello there", kind="adaptation")
    ensure_tts_uuid(seg, force_new=True)
    assert seg["tts_uuid"]
    assert seg["tts_uuid"] != old_tts


def test_f_closed_loop_unresolved_is_soft_complete():
    from engines.closed_loop_timing import stamp_closed_loop_unresolved_soft

    stats = stamp_closed_loop_unresolved_soft({}, [0, 3])
    assert stats["closed_loop_unresolved_soft"] is True
    gate = stats["requires_llm_adaptation"]
    assert gate["reason"] == "closed_loop_unresolved"
    assert gate["soft_complete"] is True
    assert gate["count"] == 2


def test_pre_mux_order_never_aborts_on_missing(tmp_path, monkeypatch):
    import inspect

    import api.auto_dub_api as api

    src = inspect.getsource(api._prepare_segments_audio_before_mux)
    assert "_repair_missing_tts_files" in src
    assert "_soft_pad_missing_segments" in src
    assert "_last_resort_pad_missing_segments" in src
    assert src.index("_repair_missing_tts_files") < src.index("_soft_pad_missing_segments")
    assert src.index("_soft_pad_missing_segments") < src.index(
        "_last_resort_pad_missing_segments"
    )

    monkeypatch.setattr(
        api,
        "_repair_missing_tts_files",
        lambda *a, **k: {"repaired": 0, "padded": 0, "failed": 1},
    )
    info = {
        "task_id": "tzdiag-order",
        "session_dir": str(tmp_path / "s"),
        "target_lang": "uk",
        "simple_pipeline": True,
    }
    seg = {
        "segment_id": "x",
        "index": 0,
        "text": "Привіт",
        "start_ms": 0,
        "end_ms": 900,
        "slot_ms": 900,
        "file": None,
    }
    out = api._prepare_segments_audio_before_mux(
        [seg], task_info=info, task_id="tzdiag-order", voice="uk-UA-OstapNeural"
    )
    assert out["ok"] is True
    assert info.get("final_status") in ("ok", "ok_with_pads")
    assert info.get("final_status") != "audio_missing_fatal"
