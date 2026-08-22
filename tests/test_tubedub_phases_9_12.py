"""TubeDub Phases 9–12: Review snapshot, resegment, cleanup, QA/FSM."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from engines.pipeline_integrity.psa_flags import (
    VM_FLAG_IDENTITY_GUARD,
    VM_FLAG_IDENTITY_GUARD_ENFORCE,
    VM_FLAG_IDENTITY_GUARD_SHADOW,
    VM_FLAG_REVISION_MANAGER,
    VM_FLAG_SEGMENT_NORMALIZER,
)

ROOT = Path(__file__).resolve().parents[1]
BA6EC = ROOT / "tests" / "fixtures" / "ba6ec_compact.json"


def _uuid(n: int) -> str:
    return f"c{n:031x}"


def test_review_get_does_not_mutate_pipeline_state(monkeypatch):
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
    task_id = "t-review-ro"
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
    assert task["info"]["segments_data"][0]["file"] == "tts_keep.wav"
    assert task["info"]["segments_data"][0]["final_text"] == "Живий текст"
    assert (task["info"].get("translation_review") or {}).get("segments")

    from app import app as flask_app

    with flask_app.test_request_context(
        f"/api/auto_dub/translation_review/{task_id}"
    ):
        resp = api.api_translation_review(task_id)
    data = resp.get_json()
    assert data.get("ok") is True
    assert task["info"]["segments_data"] == before


def test_review_apply_looks_up_by_segment_id(monkeypatch):
    monkeypatch.setenv(VM_FLAG_REVISION_MANAGER, "1")
    from api.auto_dub_api import _apply_translation_text_edits

    a_id, b_id = _uuid(1), _uuid(2)
    info = {
        "segments_data": [
            {
                "segment_id": a_id,
                "index": 0,
                "text": "A-old",
                "plain_text": "A-old",
                "adaptation_uuid": "ada",
                "tts_uuid": "tts-a",
            },
            {
                "segment_id": b_id,
                "index": 1,
                "text": "B-old",
                "plain_text": "B-old",
                "adaptation_uuid": "adb",
                "tts_uuid": "tts-b",
            },
        ],
        "translation_audits": [],
        "pipeline_state": "LOCKED",
    }
    # Display index 1 would hit A; UUID says edit B.
    _apply_translation_text_edits(
        info,
        edits=[{"index": 1, "segment_id": b_id, "text": "B-new"}],
    )
    assert info["segments_data"][0]["text"] == "A-old"
    assert info["segments_data"][1]["text"] == "B-new"
    assert info["segments_data"][1]["needs_retts"] is True
    assert info["segments_data"][1]["tts_uuid"] != "tts-b"
    assert info["segments_data"][1]["adaptation_uuid"] != "adb"


def test_review_payload_exposes_overflow_fields():
    from engines.translation_review import build_translation_review

    review = build_translation_review(
        {
            "source_segments": ["Hello there"],
            "target_lang": "uk",
            "segments_data": [
                {
                    "segment_id": _uuid(3),
                    "plain_text": "Привіт",
                    "final_text": "Привіт",
                    "slot_ms": 1000,
                    "playback_duration": 1400,
                    "overflow_ms": 400,
                    "tts_ms": 1400,
                }
            ],
            "translation_audits": [{"index": 0, "final_text": "Привіт"}],
        }
    )
    row = review["segments"][0]
    assert row["source_slot_ms"] == 1000
    assert row["target_speech_ms"] >= 1000
    assert int(row["overflow_ms"] or 0) >= 0


def test_resegment_split_archives_old_id_new_uuids():
    from engines.pipeline_integrity.identity_guard import reissue_split_children

    parent_id = _uuid(20)
    parent = {
        "segment_id": parent_id,
        "plain_text": "Parent line one. Parent line two.",
        "text": "Parent line one. Parent line two.",
        "file": "parent_tts.wav",
        "tts_file_path": "parent_tts.wav",
        "tts_uuid": "old-tts",
    }
    children = [
        {**parent, "plain_text": "Child unique A here.", "text": "Child unique A here."},
        {**parent, "plain_text": "Child unique B here.", "text": "Child unique B here."},
    ]
    archived, fresh = reissue_split_children(parent, children)
    assert archived["archived"] is True
    assert archived["segment_id"] == parent_id
    assert len(fresh) == 2
    ids = {c["segment_id"] for c in fresh}
    assert parent_id not in ids
    assert len(ids) == 2
    texts = {c["plain_text"] for c in fresh}
    assert texts == {"Child unique A here.", "Child unique B here."}
    for child in fresh:
        assert child.get("file") in (None, "")
        assert child.get("tts_file_path") in (None, "")
        assert child["tts_uuid"] != "old-tts"
        assert child["needs_retts"] is True
        assert child["parent_segment_id"] == parent_id


def test_identity_guard_merge_head_uuid_only():
    from engines.pipeline_integrity.identity_guard import _verify_identity_chain_strict

    head = _uuid(30)
    tail = _uuid(31)
    rows = [
        {
            "segment_id": head,
            "plain_text": "Merged head text",
            "final_tts_text": "Merged head text",
            "owned_text_segment_id": head,
        },
        {
            "segment_id": tail,
            "plain_text": "gone",
            "merged_into": 0,  # legacy index — must NOT be used as head
            "merged_into_id": head,
        },
    ]
    report = _verify_identity_chain_strict(rows, stage="merge_uuid", force=True)
    assert report["ok"] is True


def test_micro_slot_700ms_ten_words_is_not_lone(monkeypatch):
    monkeypatch.setenv(VM_FLAG_SEGMENT_NORMALIZER, "1")
    from engines.pipeline_integrity.segment_normalizer import is_micro_or_fragment

    text = "one two three four five six seven eight nine ten"
    assert is_micro_or_fragment(text, 700) is True


def test_long_segment_normalized_not_char_cap(monkeypatch):
    monkeypatch.setenv(VM_FLAG_SEGMENT_NORMALIZER, "1")
    from engines.pipeline_integrity.segment_normalizer import normalize_segments

    src = (
        "George Lucas went to film school. He later created Star Wars. "
        "The franchise changed cinema forever. "
    ) * 8
    texts, _timing, report = normalize_segments(
        [src],
        [{"start": 0, "end": 45000}],
        src_lang="en",
        tgt_lang="uk",
        run_smart_split=False,
    )
    joined = " ".join(texts)
    assert "George Lucas" in joined
    assert "Star Wars" in joined
    assert not any(t.rstrip().endswith("...") and t.count("...") == 1 for t in texts)
    assert report.get("enabled") is True
    # Not a dumb character cap: reconstructed text keeps the source meaning tokens.
    assert len(joined) >= len(src) * 0.8


def test_text_overflow_no_silent_truncation_as_fit():
    from engines.segment_timing_qa import looks_like_silent_truncation

    chop = {
        "plain_text": "Повний осмислений рядок про Джорджа Лукаса і кіно",
        "final_tts_text": "Повний осмислений рядок...",
        "text_adaptation_reason": "truncate",
    }
    assert looks_like_silent_truncation(chop) is True
    honest = {
        "plain_text": "Джордж Лукас зняв кіно",
        "final_tts_text": "Джордж Лукас зняв кіно",
        "text_adaptation_reason": "FitsNoChange",
    }
    assert looks_like_silent_truncation(honest) is False


def test_tts_revision_on_review_text_change(monkeypatch):
    monkeypatch.setenv(VM_FLAG_REVISION_MANAGER, "1")
    from engines.pipeline_integrity.revision_manager import (
        ensure_revision_uuids,
        note_text_change,
    )
    from engines.pipeline_integrity.uuid_chain import ensure_tts_uuid

    seg = {"segment_id": _uuid(40), "plain_text": "hello", "text": "hello"}
    ensure_revision_uuids(seg)
    old_tts = seg["tts_uuid"]
    note_text_change(seg, "hello there", kind="adaptation")
    ensure_tts_uuid(seg, force_new=True)
    assert seg["tts_uuid"] != old_tts


def test_cleanup_success_temp_deleted_final_preserved(tmp_path):
    from engines.pipeline_integrity.cleanup_manager import (
        ArtifactCategory,
        CleanupManager,
        classify_artifact,
    )
    from engines.pipeline_integrity.pipeline_state import PipelineState

    session = tmp_path / "session"
    session.mkdir()
    (session / "work").mkdir()
    temp = session / "work" / "temp_chunk.wav"
    temp.write_bytes(b"RIFF" + b"\x00" * 20)
    final = session / "movie_OUTPUT_abc.mp4"
    final.write_bytes(b"ftyp")
    info = {
        "pipeline_state": PipelineState.EXPORTED.value,
        "output_file": final.name,
        "cleanup_keep_names": [final.name],
        "session_dir": str(session),
    }
    assert classify_artifact(final, info=info) == ArtifactCategory.FINAL
    assert classify_artifact(temp, info=info) == ArtifactCategory.TEMP
    report = CleanupManager(info).cleanup_session(
        session,
        success=True,
        keep_names={final.name},
        mux_inputs_live=False,
    )
    assert not temp.exists()
    assert final.exists()
    assert str(temp) in report.removed
    assert any(final.name in p for p in report.preserved)


def test_cleanup_failure_deletes_temp(tmp_path):
    from engines.pipeline_integrity.cleanup_manager import CleanupManager

    session = tmp_path / "sess_fail"
    session.mkdir()
    junk = session / "temp_extract.wav"
    junk.write_bytes(b"RIFF" + b"\x00" * 8)
    info = {"pipeline_state": "MERGED", "session_dir": str(session)}
    CleanupManager(info).cleanup_session(session, success=False, mux_inputs_live=False)
    assert not junk.exists()


def test_cleanup_protects_mux_prefixes_while_live(tmp_path):
    from engines.pipeline_integrity.cleanup_manager import CleanupManager

    session = tmp_path / "sess_mux"
    session.mkdir()
    kept = []
    for name in (
        "slot_fit_a.wav",
        "pause_run_a.wav",
        "tts_0001.wav",
        "tts_regen_x.wav",
        "pad_silence_x.wav",
        "softpad_x.wav",
    ):
        p = session / name
        p.write_bytes(b"RIFF" + b"\x00" * 8)
        kept.append(p)
    info = {"pipeline_state": "SPEECH_READY"}
    report = CleanupManager(info).cleanup_session(
        session, success=False, mux_inputs_live=True
    )
    for p in kept:
        assert p.exists(), p.name
    assert report.blocked or report.preserved or report.skipped


def test_source_audio_underlay_and_ducking_path():
    from engines.simple_dub_pipeline import (
        SIMPLE_UK_SOURCE_UNDERLAY,
        apply_simple_uk_source_underlay,
    )
    from engines.dub_engine import DubEngine

    info = {"simple_pipeline": True, "happy_path": True, "target_lang": "uk"}
    mv = apply_simple_uk_source_underlay(
        info, {"original_volume": 0.0, "dub_volume": 1.0, "mix_mode": "full_dub"}
    )
    assert abs(float(mv["original_volume"]) - SIMPLE_UK_SOURCE_UNDERLAY) < 0.001
    assert mv.get("ducking_enabled") is True
    engine = DubEngine.__new__(DubEngine)
    engine.speech_intervals = [{"start_ms": 0, "end_ms": 500}]
    engine.ducking_enabled = True
    engine.ducking_db = -12.0
    engine.ducking_fade_in_ms = 0.04
    engine.ducking_fade_out_ms = 0.12
    filt = engine._build_ducking_filter()
    assert filt  # ducking graph exists when speech is present


def test_identity_guard_enforce_raises(monkeypatch):
    monkeypatch.setenv(VM_FLAG_IDENTITY_GUARD, "1")
    monkeypatch.setenv(VM_FLAG_IDENTITY_GUARD_ENFORCE, "1")
    monkeypatch.delenv(VM_FLAG_IDENTITY_GUARD_SHADOW, raising=False)
    from engines.pipeline_integrity.exceptions import PipelineIdentityError
    from engines.pipeline_integrity.identity_guard import verify_identity_chain

    data = json.loads(BA6EC.read_text(encoding="utf-8"))
    segs = []
    for row in data["segments"]:
        segs.append(
            {
                "segment_id": row["segment_id"],
                "plain_text": row["translated_text"],
                "translated_text": row["translated_text"],
                "final_tts_text": row["final_tts_text"],
                "owned_text_segment_id": row["segment_id"],
            }
        )
    with pytest.raises(PipelineIdentityError):
        verify_identity_chain(segs, stage="enforce_ba6ec")


def test_fsm_forbids_text_change_after_speech_without_revision():
    from engines.pipeline_integrity.exceptions import PipelineStateError
    from engines.pipeline_integrity.pipeline_state import assert_text_change_uses_revision

    info = {"pipeline_state": "SPEECH_READY"}
    seg = {
        "segment_id": _uuid(50),
        "plain_text": "old",
        "text": "old",
        "adaptation_uuid": "rev-1",
    }
    with pytest.raises(PipelineStateError):
        assert_text_change_uses_revision(info, seg, "brand new", old_revision="rev-1")


def test_qa_summary_hard_overflow_forbids_success():
    from engines.segment_timing_qa import build_final_dub_qa_report

    report = build_final_dub_qa_report(
        {
            "segments_data": [
                {
                    "segment_id": _uuid(60),
                    "plain_text": "ok",
                    "text": "ok",
                    "slot_ms": 1000,
                    "overflow_ms": 500,
                    "playback_duration": 1500,
                    "file": "tts_ok.wav",
                }
            ],
            "source_segments": ["ok"],
            "translation_audits": [],
            "timing_map": [{"start": 0, "end": 1000}],
        }
    )
    assert report["hard_overflow_count"] >= 1
    assert report["forbid_success"] is True
    assert report["final_status"] != "SUCCESS"
    assert report["segment_count"] == 1


def test_ba6ec_identity_chain_example():
    data = json.loads(BA6EC.read_text(encoding="utf-8"))
    bleed = next(
        s
        for s in data["segments"]
        if s["translated_text"] != s["final_tts_text"]
    )
    assert bleed["segment_id"]
    assert bleed["translated_text"]
    assert bleed["final_tts_text"]
    # Fixture proof only — live EN→UK dub is the user's next cold run.
    assert bleed["translated_text"] != bleed["final_tts_text"]
