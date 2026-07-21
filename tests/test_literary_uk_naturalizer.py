"""Literary UK naturalizer — freer phrasing than calque post-edit."""

from __future__ import annotations


def test_literary_dreading_home():
    from engines.naturalizer_v2.literary_uk import apply_literary_uk

    src = (
        "Джорджа-молодшого не полишало відчуття, що він дуже боявся туди дістатися."
    )
    out, codes = apply_literary_uk(
        src,
        original="George Jr. couldn’t help but feel like he was dreading getting home.",
    )
    assert codes
    assert "не хотілося" in out.lower() or "боявся" in out.lower()
    assert "важке передчуття" not in out.lower()
    assert "дуже боявся туди дістатися" not in out.lower()


def test_literary_nothing_seriously():
    from engines.naturalizer_v2.literary_uk import apply_literary_uk

    src = "він дійсно майже нічого серйозно не робив, окрім автомобілів"
    out, _ = apply_literary_uk(src)
    assert "по-справжньому серйозно" in out
    assert "дійсно майже нічого серйозно" not in out


def test_literary_fiat_no_which_is_called():
    from engines.naturalizer_v2.literary_uk import apply_literary_uk

    src = "невеликий італійський автомобіль, який називається Фіат"
    out, _ = apply_literary_uk(src)
    assert "який називається" not in out.lower()
    assert "fiat" in out.lower() or "фіат" in out.lower()


def test_literary_winning_driver_photos():
    from engines.naturalizer_v2.literary_uk import apply_literary_uk

    src = "взяти деякі фотографії переможця"
    out, _ = apply_literary_uk(src)
    assert "гонщика" in out
    assert "переможця гонки" not in out.lower()
    assert "взяти деякі фотографії" not in out.lower()


def test_literary_known_today_as_lucas():
    from engines.naturalizer_v2.literary_uk import apply_literary_uk

    src = "Джордж-молодший, відомий сьогодні, як Джордж Лукас"
    out, _ = apply_literary_uk(
        src,
        original="George Jr., better known today as George Lucas",
    )
    assert "знають як" in out.lower() or "відомий як" in out.lower()
    assert "весь світ" not in out.lower()
    assert "відомий сьогодні" not in out.lower()


def test_literary_obsession_rephrase():
    from engines.naturalizer_v2.literary_uk import apply_literary_uk

    src = "він просто не розумів одержимості сина автомобілями"
    out, _ = apply_literary_uk(src)
    assert "одержимий" in out or "не розумів" in out
    assert "просто не розумів одержимості" not in out


def test_literary_father_fiat_redundancy():
    from engines.naturalizer_v2.literary_uk import apply_literary_uk

    src = (
        "але батько, незважаючи на те, що той, хто буквально дав йому Фіат, "
        "він просто не розумів одержимості сина автомобілями"
    )
    out, _ = apply_literary_uk(src)
    assert "буквально дав" not in out.lower()
    assert "незважаючи на те, що той" not in out.lower()


def test_literary_simplifies_ornate_foreboding():
    from engines.naturalizer_v2.literary_uk import apply_literary_uk

    out, _ = apply_literary_uk("дорогою додому його не полишало важке передчуття")
    assert "передчуття" not in out.lower()
    assert "не хотілося" in out.lower()


def test_stiffness_triggers_force_llm():
    from engines.naturalizer_v2.literary_uk import (
        is_stiff_uk,
        should_force_literary_llm,
    )

    stiff = "купив невеликий італійський автомобіль, який називається Фіат"
    assert is_stiff_uk(stiff)
    assert should_force_literary_llm(stiff, original="he bought a Fiat")


def test_v2_literary_with_large_slot():
    """Literary polish applies when CATP Extended (large reserve/slot)."""
    from engines.naturalizer_v2.orchestrator import polish_segment_v2

    raw = (
        "Тож він дійсно майже нічого серйозно не робив, окрім автомобілів, "
        "і батько купив невеликий італійський автомобіль, який називається Фіат."
    )
    res = polish_segment_v2(
        raw,
        original=(
            "he really had not pursued anything all that seriously except for cars. "
            "his father bought him a small Italian car called the Fiat."
        ),
        tgt_lang="uk",
        use_llm=False,
        slot_ms=12000,
        reserve_ms=2000,
    )
    text = res["text"]
    assert "який називається" not in text.lower()
    assert "по-справжньому серйозно" in text or "нічим" in text or "Fiat" in text or "Фіат" in text


def test_bad_mt_flags_literary_stiffness():
    from engines.naturalizer_v2.bad_patterns import has_bad_mt

    assert has_bad_mt("Джордж-молодший, відомий сьогодні, як Джордж Лукас")
    assert has_bad_mt("автомобіль, який називається Фіат")
