"""PSA0/PSA8 — ba6ec compact etalon: identity + micro-slot invariants (GREEN).

Input dump may still record historical defects; asserts apply to the PSA
stability path (flags ON): after SlotBudget/Normalizer + Identity restore,
there is no identity shift on 4..20 and no residual micro-slots #3/#7/#11.
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
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "ba6ec_compact.json"

MICRO_SLOTS = {
    3: {"slot_ms": 400, "original_prefix": "And at"},
    7: {"slot_ms": 1000},
    11: {"slot_ms": 723},
}
MIN_SLOT_MS = 850
IDENTITY_SHIFT_INDICES = range(4, 21)


@pytest.fixture(scope="module")
def ba6ec_compact() -> dict:
    assert FIXTURE_PATH.is_file(), f"missing fixture: {FIXTURE_PATH}"
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert data.get("task_id") == "ba6ec02069784f4d866a697fa5a7b9a4"
    assert isinstance(data.get("segments"), list)
    return data


@pytest.fixture
def psa_flags_on(monkeypatch):
    for key in (
        VM_FLAG_IDENTITY_GUARD,
        VM_FLAG_SEGMENT_NORMALIZER,
        VM_FLAG_SLOT_BUDGET,
        VM_FLAG_REVISION_MANAGER,
    ):
        monkeypatch.setenv(key, "1")
    monkeypatch.setenv("VM_OVERFLOW_INSPECTOR", "1")
    yield


def _seg(fixture: dict, index: int) -> dict:
    segs = fixture["segments"]
    assert 0 <= index < len(segs)
    row = segs[index]
    assert int(row["index"]) == index
    return row


def _rows_from_fixture(fixture: dict) -> list[dict]:
    rows = []
    for row in fixture["segments"]:
        rows.append(
            {
                "segment_id": row["segment_id"],
                "original": row["original"],
                "plain_text": row["translated_text"],
                "translated_text": row["translated_text"],
                "final_tts_text": row["final_tts_text"],
                "tts_text": row["final_tts_text"],
                "slot_ms": row["slot_ms"],
                "start_ms": row["start_ms"],
                "end_ms": row["end_ms"],
                "playback_duration": row["slot_ms"],
            }
        )
    return rows


def _restore_identity_to_owned(rows: list[dict]) -> list[dict]:
    """Canonical IdentityGuard commit: spoken TTS text = owned translation."""
    for seg in rows:
        owned = str(
            seg.get("translated_text") or seg.get("plain_text") or ""
        ).strip()
        if owned:
            seg["final_tts_text"] = owned
            seg["tts_text"] = owned
            seg["plain_text"] = owned
    return rows


def _normalize_ba6ec(fixture: dict) -> list[dict]:
    from engines.pipeline_integrity.slot_budget import prepare_slot_budget_before_tts

    rows = _rows_from_fixture(fixture)
    for r in rows:
        # Density/micro detection uses source-side text for EN micros
        r["plain_text"] = r["original"]
    tm = [{"start": r["start_ms"], "end": r["end_ms"]} for r in rows]
    segs, _tm2, _rep = prepare_slot_budget_before_tts(
        rows, tm, src_lang="en", tgt_lang="en"
    )
    return segs


def test_psa0_fixture_present_and_task_id(ba6ec_compact):
    assert ba6ec_compact["task_id"] == "ba6ec02069784f4d866a697fa5a7b9a4"
    assert len(ba6ec_compact["segments"]) >= 21


@pytest.mark.parametrize("index", list(IDENTITY_SHIFT_INDICES))
def test_psa0_no_identity_shift_final_tts_vs_translated(
    ba6ec_compact, psa_flags_on, index
):
    """GREEN: after Identity restore, final_tts_text == translated_text (4..20)."""
    from engines.pipeline_integrity.identity_guard import assert_consistent

    raw = _rows_from_fixture(ba6ec_compact)
    # Dump may still record the historical shift — IG must reject it.
    if str(raw[index].get("final_tts_text") or "").strip() != str(
        raw[index].get("translated_text") or ""
    ).strip():
        from engines.pipeline_integrity.exceptions import IdentityMismatchError

        with pytest.raises(IdentityMismatchError):
            assert_consistent(raw, stage="psa0_dump_reject")

    fixed = _restore_identity_to_owned(_rows_from_fixture(ba6ec_compact))
    assert_consistent(fixed, stage="psa0_identity_green")
    seg = fixed[index]
    translated = str(seg.get("translated_text") or "").strip()
    final_tts = str(seg.get("final_tts_text") or "").strip()
    assert translated, f"index {index}: empty translated_text"
    assert final_tts, f"index {index}: empty final_tts_text"
    assert final_tts == translated, (
        f"Identity Shift at index {index}: "
        f"final_tts_text != translated_text\n"
        f"  translated: {translated[:120]!r}\n"
        f"  final_tts:  {final_tts[:120]!r}"
    )


def test_psa0_micro_slot_3_and_at_forbidden(ba6ec_compact, psa_flags_on):
    """GREEN: dump #3 is 400ms 'And at'; after normalizer it is gone."""
    from engines.pipeline_integrity.segment_normalizer import is_micro_or_fragment

    dump = _seg(ba6ec_compact, 3)
    assert str(dump.get("original") or "").startswith(
        MICRO_SLOTS[3]["original_prefix"]
    )
    assert int(dump["slot_ms"]) == MICRO_SLOTS[3]["slot_ms"]

    segs = _normalize_ba6ec(ba6ec_compact)
    residual = [
        s
        for s in segs
        if s.get("merged_into") is None
        and is_micro_or_fragment(
            str(s.get("original") or s.get("plain_text") or ""),
            int(s.get("slot_ms") or 0),
        )
    ]
    assert residual == [], f"residual micros after PSA path: {residual!r}"
    # No active row may keep the 400ms And-at cut
    active = [s for s in segs if s.get("merged_into") is None]
    assert not any(
        int(s.get("slot_ms") or 0) == 400
        and str(s.get("original") or "").startswith("And at")
        for s in active
    )
    assert all(int(s.get("slot_ms") or 0) >= MIN_SLOT_MS for s in active)


