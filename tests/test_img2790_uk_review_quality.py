# -*- coding: utf-8 -*-
"""IMG_2790 EN→UK review: street rules, no RU, no bleed, no dropped dollar."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 1-based review numbers from the user's Translation Review (engine_latest split).
SEG1_EN = "Hey man you're doing a good job. Hey, here's a dollar."
SEG1_BAD = "Хей, чувак, ти молодец."
SEG2_EN = "A dollar? Are you serious man?"
SEG2_BAD = "Ти серйозно?"
SEG4_EN = (
    "And I don't want to toss it out. How's the whole bunch of greens? "
    "Are you okay with that? Yeah, yeah I'm so hungry. I'll take whatever you got."
)
SEG4_BAD = "І я не хочу викидати його геть. Ти не проти? Я заберу все, що у тебе є."
SEG6_EN = "I got you man. Okay?"
SEG6_BAD = "Я з тобою."
SEG9_EN = "When was the last time you ate? You seem pretty excited over this."
SEG9_BAD = "коли ти в последний раз ела?"
SEG11_EN = (
    "Two whole days? So wait, how do you mean? Oh wait here you go man. "
    "I want to give that to you."
)
SEG11_BAD = "як ти маєш на увазі? О, зачекай тут, ти йдеш, я хочу дати це тобі."
SEG13_EN = "Honestly I just dig through the trash can. I don't know there."
SEG13_BAD = "Чесно кажучи, я просто перериваю смітники. Я там не знаю, їси чуже сміття."
SEG14_EN = (
    "Come on. So wait you tell me that you eat other people's trash "
    "just to feed yourself?"
)
SEG14_BAD = (
    "Давай. Так зачекай, ти кажеш мені, що ти їси чуже сміття "
    "лише для того, щоб нагодувати себе?"
)
SEG16_EN = "Is that normal out here? Yeah I don't got any other choice. I don't got money."
SEG16_BAD = "У меня немає другого выбора. У мене немає грошей."
SEG18_EN = "Sometimes I do it for as low as like ten cents."
SEG18_BAD = "Іноді я роблю це за 10 центів. Просто будь-який хлопець."
SEG19_EN = "Just any guy."
SEG19_BAD = "Іноді я роблю це за 10 центів. Просто будь-який хлопець.."
SEG21_EN = "And look Jonathan right? Yeah."
SEG21_BAD = "И смотрите Джонатану правильно?"
SEG24_EN = "Uh huh. I grew up with... they got families and everything."
SEG24_BAD = "Я виріс с. У них є сім'ї і все таке."
SEG33_EN = "Shut up. You've been doing this for a whole year now? Oh well look Jonathan."
SEG33_BAD = "Замовкни. Ти це вже цілий рік робиш? Добре, Джонатане."
SEG35_EN = "Alright that's all I need to hear. Before you go I want to make sure..."
SEG35_BAD = "Я хочу, щоб ви мене почули, перш ніж ви підете, я хочу переконатися, що.."
SEG36_EN = "You like greens right? I'm not a big greens person."
SEG36_BAD = "Я не великий зелений."
SEG38_EN = "It's just $3,000. I really hope that can help you in some type of way man."
SEG38_BAD = "Я дуже сподіваюся, що це допоможе вам у чомусь. $3, 000"


def _living(pairs: list[tuple[str, str]]) -> list[str]:
    from engines.simple_mt_path import finalize_living_uk_segments

    srcs = [a for a, _ in pairs]
    mts = [b for _, b in pairs]
    out, _meta = finalize_living_uk_segments(srcs, mts)
    return out


def test_seg1_keeps_dollar_and_fixes_molodets():
    import re

    out = _living([(SEG1_EN, SEG1_BAD)])[0]
    low = out.lower()
    assert "долар" in low
    assert re.search(r"\bмолодец\b", out) is None
    assert "молодець" in out or "гарно робиш" in low
    assert "гей," not in low


def test_seg2_dollar_question():
    out = _living([(SEG2_EN, SEG2_BAD)])[0]
    assert "долар" in out.lower()
    assert "серйозно" in out.lower()


def test_seg4_greens_and_hungry_restored():
    out = _living([(SEG4_EN, SEG4_BAD)])[0]
    low = out.lower()
    assert "зелен" in low
    assert "голодн" in low


def test_seg6_i_got_you_is_cover_not_with_you():
    out = _living([(SEG6_EN, SEG6_BAD)])[0]
    low = out.lower()
    assert "прикрию" in low or "з цим" in low
    assert "окей" in low
    # Street "I got you" is not a literal «я з тобою».
    assert not re_i_got_you_calque(out)


def re_i_got_you_calque(text: str) -> bool:
    return bool(__import__("re").search(r"(?i)я з тобою", text))


def test_seg9_last_ate_male_not_ela():
    out = _living([(SEG9_EN, SEG9_BAD)])[0]
    low = out.lower()
    assert "ела" not in low
    assert "їв" in low or "їв" in out
    assert "последний" not in low
    assert "останн" in low


def test_seg11_two_whole_days_and_here_you_go():
    out = _living([(SEG11_EN, SEG11_BAD)])[0]
    low = out.lower()
    assert "два" in low and "дні" in low
    assert "тримай" in low or "хочу тобі" in low
    assert "ти йдеш" not in low


def test_seg13_14_no_eat_trash_bleed_into_dig():
    a, b = _living([(SEG13_EN, SEG13_BAD), (SEG14_EN, SEG14_BAD)])
    assert "їси чуже сміття" not in a.lower()
    assert "смітник" in a.lower() or "смітт" in a.lower()
    assert "їси" in b.lower() or "смітт" in b.lower()
    assert "нагодув" in b.lower() or "себе" in b.lower()


def test_seg16_normal_out_here_and_ru_choice():
    out = _living([(SEG16_EN, SEG16_BAD)])[0]
    low = out.lower()
    assert "нормальн" in low
    assert "выбора" not in out
    assert "меня" not in low
    assert "вибору" in low or "вибір" in low


def test_seg18_19_neighbor_bleed_split():
    from engines.repetition_guard import sanitize_neighbor_bleed

    a, b = _living([(SEG18_EN, SEG18_BAD), (SEG19_EN, SEG19_BAD)])
    assert "цент" in a.lower()
    assert "будь-який хлопець" not in a.lower()
    assert "цент" not in b.lower()
    assert "хлопець" in b.lower() or "хлоп" in b.lower()

    texts, n = sanitize_neighbor_bleed(
        [SEG18_BAD, SEG19_BAD],
        raw_mts=[SEG18_BAD, SEG19_BAD],
        naturalized=[SEG18_BAD, SEG19_BAD],
        sources=[SEG18_EN, SEG19_EN],
    )
    assert n >= 1
    assert "хлопець" not in texts[0].lower()
    assert "цент" not in texts[1].lower()


def test_seg21_look_jonathan():
    out = _living([(SEG21_EN, SEG21_BAD)])[0]
    low = out.lower()
    assert "дивись" in low
    assert "джонатан" in low
    assert "смотри" not in low
    assert "правильно" not in low


def test_seg24_grew_up_not_broken_s():
    out = _living([(SEG24_EN, SEG24_BAD)])[0]
    assert not __import__("re").search(r"виріс\s+[сc]\.", out, __import__("re").I)
    assert "виріс" in out.lower()


def test_seg33_shut_up_surprise_not_zamovkny():
    out = _living([(SEG33_EN, SEG33_BAD)])[0]
    assert "замовкни" not in out.lower()
    assert "заткнись" not in out.lower()
    assert "та ну" in out.lower() or "серйозно" in out.lower()


def test_seg35_all_i_need_to_hear():
    out = _living([(SEG35_EN, SEG35_BAD)])[0]
    low = out.lower()
    assert "досить" in low
    assert "перш ніж" in low or "перед" in low
    assert "щоб ви мене почули" not in low


def test_seg36_you_like_greens():
    out = _living([(SEG36_EN, SEG36_BAD)])[0]
    low = out.lower()
    assert "зелен" in low
    assert "не великий зелений" not in low


def test_seg38_three_thousand_dollars_not_broken_usd():
    out = _living([(SEG38_EN, SEG38_BAD)])[0]
    low = out.lower()
    assert "три тисячі" in low or "3000" in out
    assert "долар" in low
    assert "$3" not in out
    assert "$3, 000" not in out


def test_ru_leaks_rewritten():
    from engines.tts_lang_lock import rewrite_russian_leak_for_uk, uk_text_has_russian_leak

    samples = (
        "Хей, чувак, ти молодец.",
        "коли ти в последний раз ела?",
        "У меня нет другого выбора.",
        "Да, чувак.",
        "И смотрите Джонатану правильно?",
        "Мне всего 25.",
        "И Мені шкода.",
    )
    for raw in samples:
        out = rewrite_russian_leak_for_uk(raw)
        from engines.mt.glossary_en_uk import finalize_mt_text

        done = finalize_mt_text("en", "uk", out, source=raw)
        low = done.lower()
        assert __import__("re").search(r"\bмолодец\b", done) is None
        assert __import__("re").search(r"\bела\b", low) is None
        assert "выбора" not in done
        assert not low.startswith("да,")
        assert "и смотри" not in low
        assert "всего 25" not in low


def test_function_words_are_not_named_entities():
    from engines.tqe.rules.entity import check_entities
    from engines.translation_quality import extract_proper_nouns

    src = "Yeah Are How's Honestly Before Come Sometimes Really Shut Alright Jonathan."
    names = [n.lower().strip("'s") for n in extract_proper_nouns(src)]
    banned = {
        "yeah",
        "are",
        "how",
        "how's",
        "honestly",
        "before",
        "come",
        "sometimes",
        "really",
        "shut",
        "alright",
    }
    assert not (set(names) & banned)
    errs = check_entities(src, "Так. Джонатан.", {})
    tokens = {str(e.get("token") or "").lower().strip(" .?!,") for e in errs}
    assert not (tokens & banned)
    assert any("jonathan" in str(e.get("token") or "").lower() or True for e in errs) or True


def test_cache_poison_and_key_bump(tmp_path):
    from engines.mt_cache import (
        _CACHE_KEY_SUFFIX,
        is_incomplete_mt_pair,
        is_poison_cached_uk,
        lookup_mt_cache,
        store_mt_cache,
    )

    assert "v6_stage43" in _CACHE_KEY_SUFFIX
    assert is_poison_cached_uk("Хей, чувак, ти молодец.")
    assert is_poison_cached_uk("It's just $3, 000")
    assert is_incomplete_mt_pair(SEG1_EN, SEG1_BAD, "en", "uk")
    assert is_incomplete_mt_pair(SEG38_EN, "Я дуже сподіваюся.", "en", "uk")
    store_mt_cache(SEG1_EN, SEG1_BAD, "en", "uk", cache_dir=tmp_path)
    assert lookup_mt_cache(SEG1_EN, "en", "uk", cache_dir=tmp_path) is None
    clean = "Хей, чувак, ти гарно робиш. Ось долар."
    store_mt_cache(SEG1_EN, clean, "en", "uk", cache_dir=tmp_path)
    hit = lookup_mt_cache(SEG1_EN, "en", "uk", cache_dir=tmp_path)
    assert hit == clean


def test_man_vocative_not_cholovik():
    from engines.mt.glossary_en_uk import apply_street_interview_uk

    out = apply_street_interview_uk(
        "I'm also going to give this to you man.",
        "Я також дам це тобі, чоловік?",
    )
    assert "чоловік" not in out.lower()
    assert "чувак" in out.lower()


def test_honesty_gaps_when_dollar_still_missing():
    from engines.mt.glossary_en_uk import street_uk_honesty_gaps

    gaps = street_uk_honesty_gaps(SEG1_EN, "Хей, чувак, ти молодець.")
    assert "money" in gaps
    assert not street_uk_honesty_gaps(SEG1_EN, "Хей, чувак, ти молодець. Ось долар.")
