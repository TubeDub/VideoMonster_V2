"""Residuals from live GL Review dump ь.json (2026-07-18)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dinner_restored_on_hometown_line():
    from engines.translation_naturalizer import polish_lines

    en = (
        "An 18-year-old boy named George Jr. drove through his hometown "
        "on his way home for dinner."
    )
    raw = (
        "18-річний хлопець на ім'я Джордж-молодший проїжджав через своє "
        "рідне місто дорогою додому."
    )
    out = polish_lines(
        [raw],
        source_segments=[en],
        tgt_lang="uk",
        src_lang="en",
        use_llm=False,
        app_dir=ROOT,
        slot_ms_list=[3200],
    )[0]
    assert "на вечерю" in out.lower()
    assert "рідн" in out.lower()
    # Must be compact enough for ~3.2s slot (long form was hard-clipped)
    assert "хлопець на ім" not in out.lower()
    assert "проїжджав" not in out.lower()
    assert len(out) < 90


def test_ejected_genitive():
    from engines.naturalizer_v2.uk_name_forms import apply_uk_dub_name_polish

    out = apply_uk_dub_name_polish(
        "що Джордж-молодший викинуло з автомобіля, але він вижив.",
        original="that George Jr. was ejected from the car but he had survived.",
    )
    assert "Джорджа-молодшого викинуло" in out
    assert "Джордж-молодший викинуло" not in out


def test_take_some_photos_and_introduce():
    from engines.translation_naturalizer import polish_lines

    en = (
        "Now, George Jr. walked over to the podium to take some photos of the winning drive. "
        "the man actually formally introduced himself as Haskell Wexler."
    )
    raw = (
        "Тепер Джордж-молодший пішов до подіуму, щоб взяти деякі фото переможного гонщика. "
        "згодом людина фактично офіційно представився як Хаскелл Векслер."
    )
    out = polish_lines(
        [raw],
        source_segments=[en],
        tgt_lang="uk",
        src_lang="en",
        use_llm=False,
        app_dir=ROOT,
        slot_ms_list=[12000],
    )[0]
    assert "взяти деякі" not in out.lower()
    assert "зробити кілька фото" in out.lower() or "фото переможного гонщика" in out.lower()
    assert "людина фактично" not in out.lower()


def test_usc_stays_compact():
    from engines.naturalizer_v2.uk_name_forms import apply_uk_dub_name_polish

    out = apply_uk_dub_name_polish(
        "подав заявку до Університету Південної Каліфорнії, я знаю людей з Університету Південної Каліфорнії",
        original="applied to USC. I know people at USC.",
    )
    assert "Університет" not in out
    assert "USC" in out


def test_po_suti_and_application_calque():
    from engines.translation_naturalizer import polish_lines

    en = (
        "In fact, George Jr. had applied to the prestigious cinematography program "
        "at the University of Southern California, but after sending off his application, "
        "he was pretty sure he would not get in."
    )
    raw = (
        "Насправді, По суті, Джордж-молодший подав заявку на престижну програму "
        "кінематографії в Університеті Південної Каліфорнії, але після відправки "
        "його заявки він був досить впевненим, що його не візьмуть."
    )
    out = polish_lines(
        [raw],
        source_segments=[en],
        tgt_lang="uk",
        src_lang="en",
        use_llm=False,
        app_dir=ROOT,
        slot_ms_list=[11000],
    )[0]
    assert "По суті" not in out
    assert "після відправки" not in out.lower()
    assert "USC" in out


def test_george_life_redundancy():
    from engines.naturalizer_v2.uk_name_forms import apply_uk_dub_name_polish

    out = apply_uk_dub_name_polish(
        "Але цей момент у житті Джорджа-молодшого не просто змінив життя Джорджа назавжди.",
        original="But this moment in George Jr's life did not just alter George's life forever.",
    )
    assert "життя Джорджа назавжди" not in out
    assert "його життя" in out.lower()


def test_winning_drive_and_usa_as_usc():
    from engines.translation_naturalizer import polish_lines

    en = (
        "Now, George Jr. walked over to the podium to take some photos of the winning drive. "
        "he said, George, I know people at USC."
    )
    raw = (
        "Тепер Джордж-молодший пішов до подіуму, щоб зробити кілька фото виграшного приводу. "
        "А коли Хаскелл почув це, він сказав, Джордж, я знаю людей у США."
    )
    out = polish_lines(
        [raw],
        source_segments=[en],
        tgt_lang="uk",
        src_lang="en",
        use_llm=False,
        app_dir=ROOT,
        slot_ms_list=[15000],
    )[0]
    assert "виграшного приводу" not in out.lower()
    assert "переможного гонщика" in out.lower()
    assert "США" not in out
    assert "USC" in out


def test_comma_before_rel_clause():
    from engines.translation_naturalizer import _polish_v1_rules

    res = _polish_v1_rules(
        "Через два роки Джордж-молодший який на той момент повністю одужав після травм, стояв.",
        original="Two years later, George Jr. who by this point was fully recovered",
        tgt_lang="uk",
        use_llm=False,
    )
    assert "Джордж-молодший, який" in res.text


def test_usc_satisfies_university_entity_tokens():
    from engines.translation_quality import missing_preserved_tokens

    en = (
        "George Jr. had applied to the prestigious cinematography program "
        "at the University of Southern California"
    )
    uk = "Джордж-молодший подав заявку на програму кінематографії в USC"
    missing = missing_preserved_tokens(en, uk, app_dir=ROOT)
    assert "University" not in missing
    assert "Southern" not in missing
    assert "California" not in missing
    assert not any("University of Southern" in m for m in missing)


def test_let_me_make_calls_not_entity_warning():
    from engines.translation_quality import missing_preserved_tokens

    missing = missing_preserved_tokens(
        "Let me make some calls.",
        "Я зроблю кілька дзвінків.",
        app_dir=ROOT,
    )
    assert "Let" not in missing


def test_jr_dangling_dinner_and_ejected_calques():
    from engines.translation_naturalizer import polish_lines

    out1 = polish_lines(
        [
            "18-річний Джордж-молодший їхав рідним містом додому на вечерю. "
            "Але коли він їхав, Джордж-молодший."
        ],
        source_segments=[
            "An 18-year-old boy named George Jr. drove through his hometown "
            "on his way home for dinner. But, as he was driving, George Jr."
        ],
        tgt_lang="uk",
        src_lang="en",
        use_llm=False,
        app_dir=ROOT,
        slot_ms_list=[5000],
    )[0]
    assert "Але коли він їхав" not in out1
    assert "на вечерю" in out1.lower()

    out2 = polish_lines(
        ["був в'язаний з автомобіля, але він зберіг."],
        source_segments=["was ejected from the car but he had survived."],
        tgt_lang="uk",
        src_lang="en",
        use_llm=False,
        app_dir=ROOT,
        slot_ms_list=[3000],
    )[0]
    assert "в'язаний" not in out2.lower()
    assert "вилетів" in out2.lower() or "вижив" in out2.lower()

    out3 = polish_lines(
        ["І вистачить, не довге після цієї долі наради, Джордж-молодший."],
        source_segments=["And sure enough, not long after this fateful meeting, George Jr."],
        tgt_lang="uk",
        src_lang="en",
        use_llm=False,
        app_dir=ROOT,
        slot_ms_list=[5000],
    )[0]
    assert "вистачить" not in out3.lower()
    assert "долі наради" not in out3.lower()


def test_dread_residue_and_seg7_calques():
    from engines.translation_naturalizer import polish_lines

    en2 = (
        "But, as he was driving, George Jr. could not help but feel like he was "
        "really dreading actually getting there."
    )
    raw2 = (
        "Але коли він їхав, Джордж-молодший його не полишала тривога, як він був "
        "дійсно зі страхом очікував насправді отримати там."
    )
    out2 = polish_lines(
        [raw2],
        source_segments=[en2],
        tgt_lang="uk",
        src_lang="en",
        use_llm=False,
        app_dir=ROOT,
        slot_ms_list=[8000],
    )[0]
    assert "тривога" not in out2.lower()
    assert "насправді отримати" not in out2.lower()
    assert "не хотілося" in out2.lower() or "не хотів" in out2.lower()

    # Exact live dump Raw MT (Jr form still «Джера» — calques must run post-name)
    en7 = (
        "Now, George Jr. walked over to the podium to take some photos of the winning drive. "
        "But as he walked over there, the smithelaged man came up beside him and just asked "
        "George Jr. about his photography and then at some point the man actually formally "
        "introduced himself as Haskell Wexler. And he said that he was actually a "
        "cinematographer in Hollywood. And George Jr. told Haskell about how he had recently "
        "applied to USC to try to get into their cinematography program. And when Haskell "
        "heard this, he said, George, I know people at USC."
    )
    raw7 = (
        "Тепер Джордж Джер. пішов над подіумом, щоб взяти деякі фотографії виграшного приводу. "
        "Але, як він прогулявся там, скотарний чоловік прийшов назустріч йому і просто попросив "
        "Джорджа Джера про свою фотографію, а потім в деякій точці людина фактично формально "
        "представилася себе як Haskell Wexler. І сказав він, що він насправді був "
        "кінематографістом в Голлівуді. Про те, як він нещодавно звернувся до USC, щоб "
        "спробувати потрапити в програму кінематографі. А коли Хаскелл чув це, він сказав, "
        "Джордж, я знаю людей у США."
    )
    out7 = polish_lines(
        [raw7],
        source_segments=[en7],
        tgt_lang="uk",
        src_lang="en",
        use_llm=False,
        app_dir=ROOT,
        slot_ms_list=[25000],
    )[0]
    assert "про свою фотографію" not in out7.lower()
    assert "Про те, як він" not in out7
    assert "розповів" in out7.lower()
    assert "США" not in out7


def test_calls_short_form_and_confidence_comma():
    from engines.translation_naturalizer import polish_lines

    out = polish_lines(
        [
            "Дозвольте мені зробити деякі дзвінки. "
            "після того як надіслав заявку він був досить впевненим, що його не візьмуть. "
            "врізався в машину Джорджа так важко, що Джорджа викинуло."
        ],
        source_segments=[
            "Let me make some calls. after sending off his application, "
            "he was pretty sure he would not get in. smashed so hard that George was ejected."
        ],
        tgt_lang="uk",
        src_lang="en",
        use_llm=False,
        app_dir=ROOT,
        slot_ms_list=[12000],
    )[0]
    assert "Я зроблю кілька дзвінків" in out
    assert "Дозвольте мені" not in out
    assert "після того, як" in out
    assert "майже впевнений" in out
    assert "так сильно, що" in out
