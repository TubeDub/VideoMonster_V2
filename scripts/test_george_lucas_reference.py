"""Reference-style Ukrainian fixes for George Lucas dub (en→uk)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_george_jr_hyphen():
    from engines.naturalizer_v2.uk_name_forms import normalize_george_jr_uk

    out = normalize_george_jr_uk("Джордж молодший їхав. Джорджа молодшого викинуло.")
    assert "Джордж-молодший" in out
    assert "Джорджа-молодшого" in out


def test_sanitize_jr_not_lucas():
    from engines.naturalizer_v2.entity_fixup import sanitize_wrong_entity_substitutions

    src = "George Jr. drove through his hometown."
    bad = "18-річний хлопчик названий George Lucas поїхав додому."
    out = sanitize_wrong_entity_substitutions(bad, original=src, tgt_lang="uk")
    assert "George Lucas" not in out
    assert "Джордж-молодший" in out


def test_calque_segment1():
    from engines.translation_naturalizer import _polish_v1_rules

    raw = (
        "18-річний хлопчик названий George Lucas поїхав через рідний міст "
        "на своєму шляху додому."
    )
    src = (
        "An 18-year-old boy named George Jr. drove through his hometown "
        "on his way home for dinner."
    )
    res = _polish_v1_rules(raw, original=src, tgt_lang="uk", use_llm=False)
    assert "George Lucas" not in res.text
    assert "хлопець" in res.text or "Джордж-молодший" in res.text


def test_calque_driving():
    from engines.translation_naturalizer import _polish_v1_rules

    raw = "Але, як він їхав, George Lucas не міг не відчувати, що він дійсно боїться потрапити туди."
    src = "But, as he was driving, George Jr. could not help but feel like he was really dreading actually getting there."
    res = _polish_v1_rules(raw, original=src, tgt_lang="uk", use_llm=False)
    assert "George Lucas" not in res.text
    assert "поки він їхав" in res.text or "страшно" in res.text or "позбутися" in res.text


def test_usc_not_star_wars():
    from engines.naturalizer_v2.entity_fixup import sanitize_wrong_entity_substitutions

    src = "George Jr. told Haskell about how he had recently applied to USC."
    bad = "George Lucas розповів Хаскеллу про те, як він звернувся до Star Wars."
    out = sanitize_wrong_entity_substitutions(bad, original=src, tgt_lang="uk")
    assert "Star Wars" not in out
    assert "Університет" in out


def test_star_wars_quote_idempotent():
    from engines.naturalizer_v2.uk_name_forms import (
        STAR_WARS_UK,
        apply_uk_dub_name_polish,
        normalize_star_wars_uk,
    )

    storm = "буде ««««Зоряні війни»»»»"
    out = normalize_star_wars_uk(storm)
    assert out.count("«") == 1
    assert out.count("»") == 1
    assert out.endswith(STAR_WARS_UK)
    again = apply_uk_dub_name_polish(out, original="his film franchise will star wars.")
    assert again.count("«") == 1
    assert STAR_WARS_UK in again


def test_calque_hospital():
    from engines.translation_naturalizer import _polish_v1_rules

    raw = "Через два тижні, Джордж-молодший було прокладене в стаціонарному комплексі в місцевій лікарні."
    src = "Two weeks later, George Jr. was laying in a hospital bed in the intensive care unit at the local hospital."
    res = _polish_v1_rules(raw, original=src, tgt_lang="uk", use_llm=False)
    assert "прокладен" not in res.text
    assert "ліжку" in res.text or "лежав" in res.text


def test_calque_dad_had():
    from engines.translation_naturalizer import _polish_v1_rules

    raw = "Джордж-молодший зрозумів, що дійсно має дadу, що в деяких випадках він марнує свій потенціал."
    src = "George Jr. had realized that really his dad had been kind of right that in some ways he was wasting his potential."
    res = _polish_v1_rules(raw, original=src, tgt_lang="uk", use_llm=False)
    assert "дad" not in res.text.lower()
    assert "батько" in res.text


def main() -> int:
    test_george_jr_hyphen()
    test_sanitize_jr_not_lucas()
    test_calque_segment1()
    test_calque_driving()
    test_usc_not_star_wars()
    test_star_wars_quote_idempotent()
    test_calque_hospital()
    test_calque_dad_had()
    print("george lucas reference tests: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
