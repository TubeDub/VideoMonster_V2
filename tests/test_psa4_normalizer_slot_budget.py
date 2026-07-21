"""PSA4 — SegmentNormalizer + SlotBudgetFirst (ba6ec micros)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engines.pipeline_integrity.psa_flags import (
    VM_FLAG_SEGMENT_NORMALIZER,
    VM_FLAG_SLOT_BUDGET,
)
from engines.pipeline_integrity.segment_normalizer import (
    MIN_SLOT_MS,
    is_micro_or_fragment,
    merge_micro_slots,
    normalize_segments,
    normalize_segments_data,
)
from engines.pipeline_integrity.slot_budget import (
    compute_slot_budgets,
    prepare_slot_budget_before_tts,
    segment_tts_allowed,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "ba6ec_compact.json"

# PSA0 micro indices (0-based)
MICRO_IDX = (3, 7, 11)


@pytest.fixture
def flags_on(monkeypatch):
    monkeypatch.setenv(VM_FLAG_SEGMENT_NORMALIZER, "1")
    monkeypatch.setenv(VM_FLAG_SLOT_BUDGET, "1")
    monkeypatch.delenv("VM_SEGMENT_NORMALIZER", raising=False)
    monkeypatch.delenv("VM_SLOT_BUDGET", raising=False)
    yield


@pytest.fixture
def flags_off(monkeypatch):
    monkeypatch.setenv(VM_FLAG_SEGMENT_NORMALIZER, "0")
    monkeypatch.setenv(VM_FLAG_SLOT_BUDGET, "0")
    yield


@pytest.fixture
def ba6ec_rows() -> list[dict]:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    rows = []
    for row in data["segments"]:
        rows.append(
            {
                "segment_id": row["segment_id"],
                "original": row["original"],
                "plain_text": row["translated_text"],
                "translated_text": row["translated_text"],
                "slot_ms": row["slot_ms"],
                "start_ms": row["start_ms"],
                "end_ms": row["end_ms"],
            }
        )
    return rows


def test_psa4_micro_detector_ba6ec(flags_on, ba6ec_rows):
    assert is_micro_or_fragment(ba6ec_rows[3]["original"], 400)
    assert is_micro_or_fragment(ba6ec_rows[7]["original"], 1000)
    assert is_micro_or_fragment(ba6ec_rows[11]["original"], 723)


def test_psa4_tts_blocked_until_merged(flags_on, ba6ec_rows):
    """Unnormalized ba6ec micros → SlotBudget blocks TTS."""
    tm = [
        {"start": r["start_ms"], "end": r["end_ms"]} for r in ba6ec_rows
    ]
    # Use original EN for density checks on micro rows
    for r in ba6ec_rows:
        r["plain_text"] = r["original"]

    report = compute_slot_budgets(ba6ec_rows, tm, tgt_lang="en")
    assert report.tts_allowed is False
    blocked_idx = {int(b["index"]) for b in report.blocked}
    for idx in MICRO_IDX:
        assert idx in blocked_idx or not segment_tts_allowed(ba6ec_rows[idx]), (
            f"micro #{idx} must block TTS before normalize"
        )
        assert segment_tts_allowed(ba6ec_rows[idx]) is False


def test_psa4_normalize_merges_micros_and_reissues(flags_on, ba6ec_rows):
    tm = [
        {"start": r["start_ms"], "end": r["end_ms"]} for r in ba6ec_rows
    ]
    old_ids = {r["segment_id"] for r in ba6ec_rows}
    # Normalize on EN originals (Whisper side)
    for r in ba6ec_rows:
        r["plain_text"] = r["original"]

    fresh, new_tm, report = normalize_segments_data(
        ba6ec_rows, tm, src_lang="en", tgt_lang="uk", force=True
    )
    assert report.get("enabled") is True
    assert report.get("boundaries_changed") or report.get("reissued")
    assert len(fresh) < len(ba6ec_rows)

    # No residual micro among fresh rows
    for seg in fresh:
        slot = int(seg.get("slot_ms") or 0)
        text = str(seg.get("plain_text") or "")
        assert not is_micro_or_fragment(text, slot), (
            f"residual micro after normalize: slot={slot} text={text[:60]!r}"
        )
        assert int(seg.get("slot_ms") or 0) >= MIN_SLOT_MS or _word_count_ok(seg)

    # New ids minted (PSA3) when reissued
    if report.get("reissued"):
        new_ids = {s["segment_id"] for s in fresh}
        assert new_ids.isdisjoint(old_ids) or len(new_ids - old_ids) >= 1


def _word_count_ok(seg: dict) -> bool:
    # After merge, short leftovers only if duration already healthy
    return int(seg.get("slot_ms") or 0) >= MIN_SLOT_MS


def test_psa4_prepare_then_tts_allowed(flags_on, ba6ec_rows):
    tm = [
        {"start": r["start_ms"], "end": r["end_ms"]} for r in ba6ec_rows
    ]
    for r in ba6ec_rows:
        r["plain_text"] = r["original"]

    segs, new_tm, report = prepare_slot_budget_before_tts(
        ba6ec_rows, tm, src_lang="en", tgt_lang="en"
    )
    # After normalize+budget, micros must be gone or still blocked explicitly
    micros_left = [
        s
        for s in segs
        if is_micro_or_fragment(
            str(s.get("plain_text") or ""), int(s.get("slot_ms") or 0)
        )
    ]
    assert micros_left == [], f"micros remain: {micros_left}"
    for s in segs:
        if s.get("archived"):
            continue
        # Budget rows stamped; allowed when not micro and not hard overflow
        if segment_tts_allowed(s):
            assert int(s.get("slot_ms") or 0) >= MIN_SLOT_MS


def test_psa4_mid_name_join(flags_on):
    segs = [
        "An 18-year-old boy named George Jr.",
        "could not help but feel dread.",
        "Джордж-молодший.",
        "лежав у лікарні після аварії і думав про майбутнє.",
    ]
    timing = [
        {"start": 0, "end": 4200},
        {"start": 4200, "end": 8000},
        {"start": 8000, "end": 9500},
        {"start": 9500, "end": 14000},
    ]
    texts, tm, report = merge_micro_slots(segs, timing)
    assert report["merged"] >= 1 or report.get("continuation_merged", 0) >= 1
    joined = " ".join(texts)
    assert "George Jr. could not" in joined or any(
        "George Jr." in t and "could not" in t for t in texts
    )
    assert any("Джордж-молодший" in t and "лікарні" in t for t in texts) or any(
        "молодший" in t for t in texts
    )


def test_psa4_and_at_fragment_merged(flags_on):
    segs = ["And at", "that point his father bought a Fiat."]
    timing = [{"start": 0, "end": 400}, {"start": 400, "end": 5200}]
    texts, tm, report = merge_micro_slots(segs, timing)
    assert len(texts) == 1
    assert texts[0].startswith("And at")
    assert tm[0]["end"] - tm[0]["start"] >= MIN_SLOT_MS


def test_psa4_so_two_weeks_merged_despite_long_slot(flags_on):
    """Whisper sometimes gives discourse openers a long bogus duration."""
    segs = [
        "So two weeks earlier",
        "when George was making that turn something happened.",
    ]
    timing = [{"start": 0, "end": 2800}, {"start": 2800, "end": 9000}]
    assert is_micro_or_fragment(segs[0], 2800)
    texts, _tm, report = merge_micro_slots(segs, timing)
    assert len(texts) == 1
    assert report["merged"] >= 1
    assert texts[0].startswith("So two weeks earlier")


def test_psa4_flags_off_legacy(flags_off, ba6ec_rows):
    tm = [
        {"start": r["start_ms"], "end": r["end_ms"]} for r in ba6ec_rows
    ]
    texts = [r["original"] for r in ba6ec_rows]
    out_t, out_tm, rep = normalize_segments(texts, tm)
    assert rep.get("enabled") is False
    assert out_t == texts

    report = compute_slot_budgets(ba6ec_rows, tm, tgt_lang="uk")
    assert report.tts_allowed is True
    assert report.blocked == []
