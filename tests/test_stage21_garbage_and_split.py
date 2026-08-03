# -*- coding: utf-8 -*-
"""Stage 21: kill garbage expand + aggressive forced split on overflow."""

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
    return " ".join(sentences * 3)


def test_garbage_expand_patterns_blocked():
    from engines.text_slot_fit import is_clean_utterance, is_garbage_expand

    garbage = [
        "Він пішов далі. Саме про Джордж молодший тут ідеться.",
        "Текст. Саме про винятком ідеться.",
        "Саме про камеру тут ідеться",
        "Він пішов на вечерю, Джордж.",
        "Він був серйозним, Вісімнадцятирічний.",
        "Він був серйозним, займався.",
        "Помилка про винятком правила.",
    ]
    for g in garbage:
        assert is_garbage_expand(g) is True, g
        assert is_clean_utterance(g) is False, g


def test_expand_never_produces_same_pro_filler():
    from engines.text_slot_fit import (
        expand_to_fill,
        is_garbage_expand,
        soft_pad_count,
    )

    short = "Джордж молодший любив кіно і мріяв про зйомки."
    out, reasons = expand_to_fill(
        short,
        target_ms=9000,
        lang="uk",
        source_hint="George Junior loved cinema.",
        raw_mt=short,
        prefer_raw=short,
        target_chars=140,
    )
    assert "Саме про" not in out
    assert "тут ідеться" not in out.lower()
    assert is_garbage_expand(out) is False
    assert soft_pad_count(out) <= 1
    assert ", Джордж." not in out
    if out == short:
        assert (
            "stage22:expand_refused" in reasons
            or "stage21:expand_refused" in reasons
            or "blocked" in " ".join(reasons)
        )


def test_strip_garbage_expand_phrases():
    from engines.text_slot_fit import is_garbage_expand, strip_garbage_expand_phrases

    dirty = (
        "Джордж молодший любив кіно. Саме про Джордж молодший тут ідеться."
    )
    clean = strip_garbage_expand_phrases(dirty)
    assert "Саме про" not in clean
    assert "ідеться" not in clean.lower()
    assert is_garbage_expand(clean) is False


def test_force_split_depth_and_fill_band():
    from engines.text_slot_fit import (
        MAX_CHILD_FILL,
        MAX_SPLIT_CHILDREN,
        MAX_SPLIT_DEPTH,
        assert_clean_split_chunks,
        assert_unique_split_chunks,
        estimate_tts_ms,
        force_split_until_fit,
        is_clean_utterance,
        is_garbage_expand,
    )

    assert MAX_CHILD_FILL == 1.12
    assert MAX_SPLIT_DEPTH >= 5
    assert MAX_SPLIT_CHILDREN >= 14

    text = _make_long_uk_paragraph()
    chunks = force_split_until_fit(text, 3500, "uk", max_children=14, depth=0)
    assert len(chunks) >= 3
    assert assert_unique_split_chunks(text, chunks) is True
    assert assert_clean_split_chunks(chunks) is True
    for c in chunks:
        assert c != text
        assert is_clean_utterance(c) is True
        assert is_garbage_expand(c) is False
        assert "Саме про" not in c
        assert len(c.split()) >= 3
        # Soft fill target for children of a ~3.5s pack slot.
        pred = estimate_tts_ms(c, "uk")
        assert pred <= int(7500 * MAX_CHILD_FILL) or len(chunks) >= 2


def test_should_force_split_on_overflow_350():
    from engines.text_slot_fit import should_force_split

    text = "Джордж молодший завжди любив кіно і мріяв про зйомки щодня."
    assert should_force_split(text, 2000, "uk", measured_ms=2500) is True
    assert should_force_split(text, 5000, "uk", measured_ms=5100) is False


def test_repeat_key_never_same_pro():
    from engines.text_slot_fit import _stage19j_repeat_key_phrase, is_garbage_expand

    text = "Джордж молодший любив кіно і мріяв про зйомки."
    out, ok = _stage19j_repeat_key_phrase(text)
    assert "Саме про" not in out
    assert "тут ідеться" not in out.lower()
    if ok:
        assert is_garbage_expand(out) is False
        assert "Джордж молодший" in out


def test_post_restore_split_stage21_metadata():
    from engines.closed_loop_timing import try_stage19e_post_restore_split
    from engines.text_slot_fit import is_clean_utterance, is_garbage_expand

    text = _make_long_uk_paragraph()
    seg = {
        "plain_text": text,
        "text": text,
        "final_text": text,
        "final_tts_text": text,
        "raw_translation": text,
        "semantic_engine_text": text,
        "tts_ms": 90000,
        "playback_duration": 90000,
        "slot_ms": 4000,
        "start_ms": 0,
        "end_ms": 4000,
        "needs_post_restore_split": True,
    }
    segments = [seg]
    sources = [text]
    timing = [{"start": 0, "end": 4000}]
    ok = try_stage19e_post_restore_split(
        segments_data=segments,
        source_segments=sources,
        timing_map=timing,
        audits=[],
        idx=0,
        lang="uk",
    )
    assert ok is True
    assert len(segments) >= 3
    for child in segments:
        t = str(child.get("final_tts_text") or child.get("plain_text") or "")
        assert is_clean_utterance(t) is True
        assert is_garbage_expand(t) is False
        assert "Саме про" not in t
        meta = child.get("stage21") or {}
        assert meta.get("force_split_executed") is True
        assert meta.get("unique_text_ok") is True
        assert meta.get("clean_split_ok") is True
        assert int(meta.get("stage21_split_depth") or 0) >= 1
