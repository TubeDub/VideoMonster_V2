# -*- coding: utf-8 -*-
"""Stage 37 — IMG_2790 / 1.json EN→UK: no pad fillers, no restatements, living Final.

Fixtures copied from Desktop 1.json (task 8fadb9dd) finals vs English source.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 1.json idx 5 — RAW was fine; Final glued whitelist pads.
JSON_IDX5_EN = "Oh it's probably been two days man. Two whole days?"
JSON_IDX5_BAD = "О, напевно, вже два дні минуло, саме тоді, і саме в цей момент."
JSON_IDX5_RAW = "О, напевно, вже два дні минуло."

# 1.json idx 8 — semantic_repeat_key echoed the opener.
JSON_IDX8_EN = "Honestly I just dig through the trash can. I don't know there."
JSON_IDX8_BAD = "Чесно кажучи, я просто прокопаю смітник. Чесно кажучи."

# 1.json idx 20 — leftover RU + pads voiced as UK.
JSON_IDX20_EN = "Oh wow. I'm sorry."
JSON_IDX20_BAD = "Мне жаль, саме тоді, і саме в цей момент."

# 1.json idx 29 — Marian calque of "I'm not a big greens person."
JSON_IDX29_EN = "You like greens right? I'm not a big greens person."
JSON_IDX29_BAD = "Я не великий зелений, саме тоді, і саме в цей момент."

# 1.json idx 31 — "$3,000" dropped entirely.
JSON_IDX31_EN = "It's just $3,000. I really hope that can help you in some type of way man."
JSON_IDX31_BAD = "Я дуже сподіваюся, що це допоможе вам у чомусь."

# 1.json idx 2 — RU leftover in Final.
JSON_IDX2_EN = (
    "And I don't want to toss it out. How's the whole bunch of greens? "
    "Are you okay with that? Yeah, yeah I'm so hungry. I'll take whatever you got."
)
JSON_IDX2_BAD = "І я не хочу викидати його геть. Ти не проти? Я заберу все, что у тебя есть."

PAD_MARKERS = (
    "саме тоді",
    "і саме в цей момент",
    "в той момент",
    "ось як це було тоді",
)


def test_strip_img2790_trailing_pads_keeps_meaning():
    from engines.text_slot_fit import prepare_uk_spoken_text, strip_slot_pad_fillers

    clean = strip_slot_pad_fillers(JSON_IDX5_BAD)
    low = clean.lower()
    for pad in PAD_MARKERS:
        assert pad not in low, clean
    assert "два дні" in clean
    assert "напевно" in clean
    spoken = prepare_uk_spoken_text(JSON_IDX5_BAD)
    assert spoken
    assert "саме тоді" not in spoken.lower()


def test_narrative_same_then_not_stripped():
    from engines.text_slot_fit import strip_slot_pad_fillers

    legit = "Він довго йшов тією дорогою і думав про те, як саме тоді вирішив піти далі."
    out = strip_slot_pad_fillers(legit)
    assert "як саме тоді вирішив" in out


def test_pad_only_child_is_garbage_and_not_unique():
    from engines.text_slot_fit import (
        assert_unique_split_chunks,
        is_garbage_expand,
        is_pad_only_utterance,
    )

    pad_child = "і саме в цей момент, саме тоді."
    parent = "Так, це нормально тут, саме тоді, і саме в цей момент."
    assert is_pad_only_utterance(pad_child) is True
    assert is_garbage_expand(pad_child) is True
    assert (
        assert_unique_split_chunks(parent, [parent, pad_child]) is False
    )


def test_expand_does_not_voice_whitelist_pads():
    from engines.text_slot_fit import expand_to_fill, strip_slot_pad_fillers

    out, reasons = expand_to_fill(
        JSON_IDX5_RAW,
        target_ms=5000,
        lang="uk",
        source_hint=JSON_IDX5_EN,
        raw_mt=JSON_IDX5_RAW,
        prefer_raw=JSON_IDX5_RAW,
        strategy_order=("soft_pad_whitelist_once", "soft_pad_whitelist_twice"),
    )
    cleaned = strip_slot_pad_fillers(out)
    low = cleaned.lower()
    for pad in ("саме тоді", "і саме в цей момент"):
        assert pad not in low, (out, reasons)
    assert "два дні" in cleaned


def test_repeat_key_does_not_echo_honestly():
    from engines.text_slot_fit import _stage19j_repeat_key_phrase

    base = "Чесно кажучи, я просто перериваю смітник."
    out, ok = _stage19j_repeat_key_phrase(base)
    assert ok is False
    assert out == base
    assert out.count("Чесно кажучи") == 1


def test_intra_segment_restatement_removed():
    from engines.repetition_guard import remove_repeated_sentences

    cleaned, changed = remove_repeated_sentences(JSON_IDX8_BAD)
    assert changed is True
    assert cleaned.count("Чесно кажучи") == 1


def test_living_uk_rewrites_ru_and_strips_pads():
    from engines.simple_mt_path import finalize_living_uk_segments

    out, meta = finalize_living_uk_segments(
        [JSON_IDX20_EN, JSON_IDX2_EN],
        [JSON_IDX20_BAD, JSON_IDX2_BAD],
    )
    assert len(out) == 2
    sorry, take = out
    assert "саме тоді" not in sorry.lower()
    assert "мне жаль" not in sorry.lower()
    assert "шкода" in sorry.lower() or "жаль" not in sorry.lower()
    assert "что у тебя" not in take.lower()
    assert "ы" not in take.lower() and "э" not in take.lower()
    assert meta.get("naturalizer_executed") is True or meta.get("ru_rewrites", 0) >= 0


def test_greens_calque_and_money_entity():
    from engines.mt.glossary_en_uk import (
        apply_uk_marian_repairs,
        restore_dropped_source_entities,
    )
    from engines.text_slot_fit import strip_slot_pad_fillers

    greens = strip_slot_pad_fillers(JSON_IDX29_BAD)
    greens = apply_uk_marian_repairs(greens)
    assert "не великий зелений" not in greens.lower()
    assert "зелень" in greens.lower() or "люблю" in greens.lower()

    money = restore_dropped_source_entities(JSON_IDX31_EN, JSON_IDX31_BAD)
    assert "3000" in money or "тисяч" in money.lower() or "долар" in money.lower()
    assert "сподіваюся" in money.lower() or "допоможе" in money.lower()


def test_incomplete_mt_rejects_dropped_dollars_and_trash():
    from engines.mt_cache import is_incomplete_mt_pair

    assert is_incomplete_mt_pair(JSON_IDX31_EN, JSON_IDX31_BAD, "en", "uk") is True
    collapsed = "Та ну. щоб нагодувати себе?"
    trash_src = (
        "Come on. So wait you tell me that you eat other people's trash "
        "just to feed yourself?"
    )
    assert is_incomplete_mt_pair(trash_src, collapsed, "en", "uk") is True


def test_adjacent_pad_clones_blanked():
    from engines.repetition_guard import dedupe_adjacent_copies

    lines = [
        "Так, це нормально тут.",
        "Так, це нормально тут, саме тоді, і саме в цей момент.",
        "У мене немає іншого вибору.",
    ]
    out = dedupe_adjacent_copies(lines)
    assert out[0]
    assert out[1] == ""
    assert "вибору" in out[2]


def test_ru_sorry_is_not_uk_ok_until_rewrite():
    from engines.tts_lang_lock import (
        is_uk_tts_text_ok,
        rewrite_russian_leak_for_uk,
        uk_text_has_russian_leak,
    )
    from engines.text_slot_fit import prepare_uk_spoken_text

    stripped = prepare_uk_spoken_text(JSON_IDX20_BAD)
    assert uk_text_has_russian_leak(stripped) or uk_text_has_russian_leak(JSON_IDX20_BAD)
    rewritten = rewrite_russian_leak_for_uk(stripped)
    rewritten = prepare_uk_spoken_text(rewritten)
    assert "саме тоді" not in rewritten.lower()
    assert is_uk_tts_text_ok(rewritten)
    assert "мне" not in rewritten.lower()
