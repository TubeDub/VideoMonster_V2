"""Regression tests for semantic validation recovery and George Lucas calques."""

from __future__ import annotations

from engines.semantic_meaning import should_prefer_semantic_over_raw_mt
from engines.translation_naturalizer import polish_lines
from engines.translation_validation import (
    prefer_semantic_authority,
    resolve_post_quality_text,
    stamp_authoritative_final_text,
    sync_final_text_to_task_info,
)


GEORGE_LONG_EN = (
    "But, as he was driving, George Jr. could not help but feel like he was really "
    "dreading actually getting there. So George Jr. was a very smart kid, but he also "
    "got distracted really easily and because of that, he really had not pursued "
    "anything all that seriously that is except for cars. And at that point his father "
    "actually bought him a small Italian car called the Fiat, but his father, despite "
    "being the one who literally gave him the Fiat, he just didn't get his son's "
    "obsession with cars, like why aren't you able to take that focus and apply it to "
    "other things, so we'll get your real job. And so basically every dinner these "
    "days, if he came this huge argument between father and son. And so George, he came "
    "to this intersection where it was right near his home, and he begins making the "
    "turn when he hears this really loud screeching sound and then everything went black."
)

GEORGE_SEMANTIC_UK = (
    "Але, коли він їхав, Джордж-молодший його не полишала тривога, що йому справді "
    "страшно туди дістатися. Тож Джордж-молодший був дуже розумною дитиною, але також "
    "дуже легко відволікався, і через це він насправді не займався чимось настільки "
    "серйозним, окрім автомобілів. І в той момент його батько дійсно купив йому "
    "невелику італійську машину під назвою Fiat, але його батько, незважаючи на те, "
    "що він буквально дав йому Fiat, він просто не зрозумів одержимості свого сина "
    "автомобілями, отримаєш справжню роботу. І так по суті, кожна вечеря в ці дні "
    "перетворювалася на велику суперечку між батьком і сином. І ось Джордж, він "
    "підійшов до цього перехрестя, де воно було прямо біля його дому, і він почав "
    "повертати, коли почув цей дуже гучний вереск, а потім усе потемніло."
)

GEORGE_RAW_MT_FRAGMENT = (
    "Джордж молодший був дуже розумною дитиною, але він також відволікся дуже легко, "
    "і через це він не займався чимось серйозним, окрім машин."
)

GEORGE_ACCENTED_RAW = (
    "Джордж-молодший був ду́же розумною дитиною, але́ ві́н тако́ж відволікся ду́же "
    "легко, і че́рез це ві́н не займався чимось серйозним, окрім машин."
)


def test_should_prefer_semantic_over_short_raw_mt_fragment():
    assert should_prefer_semantic_over_raw_mt(
        semantic=GEORGE_SEMANTIC_UK,
        raw_mt=GEORGE_RAW_MT_FRAGMENT,
        source=GEORGE_LONG_EN,
        fail_reason="entity_loss",
    )


def test_resolve_final_prefers_semantic_over_accented_raw_fragment():
    """w.json ownership bug: prosody accents hide final==raw exact match."""
    seg = {
        "final_text": GEORGE_ACCENTED_RAW,
        "voice_input": GEORGE_ACCENTED_RAW,
        "semantic_engine_text": GEORGE_SEMANTIC_UK,
        "semantic_text": GEORGE_SEMANTIC_UK,
        "original_text": GEORGE_LONG_EN,
    }
    audit = {"raw_translation": GEORGE_RAW_MT_FRAGMENT, "whisper_text": GEORGE_LONG_EN}
    assert prefer_semantic_authority(
        semantic=GEORGE_SEMANTIC_UK,
        candidate=GEORGE_ACCENTED_RAW,
        raw_mt=GEORGE_RAW_MT_FRAGMENT,
        source=GEORGE_LONG_EN,
    )
    owned = resolve_post_quality_text(seg, audit)
    assert owned == GEORGE_SEMANTIC_UK
    assert not owned.lower().startswith("між батьком і сином")


