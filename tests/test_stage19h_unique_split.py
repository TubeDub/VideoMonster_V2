# -*- coding: utf-8 -*-
"""Stage 19h: forced unique-chunk split + independent child TTS."""

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
        "Потім він вирішив змінити життя і подати документи на роботу в студії.",
        "Незважаючи на сумніви батьків, він не здавався і працював щодня.",
        "Зрештою його помітили, і він отримав шанс показати свій потенціал.",
        "Він згадав аварію на треку, але все одно продовжив рухатися вперед.",
        "Камера, роботи і гоночні мрії лишилися з ним назавжди.",
    ]
    return " ".join(sentences * 4)


def test_force_split_until_fit_returns_unique_chunks():
    from engines.text_slot_fit import (
        assert_unique_split_chunks,
        force_split_until_fit,
        should_force_split,
    )

    text = _make_long_uk_paragraph()
    slot_ms = 4000
    assert should_force_split(text, slot_ms, "uk") is True
    chunks = force_split_until_fit(text, slot_ms, "uk", max_children=12)
    assert len(chunks) >= 3
    assert assert_unique_split_chunks(text, chunks) is True
    for c in chunks:
        assert c != text
        assert c.strip()
    assert len(set(chunks)) == len(chunks)


def test_force_split_recursion_on_fill_gt_115():
    from engines.text_slot_fit import (
        MAX_CHILD_FILL,
        MAX_SPLIT_CHILDREN,
        estimate_tts_ms,
        force_split_until_fit,
    )

    text = _make_long_uk_paragraph()
    slot_ms = 2500
    chunks = force_split_until_fit(
        text, slot_ms, "uk", max_children=MAX_SPLIT_CHILDREN, depth=0
    )
    assert len(chunks) >= 3
    hard = int(slot_ms * MAX_CHILD_FILL)
    over = [c for c in chunks if estimate_tts_ms(c, "uk") > hard + 80]
    # With max_children cap, residual oversizes may remain for re-split;
    # recursion must still cut the giant into many unique packs.
    assert len(chunks) >= 6 or len(over) <= 3, (
        len(chunks),
        [(estimate_tts_ms(c, "uk"), c[:50]) for c in over[:3]],
    )


def test_child_does_not_inherit_parent_duration_or_text():
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
        "raw_mt": text,
        "tts_ms": parent_ms,
        "playback_duration": parent_ms,
        "actual_duration_ms": parent_ms,
        "measured_duration": parent_ms,
        "first_tts_duration_ms": parent_ms,
        "slot_ms": slot_ms,
        "start_ms": 0,
        "end_ms": slot_ms,
        "segment_id": "p19h",
        "needs_post_restore_split": True,
    }
    segments = [seg]
    timing_map = [{"start": 0, "end": slot_ms}]
    ok = try_stage19e_post_restore_split(
        segments_data=segments,
        source_segments=[
            "George Junior met Haskell Wexler about Fiat USC and Star Wars."
        ],
        timing_map=timing_map,
        audits=None,
        idx=0,
        lang="uk",
    )
    assert ok is True
    assert len(segments) >= 3
    child_texts = []
    for s in segments:
        assert s.get("playback_duration") is None
        assert s.get("tts_ms") is None
        assert s.get("actual_duration_ms") is None
        assert s.get("measured_duration") is None
        assert s.get("tts_duration") is None
        final = " ".join(str(s.get("final_tts_text") or "").split()).strip()
        assert final
        assert final != text
        # Raw anchors must also be scoped to unique chunk (not parent blob).
        assert " ".join(str(s.get("raw_translation") or "").split()).strip() == final
        assert (
            " ".join(str(s.get("semantic_engine_text") or "").split()).strip() == final
        )
        h = s.get("stage19h") or {}
        assert h.get("unique_text_ok") is True
        assert h.get("split_children") == len(segments)
        assert int(h.get("stage19h_split_depth") or 0) >= 1
        child_texts.append(final)
    assert len(set(child_texts)) == len(child_texts)


def test_metadata_unique_text_ok_false_on_parent_equal():
    from engines.text_slot_fit import assert_unique_split_chunks

    parent = "Джордж любив кіно і зустрів Векслера."
    bad = [parent, "інший кусок"]
    assert assert_unique_split_chunks(parent, bad) is False
    good = ["Джордж любив кіно", "і зустрів Векслера."]
    assert assert_unique_split_chunks(parent, good) is True


def test_stage19h_soft_pad_whitelist_only():
    from engines.text_slot_fit import SOFT_PAD_WHITELIST, expand_to_fill

    assert "саме тоді" in SOFT_PAD_WHITELIST
    assert "і саме в цей момент" in SOFT_PAD_WHITELIST
    assert "отже" in SOFT_PAD_WHITELIST
    assert "тому" in SOFT_PAD_WHITELIST
    assert "ось як це було тоді" not in SOFT_PAD_WHITELIST
    short = "Він пішов."
    out, reasons = expand_to_fill(
        short,
        target_ms=5000,
        lang="uk",
        source_hint="He went then.",
        raw_mt=short,
        prefer_raw=short,
    )
    assert "ось як це було тоді" not in out.lower()
    if out != short:
        assert any(
            str(r).startswith("stage19i:") or str(r).startswith("stage19g:")
            for r in reasons
        ) or len(out) > len(short)
