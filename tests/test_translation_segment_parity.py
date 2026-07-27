# -*- coding: utf-8 -*-
"""Stage 3: translation 1:1 parity + anti-bleed."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_length_imbalance_podium_pair():
    from engines.translation_segment_parity import enforce_one_to_one_translations

    src = [
        "Now, George Jr. walked over to the podium to take some photos of the winning driver",
        "but as he walked over there, this middle-aged man came up beside him and just asked "
        "George Jr. about his photography and then at some point the man formally introduced "
        "himself as Haskell Wexler.",
    ]
    long_uk = (
        "Джордж-молодший підійшов до подіуму, щоб сфотографувати водія-переможця, "
        "але коли він ішов туди, до нього підійшов чоловік середнього віку і запитав "
        "про фотографію, а потім чоловік представився як Хаскелл Векслер."
    )
    stray = "«Джордж, я знаю людей в USC»."
    fixed, audits = enforce_one_to_one_translations(src, [long_uk, stray])
    assert len(fixed) == 2
    assert fixed[0] != long_uk or fixed[1] != stray
    assert "USC" not in fixed[1] or "фотограф" in fixed[1].lower() or "чоловік" in fixed[1].lower()
    assert len(fixed[1]) > len(stray)
    assert any(a.get("action") == "length_imbalance_repair" for a in audits)


def test_split_by_sources_keeps_count():
    from engines.translation_segment_parity import split_translation_by_sources

    src = [
        "That is, except for cars.",
        "And at that point his father bought him a Fiat.",
    ]
    blob = (
        "Тобто, окрім автомобілів. І в той момент батько купив йому Fiat."
    )
    out = split_translation_by_sources(blob, src)
    assert len(out) == 2
    assert out[0] and out[1]
    assert out[0] != out[1]


def test_enforce_debleeds_identical_neighbors():
    from engines.translation_segment_parity import (
        detect_translation_bleed,
        enforce_one_to_one_translations,
    )

    src = [
        "That is, except for cars.",
        "And at that point his father bought him a Fiat.",
    ]
    blob = (
        "Тобто, окрім автомобілів. І в той момент батько купив йому маленький Fiat."
    )
    fixed, audits = enforce_one_to_one_translations(src, [blob, blob])
    assert len(fixed) == 2
    assert fixed[0] != fixed[1]
    bleed = detect_translation_bleed(src, fixed)
    # After split, identical-neighbor bleed should be gone or reduced
    assert not (fixed[0] == fixed[1])


def test_fit_shortens_severe_overflow():
    from engines.text_slot_fit import estimate_tts_ms, fit_text_to_slot

    long_uk = (
        "Джордж-молодший підійшов до подіуму, щоб сфотографувати водія-переможця, "
        "але коли він ішов туди, до нього підійшов чоловік середнього віку і запитав "
        "про фотографію. Потім чоловік представився як Хаскелл Векслер і сказав, "
        "що він кінооператор у Голлівуді. Джордж розповів про заявку до USC."
    )
    slot = 4000
    before = estimate_tts_ms(long_uk, "uk")
    assert before > slot * 1.5
    fit = fit_text_to_slot(long_uk, slot, "uk")
    assert fit.changed or fit.predicted_ms_after <= before
    assert fit.predicted_ms_after < before
    assert "Джордж" in fit.text or "подіум" in fit.text.lower() or "подium" in fit.text.lower()


def test_post_tts_split_does_not_leave_full_blob_on_left():
    from engines.adaptive_segmentation.post_tts import try_split_long_overflow_segment

    src = (
        "Now, George Jr. walked over to the podium to take some photos of the winning driver "
        "but as he walked over there, this middle-aged man came up beside him and just asked "
        "George Jr. about his photography."
    )
    tgt = (
        "Джордж-молодший підійшов до подіуму, щоб сфотографувати водія-переможця, "
        "але коли він ішов туди, до нього підійшов чоловік середнього віку і запитав "
        "про його фотографію."
    )
    segs = [
        {
            "text": tgt,
            "plain_text": tgt,
            "final_text": tgt,
            "tts_ms": 20000,
            "slot_ms": 12000,
            "segment_id": "a1",
        }
    ]
    sources = [src]
    timing = [{"start": 0, "end": 12000}]
    ok = try_split_long_overflow_segment(
        segments_data=segs,
        source_segments=sources,
        timing_map=timing,
        audits=[],
        idx=0,
    )
    if ok:
        assert len(segs) == 2
        left = str(segs[0].get("text") or "")
        right = str(segs[1].get("text") or "")
        # Must not keep the entire original blob only on the left.
        assert not (left == tgt and not right)
        assert left != right or right == ""
