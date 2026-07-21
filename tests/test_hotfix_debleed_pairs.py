# -*- coding: utf-8 -*-
"""Anti-bleed: split shared MT blob across incomplete EN + but-continuation."""

from __future__ import annotations

from engines.translation_naturalizer import (
    debleed_adjacent_batch_copies,
    polish_lines,
)
from engines.pipeline_orchestrator.translation_batch import (
    TranslationBatch,
    split_batch_translation,
)

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]

FIAT_EN = "And at that point his father actually bought him a small Italian car called the Fiat,"
OBS_EN = (
    "but his father, despite being the one who literally gave him the Fiat, "
    "he just didn't get his son's obsession with cars, like why aren't you able "
    "to take that focus and apply it to other things, so we'll get your real job."
)
COMBINED_UK = (
    "І в той момент його батько купив йому невеликий італійський автомобіль під назвою Fiat, "
    "але батько його, хоч і сам подарував йому Fiat, він просто не розумів одержимості сина, "
    "«Чому ти не можеш зосередитися на цьому і застосувати це до інших речей, отримаєш справжню роботу»."
)

USC_EN = (
    "In fact, George Jr. had applied to the prestigious cinematography program "
    "at the University of Southern California,"
)
AFTER_EN = "but after sending off his application, he was pretty sure he would not get in."
USC_COMBINED = (
    "Насправді Джордж-молодший подав заявку на престижну програму кінематографії в USC, "
    "але після того, як надіслав заявку він був майже впевнений, що його не візьмуть."
)


def test_debleed_fiat_obsession_pair():
    out = debleed_adjacent_batch_copies(
        [FIAT_EN, OBS_EN],
        [COMBINED_UK, COMBINED_UK],
    )
    assert len(out) == 2
    assert out[0] != out[1]
    assert "купив" in out[0].lower() or "фіат" in out[0].lower() or "fiat" in out[0].lower()
    assert "одержимост" in out[1].lower() or "зосередит" in out[1].lower() or "роботу" in out[1].lower()
    assert "одержимост" not in out[0].lower() and "зосередит" not in out[0].lower()


def test_debleed_usc_after_pair():
    out = debleed_adjacent_batch_copies(
        [USC_EN, AFTER_EN],
        [USC_COMBINED, USC_COMBINED],
    )
    assert "USC" in out[0] or "кінематограф" in out[0].lower()
    assert "але після" in out[1].lower() or "впевнен" in out[1].lower()
    assert "але після" not in out[0].lower()


def test_polish_lines_debleeds_pair():
    out = polish_lines(
        [COMBINED_UK, COMBINED_UK],
        source_segments=[FIAT_EN, OBS_EN],
        tgt_lang="uk",
        src_lang="en",
        use_llm=False,
        app_dir=ROOT,
    )
    assert len(out) == 2
    assert out[0].strip() and out[1].strip()
    assert out[0] != out[1]


def test_split_batch_uses_debleed():
    batch = TranslationBatch(
        batch_id=0,
        segment_indices=[3, 4],
        source_texts=[FIAT_EN, OBS_EN],
    )
    split = split_batch_translation(batch, COMBINED_UK)
    assert split[3] != split[4]
    assert "але" in split[4].lower() or "роботу" in split[4].lower()
