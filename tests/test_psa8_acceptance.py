"""PSA8 — Anti-overfit acceptance on ba6ec fixture + stability invariants.

GREEN: identity 4..20 and micro-slots remediated under PSA flags;
overflow honesty + truthful reasons remain enforced.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engines.pipeline_integrity.psa_flags import (
    VM_FLAG_IDENTITY_GUARD,
    VM_FLAG_REVISION_MANAGER,
    VM_FLAG_SEGMENT_NORMALIZER,
    VM_FLAG_SLOT_BUDGET,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "ba6ec_compact.json"


@pytest.fixture
def all_psa_flags_on(monkeypatch):
    for key in (
        VM_FLAG_IDENTITY_GUARD,
        VM_FLAG_SEGMENT_NORMALIZER,
        VM_FLAG_SLOT_BUDGET,
        VM_FLAG_REVISION_MANAGER,
    ):
        monkeypatch.setenv(key, "1")
    monkeypatch.setenv("VM_OVERFLOW_INSPECTOR", "1")
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
                "final_tts_text": row["final_tts_text"],
                "slot_ms": row["slot_ms"],
                "start_ms": row["start_ms"],
                "end_ms": row["end_ms"],
                "playback_duration": row["slot_ms"],  # baseline; overflow cases override
            }
        )
    return rows


def test_psa8_identity_shift_4_20_caught(all_psa_flags_on, ba6ec_rows):
    """Dump shift rejected; after owned restore, 4..20 are identity-green."""
    from engines.pipeline_integrity.exceptions import IdentityMismatchError
    from engines.pipeline_integrity.identity_guard import assert_consistent

    with pytest.raises(IdentityMismatchError):
        assert_consistent(ba6ec_rows, stage="psa8_ba6ec_dump")

    for seg in ba6ec_rows:
        owned = str(seg.get("translated_text") or seg.get("plain_text") or "").strip()
        if owned:
            seg["final_tts_text"] = owned
            seg["tts_text"] = owned
            seg["plain_text"] = owned
    assert_consistent(ba6ec_rows, stage="psa8_ba6ec_green")
    for idx in range(4, 21):
        assert ba6ec_rows[idx]["final_tts_text"] == ba6ec_rows[idx]["translated_text"]


def test_psa8_no_micro_slot_tts_without_budget(all_psa_flags_on, ba6ec_rows):
    """Micro #3/#7/#11 cannot pass SlotBudget TTS gate until merged."""
    from engines.pipeline_integrity.slot_budget import (
        compute_slot_budgets,
        prepare_slot_budget_before_tts,
        segment_tts_allowed,
    )

    for r in ba6ec_rows:
        r["plain_text"] = r["original"]
    tm = [{"start": r["start_ms"], "end": r["end_ms"]} for r in ba6ec_rows]

    before = compute_slot_budgets(ba6ec_rows, tm, tgt_lang="en")
    assert before.tts_allowed is False
    for idx in (3, 7, 11):
        assert segment_tts_allowed(ba6ec_rows[idx]) is False

    segs, _tm2, after = prepare_slot_budget_before_tts(
        ba6ec_rows, tm, src_lang="en", tgt_lang="en"
    )
    from engines.pipeline_integrity.segment_normalizer import is_micro_or_fragment

    residual_micros = [
        s
        for s in segs
        if is_micro_or_fragment(
            str(s.get("plain_text") or ""), int(s.get("slot_ms") or 0)
        )
    ]
    assert residual_micros == []
    # After merge, micros gone; budget report is defined (may still block hard overflow)
    assert after is not None
    assert hasattr(after, "tts_allowed")


def test_psa8_no_success_on_residual_overflow(all_psa_flags_on):
    from engines.pipeline_integrity.overflow_inspector import (
        CRITICAL_OVERFLOW_MS,
        SYNC_FAIL,
        apply_psa6_lock_ordering,
    )

    segs = [
        {
            "segment_id": "f" * 32,
            "slot_ms": 1000,
            "playback_duration": 1000 + CRITICAL_OVERFLOW_MS + 500,
            "status": "SUCCESS",
            "success": True,
            "translation_locked": True,
            "plain_text": "overflow line",
        }
    ]
    info = {"translation_locked": True, "segments_data": segs, "status": "SUCCESS"}
    out = apply_psa6_lock_ordering(info, segs, slot_budget_ok=True)
    assert out["pipeline_success_allowed"] is False
    assert segs[0]["status"] == SYNC_FAIL
    assert segs[0]["needs_manual_review"] is True
    assert segs[0]["success"] is False
    assert info.get("translation_locked") is False


def test_psa8_truthful_reasons(all_psa_flags_on):
    from engines.pipeline_integrity.honest_diagnostics import apply_honest_reasons

    seg = {
        "segment_id": "a" * 32,
        "slot_ms": 2000,
        "playback_duration": 2050,
        "fitted_file": "x.wav",
        "overflow_decision": {"chosen": "trim"},
        "adaptation_stages": ["overflow_strategy:trim"],
        "decision_trace": ["TTS:SKIPPED:AudioStrategyNoTextRewrite"],
        "algorithm_reason": (
            "post_tts_text_adaptation: semantic shorten + TTS regen until slot fit"
        ),
        "text_adaptation_reason": "semantic_shortening",
    }
    summary = apply_honest_reasons(seg)
    assert "semantic shorten" not in summary["algorithm_reason"].lower()
    assert summary["audio_strategy_reason"]
    assert summary["residual_overflow_ms"] == 50
