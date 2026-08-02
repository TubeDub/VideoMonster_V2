# -*- coding: utf-8 -*-
"""Stage 19i: CPS budget + bounded expand/split + atempo band."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _make_long_uk_paragraph() -> str:
    sentences = [
        "Джордж молодший завжди любив кіно і мріяв потрапити на знімальний майданчик.",
        "Він зустрів Хаскелла Векслера і розповів про свою камеру та старий Фіат.",
        "Ще з дитинства він дивився Зоряні війни і вірив у силу фантазії.",
        "Потім він вирішив змінити життя і подати документи на роботу в студії USC.",
        "Незважаючи на сумніви батьків, він не здавався і працював щодня.",
        "Зрештою його помітили, і він отримав шанс показати свій потенціал.",
        "Він згадав аварію на треку, але все одно продовжив рухатися вперед.",
        "Камера, роботи і гоночні мрії лишилися з ним назавжди.",
    ]
    return " ".join(sentences * 4)


def test_char_budget_and_estimated_cps():
    from engines.text_slot_fit import (
        MAX_CPS_UK,
        MIN_CPS_UK,
        TARGET_CPS_UK,
        char_budget,
        estimated_cps,
    )

    assert char_budget(1000, TARGET_CPS_UK) == 20  # floor max(20, 14)
    assert char_budget(5000) == int(5.0 * TARGET_CPS_UK)
    text = "А" * 70  # 70 non-space chars
    cps = estimated_cps(text, 5000)
    assert MIN_CPS_UK <= cps <= MAX_CPS_UK
    assert estimated_cps(text, 0) == 0.0


def test_force_split_production_child_size_and_unique():
    from engines.text_slot_fit import (
        MAX_CHILD_FILL,
        MAX_CHILD_SLOT_MS,
        MIN_CHILD_SLOT_MS,
        assert_unique_split_chunks,
        estimate_tts_ms,
        force_split_until_fit,
    )

    text = _make_long_uk_paragraph()
    chunks = force_split_until_fit(text, 4000, "uk", max_children=10)
    assert len(chunks) >= 4
    assert assert_unique_split_chunks(text, chunks) is True
    for c in chunks:
        assert c != text
        pred = estimate_tts_ms(c, "uk")
        # Aimed at production chunk; allow recursion leftovers under hard fill.
        assert pred <= int(MAX_CHILD_SLOT_MS * MAX_CHILD_FILL) + 200 or len(chunks) >= 8
        assert pred >= MIN_CHILD_SLOT_MS * 0.35 or len(c.split()) < 8


def test_soft_pad_at_most_one():
    from engines.text_slot_fit import (
        MAX_SOFT_PADS_PER_SEG,
        SOFT_PAD_WHITELIST,
        expand_to_fill,
        soft_pad_count,
    )

    assert MAX_SOFT_PADS_PER_SEG == 1
    assert "тому" in SOFT_PAD_WHITELIST
    short = "Він пішов далі."
    out, _ = expand_to_fill(
        short,
        target_ms=6000,
        lang="uk",
        source_hint="He went further then.",
        raw_mt=short,
        prefer_raw=short,
        target_chars=80,
        strategy_order=("soft_pad_once",),
    )
    assert soft_pad_count(out) <= 1
    # Second expand must not stack another pad.
    out2, _ = expand_to_fill(
        out,
        target_ms=6000,
        lang="uk",
        source_hint="He went further then.",
        raw_mt=out,
        prefer_raw=out,
        target_chars=120,
        strategy_order=("soft_pad_once",),
    )
    assert soft_pad_count(out2) <= 1


def test_expand_requires_real_char_growth():
    from engines.text_slot_fit import clean_text_chars, expand_to_fill

    text = "Ок."
    out, reasons = expand_to_fill(
        text,
        target_ms=8000,
        lang="uk",
        source_hint="",
        raw_mt=text,
        prefer_raw=text,
        target_chars=100,
    )
    if out == text:
        assert "stage19i:text_grown" not in reasons
    else:
        assert len(clean_text_chars(out)) > len(clean_text_chars(text))


def test_split_children_independent_no_parent_duration_stage19i():
    from engines.closed_loop_timing import try_stage19e_post_restore_split

    text = _make_long_uk_paragraph()
    slot_ms = 4000
    parent_ms = 90000
    seg = {
        "plain_text": text,
        "text": text,
        "final_text": text,
        "final_tts_text": text,
        "raw_translation": text,
        "semantic_engine_text": text,
        "tts_ms": parent_ms,
        "playback_duration": parent_ms,
        "measured_duration": parent_ms,
        "slot_ms": slot_ms,
        "start_ms": 0,
        "end_ms": slot_ms,
        "segment_id": "p19i",
        "needs_post_restore_split": True,
    }
    segments = [seg]
    ok = try_stage19e_post_restore_split(
        segments_data=segments,
        source_segments=["George Junior met Haskell Wexler at USC about Star Wars."],
        timing_map=[{"start": 0, "end": slot_ms}],
        audits=None,
        idx=0,
        lang="uk",
    )
    assert ok is True
    assert len(segments) >= 4
    texts = []
    for s in segments:
        assert s.get("tts_ms") is None
        assert s.get("playback_duration") is None
        assert s.get("measured_duration") is None
        final = " ".join(str(s.get("final_tts_text") or "").split()).strip()
        assert final != text
        meta = s.get("stage19i") or {}
        assert meta.get("unique_text_ok") is True
        assert int(meta.get("soft_pad_count") or 0) <= 1
        assert int(meta.get("char_budget") or 0) > 0
        texts.append(final)
    assert len(set(texts)) == len(texts)


def test_atempo_bounds_constants():
    from engines.text_slot_fit import ATEMPO_MAX, ATEMPO_MIN, suggested_atempo_for_fill

    assert ATEMPO_MIN == 0.90
    assert ATEMPO_MAX == 1.20
    slow = suggested_atempo_for_fill(3000, 4000)
    assert ATEMPO_MIN <= slow <= 1.0
    fast = suggested_atempo_for_fill(4500, 4000)
    assert 1.0 <= fast <= ATEMPO_MAX
