# -*- coding: utf-8 -*-
"""GL Review polish: Haskell dialogue, cinematography program, create-calque."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engines.translation_naturalizer import naturalize_uk, polish_lines


def test_haskell_let_me_call_not_formal_allow():
    raw = (
        "Дозвольте мені подзвонити, і, звичайно, невдовзі після цієї доленосної "
        "зустрічі Джордж-молодший отримає лист про прийняття від кіношколи USC, "
        "але цей момент у житті Джорджа-молодшого не лише змінив його життя назавжди."
    )
    # Production path (naturalizer route)
    out = polish_lines(
        [raw],
        source_segments=[
            "Let me make some calls and sure enough, not long after this fateful "
            "meeting, George Jr. would receive an acceptance letter from USC's "
            "film school but this moment in George Jr.'s life did not just alter "
            "George's life forever."
        ],
        tgt_lang="uk",
        src_lang="en",
        use_llm=False,
        app_dir=ROOT,
    )[0]
    assert "Дозвольте мені подзвонити" not in out
    assert out.startswith("Я подзвоню")
    assert "лист про прийняття" not in out
    # Belt: naturalize_uk alone must also strip the formal allow-me calque
    assert "Дозвольте мені подзвонити" not in naturalize_uk(raw)
    assert naturalize_uk(raw).startswith("Я подзвоню")


def test_cinematography_program_case_collapse():
    raw = (
        "Джордж-молодший розповів Хаскеллу про те, як він нещодавно подав заяву в USC, "
        "щоб спробувати потрапити до них Кінематографічна програма, і коли Хаскелл "
        "почув це, він сказав: «Джордж, я знаю людей в USC."
    )
    out = polish_lines(
        [raw],
        source_segments=[
            "George Jr. told Haskell about how he had recently applied to USC to try "
            "to get into their cinematography program and when Haskell heard this, "
            "he said, George, I know people at USC."
        ],
        tgt_lang="uk",
        src_lang="en",
        use_llm=False,
        app_dir=ROOT,
    )[0]
    assert "Кінематографічна програма" not in out
    assert "програми з кінематографії" in out or "програму з кінематографії" in out
    assert out.count("«") == out.count("»")


def test_would_go_on_to_create_not_continue_creation():
    raw = (
        "Це також назавжди повністю змінить кінематограф загалом, тому що приблизно "
        "через 13 років Джордж-молодший продовжить створення одного з найбільш "
        "новаторських фільмів в історії."
    )
    out = polish_lines(
        [raw],
        source_segments=[
            "It also would go on to completely alter cinema in general forever "
            "because about 13 years later, George Jr. would go on to create one of "
            "the most groundbreaking films in history."
        ],
        tgt_lang="uk",
        src_lang="en",
        use_llm=False,
        app_dir=ROOT,
    )[0]
    assert "продовжить створення" not in out
    assert "створить" in out
    assert "продовжить створення" not in naturalize_uk(raw)
    assert "створить" in naturalize_uk(raw)