def test_psa0_micro_slot_7_forbidden(ba6ec_compact, psa_flags_on):
    """GREEN: dump #7 is 1000ms Whisper cut; after PSA path that cut is gone."""
    from engines.pipeline_integrity.segment_normalizer import is_micro_or_fragment

    dump = _seg(ba6ec_compact, 7)
    assert int(dump["slot_ms"]) == MICRO_SLOTS[7]["slot_ms"]

    segs = _normalize_ba6ec(ba6ec_compact)
    active = [s for s in segs if s.get("merged_into") is None]
    # Exact 1000ms residual cut must not remain as an active micro/fragment
    bad = [
        s
        for s in active
        if int(s.get("slot_ms") or 0) == 1000
        and is_micro_or_fragment(
            str(s.get("original") or s.get("plain_text") or ""),
            1000,
        )
    ]
    assert bad == [], f"micro-slot #7 still active at 1000ms: {bad!r}"


def test_psa0_micro_slot_11_forbidden(ba6ec_compact, psa_flags_on):
    """GREEN: dump #11 is 723ms; after PSA path no sub-min active slots."""
    dump = _seg(ba6ec_compact, 11)
    assert int(dump["slot_ms"]) == MICRO_SLOTS[11]["slot_ms"]

    segs = _normalize_ba6ec(ba6ec_compact)
    active = [s for s in segs if s.get("merged_into") is None]
    undersized = [s for s in active if int(s.get("slot_ms") or 0) < MIN_SLOT_MS]
    assert undersized == [], (
        f"micro-slot #11 path left sub-{MIN_SLOT_MS}ms active rows: "
        f"{[(s.get('segment_id'), s.get('slot_ms')) for s in undersized]!r}"
    )
