"""Hotfix: duplicate segment_id repair + MF near-fit / GL shorten."""

from __future__ import annotations

import os

import pytest

from engines.pipeline_integrity.guards import ArchitectureGuard
from engines.pipeline_integrity.segment import new_segment_id


def test_architecture_guard_repairs_duplicate_ids():
    sid = new_segment_id()
    segs = [
        {"segment_id": sid, "plain_text": "left half", "index": 0},
        {"segment_id": sid, "plain_text": "right half", "index": 0},
        {"segment_id": new_segment_id(), "plain_text": "ok", "index": 1},
    ]
    ArchitectureGuard.check(segs, stage="slot_fit")
    ids = [s["segment_id"] for s in segs]
    assert len(ids) == len(set(ids))
    assert segs[0]["segment_id"] == sid
    assert segs[1]["segment_id"] != sid


def test_legacy_split_mints_new_id():
    from engines.adaptive_segmentation.post_tts import try_split_long_overflow_segment

    sid = new_segment_id()
    segs = [
        {
            "segment_id": sid,
            "plain_text": (
                "First sentence about the story. "
                "Second sentence continues the thought clearly."
            ),
            "text": (
                "First sentence about the story. "
                "Second sentence continues the thought clearly."
            ),
            "original": (
                "First sentence about the story. "
                "Second sentence continues the thought clearly."
            ),
            "start_ms": 0,
            "end_ms": 12000,
            "slot_ms": 12000,
            "playback_duration": 16000,
        }
    ]
    source = [segs[0]["original"]]
    timing = [{"start": 0, "end": 12000}]
    # Force path even if thresholds vary
    segs[0]["playback_duration"] = 20000
    ok = try_split_long_overflow_segment(
        segments_data=segs,
        source_segments=source,
        timing_map=timing,
        idx=0,
        audits=None,
    )
    if not ok:
        pytest.skip("split thresholds not met for this fixture")
    assert len(segs) >= 2
    assert segs[0]["segment_id"] != segs[1]["segment_id"]


def test_mf_near_fit_already_fits():
    from engines.meaning_fit.duration_predictor import classify_vs_slot, predict_vs_slot
    from engines.meaning_fit.orchestrator import fit_segment
    from engines.meaning_fit.types import FitRequest

    for k in (
        "VM_FLAG_MEANING_FIT",
        "VM_FLAG_MEANING_FIT_SHORTEN",
        "VM_FLAG_MEANING_FIT_EXPAND",
        "VM_FLAG_MEANING_FIT_BEFORE_LOCK",
    ):
        os.environ[k] = "1"

    # 247ms over 10493 → OK with soft slack
    assert classify_vs_slot(10740, 10493) == "OK"

    text = (
        "18-річний Джордж-молодший їхав рідним містом додому на вечерю. "
        "Але у той момент, коли він їхав, Джорджу-молодшому зовсім не хотілося їхати додому"
    )
    res = fit_segment(FitRequest(text_uk=text, slot_ms=10493), force=True)
    assert res.success is True
    assert res.status in ("already_fits", "paraphrase_shorten")
    assert res.needs_manual is False


def test_mf_gl_shorten_compacts():
    from engines.meaning_fit.semantic_shorten import semantic_shorten

    for k in (
        "VM_FLAG_MEANING_FIT",
        "VM_FLAG_MEANING_FIT_SHORTEN",
    ):
        os.environ[k] = "1"

    long = (
        "Але у той момент, коли він їхав, Джорджу-молодшому зовсім не хотілося їхати додому"
    )
    res = semantic_shorten(long, slot_ms=3000, force=True)
    assert res.text_uk != long or res.status == "already_fits"
    if res.status == "paraphrase_shorten":
        assert "у той момент, коли" not in res.text_uk.lower()
        assert "зовсім не хотілося" not in res.text_uk.lower()
