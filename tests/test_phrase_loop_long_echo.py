# -*- coding: utf-8 -*-
"""Long STT / near-duplicate sentence echoes must deflate before TTS."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engines.mt.cross_script_guard import deflate_phrase_loop, has_phrase_loop
from engines.tts_review_align import align_info_for_translation_review


UK_LUCAS = (
    "Джордж-молодший сьогодні більш відомий як Джордж Лукас, а його франшиза "
    "фільму «Зоряні війни». більш відомий сьогодні як Джордж Лукас, а його "
    "франшиза «Зоряні війни»."
)

EN_LUCAS = (
    "George Jr. is better known today as George Lucas and his film franchise "
    "was Star Wars. better known today as George Lucas and his film franchise "
    "was Star Wars."
)

EN_CRASH = (
    "And so George, he came to this intersection where it was right near his "
    "home and he begins making the turn when he hears this really loud "
    "screeching sound and then everything went making the turn when he hears "
    "this really loud screeching sound and then everything went Two weeks "
    "later, George Jr. was laying in a hospital bed in the intensive care "
    "unit at the local hospital."
)

UK_CRASH = (
    "І ось Джордж, він підійшов до цього перехрестя, де воно було прямо біля "
    "його дому, і він почав повертати, коли почув цей дуже гучний вереск, а "
    "потім усе повернуло, коли він почув цей дуже гучний вереск, а потім усе "
    "пішло. Через два тижні Джордж-молодший лежав на лікарняному ліжку у "
    "відділенні інтенсивної терапії місцевої лікарні."
)


def test_detects_george_lucas_sentence_echo():
    assert has_phrase_loop(UK_LUCAS, min_repeats=2)
    assert has_phrase_loop(EN_LUCAS, min_repeats=2)
    out = deflate_phrase_loop(UK_LUCAS)
    assert out.count("Джордж Лукас") == 1
    assert out.count("Зоряні війни") == 1
    assert "більш відомий сьогодні" not in out or out.index("більш") < 40
    en = deflate_phrase_loop(EN_LUCAS)
    assert en.count("George Lucas") == 1
    assert en.count("Star Wars") == 1
    assert "better known today" in en.lower()
    assert en.lower().count("better known today") == 1


def test_detects_long_consecutive_stt_crash_loop():
    assert has_phrase_loop(EN_CRASH, min_repeats=2)
    out = deflate_phrase_loop(EN_CRASH)
    assert out.lower().count("making the turn when he hears") == 1
    assert "Two weeks later" in out
    assert "intensive care" in out


def test_deflates_uk_crash_near_repeat_clause():
    assert has_phrase_loop(UK_CRASH, min_repeats=2)
    out = deflate_phrase_loop(UK_CRASH)
    assert out.lower().count("дуже гучний вереск") == 1
    assert "лікарн" in out.lower()
    assert "перехрестя" in out.lower()


def test_align_deflates_lucas_loop_in_review():
    info = {
        "source_segments": [EN_LUCAS],
        "translation_audits": [
            {
                "index": 0,
                "raw_translation": UK_LUCAS,
                "naturalized_text": UK_LUCAS,
                "final_text": UK_LUCAS,
                "tts_text": UK_LUCAS,
            }
        ],
        "segments_data": [{"index": 0, "text": UK_LUCAS, "final_text": UK_LUCAS}],
    }
    align_info_for_translation_review(info)
    final = info["translation_audits"][0]["final_text"]
    assert final.count("Джордж Лукас") == 1
    assert info["segments_data"][0]["tts_text"] == final