def test_resolve_final_prefers_semantic_over_clause_restored_raw():
    from engines.dsal.clause_coverage import restore_missing_clauses

    mangled, cov = restore_missing_clauses(GEORGE_RAW_MT_FRAGMENT, GEORGE_LONG_EN)
    assert cov.restored_phrases
    # Mid-paragraph "between father and son" must NOT become a fake sentence opener.
    assert "між батьком і сином" in mangled.lower()
    assert not mangled.lower().startswith("між батьком і сином")
    assert prefer_semantic_authority(
        semantic=GEORGE_SEMANTIC_UK,
        candidate=mangled,
        raw_mt=GEORGE_RAW_MT_FRAGMENT,
        source=GEORGE_LONG_EN,
    )
    owned = resolve_post_quality_text(
        {
            "final_text": mangled,
            "semantic_engine_text": GEORGE_SEMANTIC_UK,
            "original_text": GEORGE_LONG_EN,
        },
        {"raw_translation": GEORGE_RAW_MT_FRAGMENT},
    )
    assert owned == GEORGE_SEMANTIC_UK
    assert not owned.lower().startswith("між батьком і сином")


def test_sync_stamps_one_authoritative_final_from_semantic():
    info = {
        "source_segments": [GEORGE_LONG_EN],
        "segments_data": [
            {
                "index": 0,
                "final_text": GEORGE_ACCENTED_RAW,
                "semantic_engine_text": GEORGE_SEMANTIC_UK,
                "semantic_text": GEORGE_SEMANTIC_UK,
            }
        ],
        "translation_audits": [
            {
                "index": 0,
                "raw_translation": GEORGE_RAW_MT_FRAGMENT,
                "semantic_engine_text": GEORGE_SEMANTIC_UK,
                "final_text": GEORGE_ACCENTED_RAW,
            }
        ],
    }
    sync_final_text_to_task_info(info)
    seg = info["segments_data"][0]
    audit = info["translation_audits"][0]
    assert seg["final_text"] == GEORGE_SEMANTIC_UK
    assert seg["text_for_tts"] == GEORGE_SEMANTIC_UK
    assert audit["final_text"] == GEORGE_SEMANTIC_UK
    assert audit["tts_text"] == GEORGE_SEMANTIC_UK


def test_stamp_preserves_semantic_engine_when_writing_downstream_text():
    from engines.stress_marks import strip_stress_marks

    seg = {"semantic_engine_text": GEORGE_SEMANTIC_UK}
    stamp_authoritative_final_text(
        seg,
        GEORGE_ACCENTED_RAW,
        audit={"raw_translation": GEORGE_RAW_MT_FRAGMENT},
    )
    assert seg["semantic_engine_text"] == GEORGE_SEMANTIC_UK
    # Prosody accents are TTS-only; authoritative final stays plain.
    assert seg["final_text"] == strip_stress_marks(GEORGE_ACCENTED_RAW)


def test_polish_lines_fixes_star_wars_franchise_calque():
    src = (
        'George Jr. is better known today as George Lucas and his film franchise '
        'will star wars.'
    )
    bad = (
        'Сьогодні Джорджа Молодшого більше знають як Джорджа Лукаса і його фільм '
        '"Франшиза" буде вести війни.'
    )
    out = polish_lines([bad], source_segments=[src], tgt_lang="uk")[0]
    assert ("Зоряними війнами" in out) or ("«Зоряні війни»" in out)
    assert "Франшиза" not in out
    assert "вести війни" not in out


def test_polish_lines_fixes_haskell_wexler_garble():
    src = (
        "But as he walked over there, the smithelaged man came up beside him and just "
        "asked George Jr. about his photography and then at some point the man actually "
        "formally introduced himself as Haskell Wexler."
    )
    bad = (
        "Але як він прогулявся над там, скотарний чоловік приступив до нього і просто "
        "попросив Джорджа Джера. про свою фотографію"
    )
    out = polish_lines([bad], source_segments=[src], tgt_lang="uk")[0]
    assert "середнього віку" in out
    assert "Джорджа-молодшого" in out
    assert "скотарний" not in out
