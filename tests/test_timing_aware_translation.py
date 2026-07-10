"""Tests — Timing-Aware Translation stage."""

from __future__ import annotations

from engines.timing_aware_translation import (
    adapt_segment_to_slot,
    adapt_segments_to_timing,
    slot_ms_from_timing,
    word_count,
)


def test_slot_ms_from_timing_dict():
    timing = [{"start": 1000, "end": 4500}]
    assert slot_ms_from_timing(timing, 0) == 3500


def test_adapt_segment_skips_when_fits():
    short = "Коротко."
    adapted, rec = adapt_segment_to_slot(
        short,
        source_text="Hi.",
        slot_ms=5000,
        src_lang="en",
        tgt_lang="ru",
        index=0,
    )
    assert adapted == short
    assert rec.adapted is False
    assert rec.reason == "fits_no_change"


def test_adapt_segments_to_timing_long_text():
    timing = [{"start": 0, "end": 2000}]
    long_text = " ".join(["слово"] * 30)
    out, records = adapt_segments_to_timing(
        [long_text],
        timing,
        ["word " * 10],
        src_lang="en",
        tgt_lang="ru",
        task_id="t1",
    )
    assert len(out) == 1
    assert len(records) == 1
    assert word_count(out[0]) <= word_count(long_text)


def test_apply_records_to_audits_updates_final():
    from engines.timing_aware_translation import (
        TimingAwareRecord,
        apply_records_to_audits,
    )

    audits = [{"index": 0, "final_text": "old", "quality_details": {}}]
    rec = TimingAwareRecord(
        index=0,
        text_before="old long text",
        text_after="new short",
        adapted=True,
        slot_ms=3000,
        predicted_ms_before=5000,
        predicted_ms_after=2500,
    )
    apply_records_to_audits(audits, [rec])
    assert audits[0]["final_text"] == "new short"
    assert audits[0]["quality_details"]["timing_aware"]["text_after"] == "new short"
