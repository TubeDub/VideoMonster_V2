"""PSA3 — Immutable Segment Contract (+ IdentityGuard)."""

from __future__ import annotations

import pytest

from engines.pipeline_integrity.exceptions import SegmentImmutabilityError
from engines.pipeline_integrity.identity_guard import bind
from engines.pipeline_integrity.immutable_segment import (
    apply_split_reissue_in_place,
    assert_no_text_move_or_swap,
    forbid_swap_texts,
    resegment_archive_and_reissue,
)
from engines.pipeline_integrity.psa_flags import VM_FLAG_IDENTITY_GUARD


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setenv(VM_FLAG_IDENTITY_GUARD, "1")
    monkeypatch.delenv("VM_IDENTITY_GUARD", raising=False)
    yield


@pytest.fixture
def flag_off(monkeypatch):
    monkeypatch.setenv(VM_FLAG_IDENTITY_GUARD, "0")
    monkeypatch.delenv("VM_IDENTITY_GUARD", raising=False)
    yield


def _sid(n: int) -> str:
    return f"b{n:031x}"


def test_psa3_swap_raises(flag_on):
    a = {
        "segment_id": _sid(1),
        "plain_text": "Alpha owned text",
    }
    b = {
        "segment_id": _sid(2),
        "plain_text": "Beta owned text",
    }
    bind(a, text="Alpha owned text", stage="pre")
    bind(b, text="Beta owned text", stage="pre")

    with pytest.raises(SegmentImmutabilityError) as exc:
        forbid_swap_texts(a, b, stage="test_swap")
    assert "swap" in str(exc.value).lower() or "forbidden" in str(exc.value).lower()

    # Actual pairwise swap of live texts must also be caught
    a["plain_text"], b["plain_text"] = b["plain_text"], a["plain_text"]
    with pytest.raises(SegmentImmutabilityError) as exc2:
        assert_no_text_move_or_swap([a, b], stage="test_swap_detect")
    assert "swap" in str(exc2.value).lower() or "moved" in str(exc2.value).lower()


def test_psa3_resegment_archives_old_and_mints_new_ids(flag_on):
    old_id = _sid(10)
    old = {
        "segment_id": old_id,
        "plain_text": "Long monologue that will be split into two halves here.",
        "start_ms": 0,
        "end_ms": 10000,
        "slot_ms": 10000,
    }
    bind(old, text=old["plain_text"], stage="pre")

    archived, fresh, _uuid_map = resegment_archive_and_reissue(
        [old],
        ["First half of the monologue.", "Second half continues the story."],
        [{"start": 0, "end": 5000}, {"start": 5000, "end": 10000}],
        stage="test_resegment",
    )

    assert len(archived) == 1
    assert archived[0]["archived"] is True
    assert archived[0]["segment_id"] == old_id
    assert old.get("archived") is True

    assert len(fresh) == 2
    new_ids = {s["segment_id"] for s in fresh}
    assert old_id not in new_ids
    assert len(new_ids) == 2
    for s in fresh:
        assert s.get("reissued_from_resegment") is True
        assert old_id in (s.get("reissued_from") or [])
        assert s.get("identity_binding", {}).get("segment_id") == s["segment_id"]
        assert s.get("owned_text_segment_id") == s["segment_id"]


def test_psa3_split_reissue_in_place(flag_on):
    old_id = _sid(20)
    segments = [
        {
            "segment_id": old_id,
            "plain_text": "Sentence one. Sentence two continues with more words here.",
            "start_ms": 0,
            "end_ms": 12000,
            "slot_ms": 12000,
        }
    ]
    bind(segments[0], text=segments[0]["plain_text"], stage="pre")
    sources = ["Sentence one. Sentence two continues with more words here."]
    timing = [{"start": 0, "end": 12000}]

    ok = apply_split_reissue_in_place(
        segments_data=segments,
        source_segments=sources,
        timing_map=timing,
        idx=0,
        src_left="Sentence one.",
        src_right="Sentence two continues with more words here.",
        tgt_left="Речення одне.",
        tgt_right="Речення два продовжується з більшою кількістю слів тут.",
        start0=0,
        end0=5000,
        start1=5000,
        end1=12000,
        force=True,
    )
    assert ok is True
    assert len(segments) == 2
    assert all(s["segment_id"] != old_id for s in segments)
    assert segments[0]["segment_id"] != segments[1]["segment_id"]
    assert len(sources) == 2
    assert len(timing) == 2


def test_psa3_flag_off_legacy_allows_noop(flag_off):
    a = {"segment_id": _sid(3), "plain_text": "A"}
    b = {"segment_id": _sid(4), "plain_text": "B"}
    # No raise when contract OFF
    forbid_swap_texts(a, b)
    report = assert_no_text_move_or_swap([a, b])
    assert report["enabled"] is False
    archived, fresh, umap = resegment_archive_and_reissue(
        [a], ["x"], [{"start": 0, "end": 1}]
    )
    assert archived == []
    assert fresh == [a]
    assert umap == {}


def test_psa3_works_with_identity_guard_assert(flag_on):
    from engines.pipeline_integrity.identity_guard import assert_consistent

    a = {
        "segment_id": _sid(30),
        "plain_text": "Keep A",
        "final_tts_text": "Keep A",
    }
    b = {
        "segment_id": _sid(31),
        "plain_text": "Keep B",
        "final_tts_text": "Keep B",
    }
    bind(a, text="Keep A", audio_path="a.wav", stage="post_tts")
    bind(b, text="Keep B", audio_path="b.wav", stage="post_tts")
    report = assert_consistent([a, b], stage="psa3_ok")
    assert report["ok"] is True

    # Move A's bound text onto B while A gets something else
    a["plain_text"] = "Keep B"
    a["final_tts_text"] = "Keep B"
    b["plain_text"] = "Intruder"
    b["final_tts_text"] = "Intruder"
    with pytest.raises((SegmentImmutabilityError, Exception)):
        assert_consistent([a, b], stage="psa3_move")
