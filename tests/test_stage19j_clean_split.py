# -*- coding: utf-8 -*-
"""Stage 19j: clean sentence/clause split + safe expand (no garbage)."""

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


def test_forbidden_garbage_patterns_detected():
    from engines.text_slot_fit import has_forbidden_expand_pattern, is_clean_utterance

    garbage = [
        "Він пішов на вечерю, Джордж.",
        "Він хотів потрапити туди, Джордж.",
        "Він відволікся, Джордж.",
        "Він був серйозним, Вісімнадцятирічний, займався.",
        "Він був серйозним, Вісімнадцятирічний.",
    ]
    for g in garbage:
        assert has_forbidden_expand_pattern(g) is True, g
        assert is_clean_utterance(g) is False, g


def test_expand_never_appends_single_word_crumb():
    from engines.text_slot_fit import (
        expand_to_fill,
        has_forbidden_expand_pattern,
        is_clean_utterance,
    )

    short = "Він пішов далі дорогою."
    out, reasons = expand_to_fill(
        short,
        target_ms=8000,
        lang="uk",
        source_hint="George Junior walked further.",
        raw_mt=short,
        prefer_raw=short,
        target_chars=120,
    )
    assert has_forbidden_expand_pattern(out) is False
    if out != short:
        assert is_clean_utterance(out) is True
        assert "stage19j:text_grown" in reasons
    # Classic garbage must never be produced.
    assert ", Джордж." not in out
    assert "Вісімнадцятирічний" not in out or is_clean_utterance(out)


def test_force_split_returns_clean_sentences():
    from engines.text_slot_fit import (
        assert_clean_split_chunks,
        assert_unique_split_chunks,
        force_split_until_fit,
        has_forbidden_expand_pattern,
        is_clean_utterance,
    )

    text = _make_long_uk_paragraph()
    chunks = force_split_until_fit(text, 4000, "uk", max_children=10)
    assert len(chunks) >= 3
    assert assert_unique_split_chunks(text, chunks) is True
    assert assert_clean_split_chunks(chunks) is True
    for c in chunks:
        assert c != text
        assert is_clean_utterance(c) is True
        assert has_forbidden_expand_pattern(c) is False
        # Must not be a bare word crumb.
        assert len(c.split()) >= 3
        assert not c.rstrip(".!?…").endswith(",")
        assert not c.endswith(",")


def test_repeat_key_noun_no_longer_makes_garbage():
    from engines.text_slot_fit import _stage19g_repeat_key_noun, has_forbidden_expand_pattern

    text = "Джордж молодший любив кіно і мріяв про зйомки."
    out, ok = _stage19g_repeat_key_noun(text)
    if ok:
        assert has_forbidden_expand_pattern(out) is False
        assert ", Джордж." not in out
        assert out.count(".") >= 1


def test_post_restore_split_clean_metadata():
    from engines.closed_loop_timing import try_stage19e_post_restore_split
    from engines.text_slot_fit import has_forbidden_expand_pattern, is_clean_utterance

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
        "segment_id": "p19j",
        "needs_post_restore_split": True,
    }
    segments = [seg]
    ok = try_stage19e_post_restore_split(
        segments_data=segments,
        source_segments=["George Junior met Haskell Wexler about Star Wars."],
        timing_map=[{"start": 0, "end": 4000}],
        audits=None,
        idx=0,
        lang="uk",
    )
    assert ok is True
    assert len(segments) >= 3
    for s in segments:
        final = " ".join(str(s.get("final_tts_text") or "").split()).strip()
        assert is_clean_utterance(final) is True
        assert has_forbidden_expand_pattern(final) is False
        meta = s.get("stage19j") or {}
        assert meta.get("clean_split_ok") is True
        assert meta.get("unique_text_ok") is True
        assert ", Джордж." not in final
        assert not final.endswith(", Вісімнадцятирічний.")
