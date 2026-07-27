# -*- coding: utf-8 -*-
"""Review Final ↔ TTS align + shared-blob debleed at populate."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_align_debleeds_dinner_crash_audits():
    from engines.tts_review_align import align_info_for_translation_review

    blob = (
        "І тому практично кожна вечеря в ці дні перетворювалася на величезну "
        "суперечку між батьком і сином. І ось Джордж, він підійшов до цього "
        "перехрестя, де воно було прямо біля його дому, і він почав повертати, "
        "коли почув цей дуже гучний вереск, а потім усе повернуло, коли він "
        "почув цей дуже гучний вереск, а потім усе пішло. Через два тижні "
        "Джордж-молодший лежав на лікарняному ліжку у відділенні інтенсивної "
        "терапії місцевої лікарні."
    )
    info = {
        "source_segments": [
            "And so basically every dinner these days it became this huge argument between father and son.",
            "And so George, he came to this intersection where it was right near his home and he begins making the turn when he hears this really loud screeching sound and then everything went making the turn when he hears this really loud screeching sound and then everything went Two weeks later, George Jr. was laying in a hospital bed in the intensive care unit at the local hospital.",
        ],
        "translation_audits": [
            {
                "index": 0,
                "raw_translation": blob,
                "naturalized_text": blob,
                "final_text": blob,
                "tts_text": blob,
            },
            {
                "index": 1,
                "raw_translation": blob,
                "naturalized_text": blob,
                "final_text": blob,
                "tts_text": blob,
            },
        ],
        "segments_data": [
            {"index": 0, "text": blob, "final_text": blob},
            {"index": 1, "text": blob, "final_text": blob},
        ],
    }
    align_info_for_translation_review(info)
    a0 = info["translation_audits"][0]["final_text"]
    a1 = info["translation_audits"][1]["final_text"]
    assert a0 != a1, (a0[:90], a1[:90])
    assert "суперечку" in a0.lower()
    assert "перехрестя" in a1.lower() or "лікарн" in a1.lower()
    assert info["segments_data"][0]["tts_text"] == info["segments_data"][0]["final_text"]
    assert info["segments_data"][1]["tts_text"] == info["segments_data"][1]["final_text"]


def test_freeze_restores_terminal_period():
    from engines.tts_review_align import freeze_spoken_to_review_final

    sd = [
        {
            "final_text": "Його фільм став частиною найуспішнішої кінофраншизи всіх часів.",
            "text": "Його фільм став частиною найуспішнішої кінофраншизи всіх часів",
            "tts_text": "Його фільм став частиною найуспішнішої кінофраншизи всіх часів",
        }
    ]
    out = freeze_spoken_to_review_final(
        ["Його фільм став частиною найуспішнішої кінофраншизи всіх часів"],
        sd,
        [{"index": 0, "final_text": sd[0]["final_text"]}],
        source_segments=["His movie would go on to become part of the most successful movie franchise of all time."],
    )
    assert out[0].endswith("."), out[0]
    assert sd[0]["tts_text"] == out[0]


def test_phrase_loop_deflate_on_align():
    from engines.tts_review_align import align_info_for_translation_review

    looped = (
        "Джордж-молодший сьогодні більш відомий як Джордж Лукас, "
        "а його франшиза фільму «Зоряні війни». більш відомий сьогодні "
        "як Джордж Лукас, а його франшиза «Зоряні війни»."
    )
    info = {
        "source_segments": [
            "George Jr. is better known today as George Lucas",
            "and his film franchise was Star Wars. better known today as George Lucas and his film franchise was Star Wars.",
        ],
        "translation_audits": [
            {
                "index": 0,
                "raw_translation": looped,
                "naturalized_text": looped,
                "final_text": looped,
                "tts_text": looped,
            },
            {
                "index": 1,
                "raw_translation": looped,
                "naturalized_text": looped,
                "final_text": looped,
                "tts_text": looped,
            },
        ],
        "segments_data": [
            {"text": looped, "final_text": looped},
            {"text": looped, "final_text": looped},
        ],
    }
    align_info_for_translation_review(info)
    f0 = info["translation_audits"][0]["final_text"].lower()
    # At least one of: split pair OR phrase-loop deflate
    assert (
        info["translation_audits"][0]["final_text"]
        != info["translation_audits"][1]["final_text"]
        or f0.count("джордж лукас") <= 1
        or "phrase_loop_healed" in info["translation_audits"][0]
    ), f0


def test_freeze_keeps_review_not_engine_rewrite():
    from engines.tts_review_align import freeze_spoken_to_review_final

    approved = "Наприклад, чому ви не можете зосередитися на цьому?"
    engine_chop = "Наприклад."
    sd = [{"final_text": approved, "text": engine_chop, "tts_text": engine_chop}]
    out = freeze_spoken_to_review_final(
        [engine_chop],
        sd,
        [{"index": 0, "final_text": approved}],
    )
    assert out[0] == approved or out[0].startswith("Наприклад, чому"), out[0]


if __name__ == "__main__":
    test_align_debleeds_dinner_crash_audits()
    test_freeze_restores_terminal_period()
    test_phrase_loop_deflate_on_align()
    test_freeze_keeps_review_not_engine_rewrite()
    print("OK")
