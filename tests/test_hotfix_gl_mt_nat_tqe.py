"""HOTFIX-GL-MT-NAT-TQE v1.1 — HF0–HF6 regression + anti-overfit."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "golden" / "dub"


# ── HF0: reproduction fixtures (bad Raw MT from GL run) ─────────────────────

GL_BAD_SEG1_EN = (
    "An 18-year-old boy named George Jr. drove through his hometown "
    "on his way home for dinner."
)
GL_BAD_SEG1_RAW = "18-річний Жр поїхав через міст додому на вечерю."  # broken Jr + hometown

GL_BAD_SEG2_EN = (
    "But, as he was driving, George Jr. could not help but feel like he was "
    "really dreading actually getting there. So George Jr. was a very smart kid, "
    "but he also got distracted really easily and because of that, he really had "
    "not pursued anything all that seriously that is except for cars. And at that "
    "point his father actually bought him a small Italian car called the Fiat."
)
GL_BAD_MEGA_EN = GL_BAD_SEG2_EN  # oversized multi-sentence


def test_hf0_dirty_noop_is_bug():
    from engines.mt.dirty_mt import naturalizer_noop_is_bug

    assert naturalizer_noop_is_bug(
        GL_BAD_SEG1_EN, GL_BAD_SEG1_RAW, GL_BAD_SEG1_RAW, tgt_lang="uk"
    )


def test_hf0_clean_noop_is_not_bug():
    from engines.mt.dirty_mt import naturalizer_noop_is_bug

    clean = "18-річний Джордж-молодший поїхав додому на вечерю через рідне місто."
    assert not naturalizer_noop_is_bug(GL_BAD_SEG1_EN, clean, clean, tgt_lang="uk")


# ── HF1: oversized guard ────────────────────────────────────────────────────

def test_hf1_oversized_split():
    from engines.mt.oversized_guard import (
        is_oversized_mt_unit,
        split_oversized_unit,
        translate_oversized_safely,
    )

    assert is_oversized_mt_unit(GL_BAD_MEGA_EN)
    parts = split_oversized_unit(GL_BAD_MEGA_EN)
    assert len(parts) >= 2
    assert all(len(p) < len(GL_BAD_MEGA_EN) for p in parts)

    calls: list[str] = []

    def _tr(chunk: str) -> str:
        calls.append(chunk)
        return f"UK:{chunk[:20]}"

    out = translate_oversized_safely(GL_BAD_MEGA_EN, _tr)
    assert len(calls) >= 2
    assert out.startswith("UK:")


def test_hf1_short_not_split():
    from engines.mt.oversized_guard import is_oversized_mt_unit, split_oversized_unit

    short = "Hello world."
    assert not is_oversized_mt_unit(short)
    assert split_oversized_unit(short) == [short]


# ── HF2: project glossary ───────────────────────────────────────────────────

def test_hf2_glossary_canonical_jr():
    from engines.project_glossary import load_project_glossary

    g = load_project_glossary(app_dir=ROOT)
    assert g.canonical_for("George Jr.") == "Джордж-молодший"
    assert g.canonical_for("USC") == "USC"
    assert g.canonical_for("Star Wars") == "Зоряні війни"


def test_hf2_glossary_entity_check():
    from engines.project_glossary import check_glossary_entities, load_project_glossary

    g = load_project_glossary(app_dir=ROOT)
    missing = check_glossary_entities(
        GL_BAD_SEG1_EN, "Хлопець поїхав додому.", g
    )
    assert "George Jr." in missing or any("George" in m for m in missing)


def test_hf2_glossary_accepts_uk_inflected_forms():
    """Locative/instrumental surfaces must not false-fail Fast QA."""
    from engines.project_glossary import (
        check_glossary_entities,
        clear_glossary_cache,
        load_project_glossary,
    )

    clear_glossary_cache()
    g = load_project_glossary(app_dir=ROOT)
    # dinner compressor → рідним містом; ICU often locative відділенні…
    s1 = (
        "An 18-year-old boy named George Jr. drove through his hometown "
        "on his way home for dinner."
    )
    t1 = "18-річний Джордж-молодший їхав рідним містом додому на вечерю."
    assert check_glossary_entities(s1, t1, g) == []

    s3 = (
        "Two weeks later, George Jr. was laying in a hospital bed in the "
        "intensive care unit at the local hospital."
    )
    t3 = (
        "Через два тижні Джордж-молодший лежав у відділенні інтенсивної "
        "терапії місцевої лікарні."
    )
    assert check_glossary_entities(s3, t3, g) == []


def test_hf2_mask_restore_uses_glossary():
    from engines.naturalizer_v2.entity_tokens import mask_entities, restore_entities

    masked, tokmap = mask_entities(GL_BAD_SEG1_EN, app_dir=ROOT)
    assert tokmap  # George Jr. / hometown terms masked
    restored, notes = restore_entities(
        "18-річний TOKEN поїхав.",
        {list(tokmap.keys())[0]: list(tokmap.values())[0]} if tokmap else {},
        original=GL_BAD_SEG1_EN,
        tgt_lang="uk",
        app_dir=ROOT,
    )
    # At least restore path does not crash
    assert isinstance(restored, str)


# ── HF3: naturalizer dirty force ────────────────────────────────────────────

def test_hf3_naturalizer_repairs_dirty_jr():
    from engines.translation_naturalizer import polish_lines

    meta: list[dict] = []
    out = polish_lines(
        [GL_BAD_SEG1_RAW],
        source_segments=[GL_BAD_SEG1_EN],
        tgt_lang="uk",
        src_lang="en",
        use_llm=False,
        app_dir=ROOT,
        naturalizer_meta_out=meta,
    )
    text = out[0]
    assert "Жр" not in text
    # hometown fix when dirty
    assert "міст" not in text or "рідне місто" in text.lower()
    assert meta and meta[0].get("dirty_mt", {}).get("dirty") is True


def test_hf3_dirty_noop_fails_fast_qa():
    from engines.tps.fast_qa import run_fast_qa

    qa = run_fast_qa(
        GL_BAD_SEG1_EN,
        GL_BAD_SEG1_RAW,
        context={
            "target_lang": "uk",
            "raw_mt": GL_BAD_SEG1_RAW,
            "naturalized": GL_BAD_SEG1_RAW,
            "app_dir": str(ROOT),
        },
    )
    assert not qa.passed
    assert "dirty_mt_noop" in qa.reason_codes or "entity_missing" in qa.reason_codes


def test_hf3_residual_calque_after_cosmetic_fix_is_bug():
    """Entity-only patches must not hide residual Argos calques."""
    from engines.mt.dirty_mt import naturalizer_noop_is_bug
    from engines.tps.fast_qa import run_fast_qa

    raw = (
        "Але, як він їхав, Джордж Жр не може допомогти, але відчувати себе, "
        "як він був дійсно зі страхом очікував насправді отримати там."
    )
    # Cosmetic Jr fix only — still unusable Ukrainian
    nat = (
        "Але, як він їхав, Джордж-молодший не може допомогти, але відчувати себе, "
        "як він був дійсно зі страхом очікував насправді отримати там."
    )
    assert naturalizer_noop_is_bug(GL_BAD_SEG2_EN, raw, nat, tgt_lang="uk")
    qa = run_fast_qa(
        GL_BAD_SEG2_EN,
        nat,
        context={"target_lang": "uk", "raw_mt": raw, "naturalized": nat},
    )
    assert not qa.passed
    assert any(
        c in qa.reason_codes for c in ("dirty_mt_noop", "nonsense_calque", "en_word_leak")
    )


def test_hf3_dative_subject_and_could_not_help_calques():
    from engines.translation_naturalizer import polish_lines

    raw = (
        "18-річному хлопчику ім. Георга Жр. поїхав через рідний міст "
        "на своєму шляху додому."
    )
    out = polish_lines(
        [raw],
        source_segments=[GL_BAD_SEG1_EN],
        tgt_lang="uk",
        src_lang="en",
        use_llm=False,
        app_dir=ROOT,
    )[0]
    assert "річному" not in out
    assert "хлопчику" not in out
    assert "Жр" not in out
    assert "хлопець" in out.lower() or "Джордж" in out

    calque = (
        "Джордж-молодший не може допомогти, але відчувати себе, як він був "
        "дійсно зі страхом очікував насправді отримати там."
    )
    out2 = polish_lines(
        [calque],
        source_segments=[GL_BAD_SEG2_EN],
        tgt_lang="uk",
        src_lang="en",
        use_llm=False,
        app_dir=ROOT,
    )[0]
    assert "не може допомогти" not in out2
    assert "насправді отримати там" not in out2


# ── HF4: TQE multi-factor (not score-only) ───────────────────────────────────

def test_hf4_low_score_alone_does_not_reject():
    from engines.tqe.decision import decide_segment
    from engines.tqe.models import ConfidenceMetrics, QualityReport, ReviewStatus

    # No critical errors, only low confidence
    report = QualityReport(
        reviewer_name="dummy",
        status=ReviewStatus.PASS,
        errors=[],
        confidence=ConfidenceMetrics(
            entity_preservation=0.5,
            meaning_coverage=0.5,
            grammar_integrity=0.5,
            sentence_completeness=0.5,
            narrative_integrity=0.5,
            timing_fitness=0.5,
        ),
        explanation="soft",
    )
    decision = decide_segment(
        index=0,
        original="Hello",
        translation="Привіт.",
        reports=[report],
        threshold=0.9,
    )
    assert decision.allowed_for_tts is True
    assert "soft:low_confidence" in decision.explanation


def test_hf4_entity_missing_rejects_even_high_score():
    from engines.tqe.decision import decide_segment
    from engines.tqe.models import ConfidenceMetrics, QualityReport, ReviewStatus

    report = QualityReport(
        reviewer_name="entity",
        status=ReviewStatus.PASS,
        errors=[{"code": "entity_missing", "severity": "critical", "token": "George Jr."}],
        confidence=ConfidenceMetrics(
            entity_preservation=0.95,
            meaning_coverage=0.95,
            grammar_integrity=0.95,
            sentence_completeness=0.95,
            narrative_integrity=0.95,
            timing_fitness=0.95,
        ),
        explanation="",
    )
    decision = decide_segment(
        index=0,
        original=GL_BAD_SEG1_EN,
        translation="Хлопець поїхав.",
        reports=[report],
        threshold=0.5,
    )
    assert decision.allowed_for_tts is False


# ── HF5: mid-name punct + DSAL visibility ───────────────────────────────────

def test_hf5_mid_name_period_removed():
    from engines.dsal.pre_lock_polish import polish_false_name_period

    bad = "Джордж-молодший. він поїхав додому."
    fixed = polish_false_name_period(bad)
    assert "молодший. в" not in fixed.lower()
    assert "молодший він" in fixed.lower() or "молодший  він" in fixed.lower()


def test_hf5_dsal_skip_reason_stamped():
    from engines.dsal.core import DSALResult, analyze_duration, stamp_dsal_on_segment

    seg: dict = {}
    analysis = analyze_duration(slot_ms=5000, text="Привіт.", tgt_lang="uk")
    result = DSALResult(
        text="Привіт.",
        changed=False,
        analysis=analysis,
        adaptation_executed=False,
        method="duration_only",
        detail="post-APPROVED stamp; text immutable",
    )
    stamp_dsal_on_segment(seg, result)
    assert seg.get("dsal_applied") is False
    assert seg.get("dsal_skip_reason")


# ── HF6: George Lucas checklist + anti-overfit suites ───────────────────────

def test_hf6_gl_checklist_seg1_naturalizer():
    from engines.translation_naturalizer import polish_lines

    out = polish_lines(
        [GL_BAD_SEG1_RAW],
        source_segments=[GL_BAD_SEG1_EN],
        tgt_lang="uk",
        src_lang="en",
        use_llm=False,
        app_dir=ROOT,
    )[0]
    assert "Жр" not in out
    assert "Джордж" in out or "молодший" in out
    assert "міст" not in out or "рідне місто" in out.lower()


def test_hf6_gl_checklist_no_mid_dot():
    from engines.dsal.pre_lock_polish import polish_false_name_period

    text = polish_false_name_period(
        "Джордж-молодший. розповів Хаскелу про USC і Зоряні війни."
    )
    assert not __import__("re").search(r"молодший\.\s+[а-яіїєґ]", text)


def test_hf6_anti_overfit_suites_load():
    """Other golden suites exist and are not George-Lucas-only."""
    manifest = json.loads((GOLDEN / "manifest.json").read_text(encoding="utf-8"))
    ids = {e["id"] for e in manifest["entries"]}
    assert "george_lucas_en_uk_20" in ids
    assert "short_dialog_en_uk_anti_overfit" in ids
    assert "tech_terms_en_uk_anti_overfit" in ids
    for entry in manifest["entries"]:
        path = GOLDEN / entry["path"]
        assert path.is_file(), entry["path"]
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data.get("segments")


def test_hf6_anti_overfit_short_dialog_naturalizer_safe():
    """GL hotfix must not inject GL entities into unrelated dialog."""
    from engines.translation_naturalizer import polish_lines

    data = json.loads(
        (GOLDEN / "short_dialog_en_uk_anti_overfit.json").read_text(encoding="utf-8")
    )
    for seg in data["segments"]:
        raw = "Доброго ранку. Як ти сьогодні?"
        out = polish_lines(
            [raw],
            source_segments=[seg["en"]],
            tgt_lang="uk",
            src_lang="en",
            use_llm=False,
            app_dir=ROOT,
        )[0]
        for banned in seg.get("uk_must_not") or []:
            assert banned not in out, f"injected {banned} into anti-overfit suite"


def test_hf6_temporary_repair_has_todo_tickets():
    from engines.mt.dirty_mt import _TEMP_ENTITY_REPAIRS

    assert _TEMP_ENTITY_REPAIRS
    for _pat, _repl, ticket in _TEMP_ENTITY_REPAIRS:
        assert ticket.startswith("TODO:"), ticket


# ── HF7: residual grammar that previously locked into Review/TTS ────────────

def test_hf7_na_imya_takes_nominative():
    from engines.translation_naturalizer import polish_lines

    raw = (
        "18-річному хлопчику ім. Георга Жр. поїхав через рідний міст "
        "на своєму шляху додому."
    )
    out = polish_lines(
        [raw],
        source_segments=[GL_BAD_SEG1_EN],
        tgt_lang="uk",
        src_lang="en",
        use_llm=False,
        app_dir=ROOT,
    )[0]
    assert "на ім'я Джордж-молодший" in out or "на ім’я Джордж-молодший" in out
    assert "на ім'я Джорджа-молодшого" not in out
    assert "на ім’я Джорджа-молодшого" not in out


def test_hf7_gender_agreement_and_smash_verb():
    from engines.translation_naturalizer import polish_lines

    raw = (
        "батько купив йому невеликий італійський автомобіль, яка називається Fiat, "
        "і ще один автомобіль на великій швидкості промчала дорогою і "
        "розім'ятити в машину Джорджа так важко"
    )
    out = polish_lines(
        [raw],
        source_segments=[GL_BAD_SEG2_EN],
        tgt_lang="uk",
        src_lang="en",
        use_llm=False,
        app_dir=ROOT,
    )[0]
    assert "автомобіль, яка" not in out
    # Gender fix («який») OR literary drop of EN «which is called»
    assert (
        "автомобіль, який" in out
        or "автомобіль Fiat" in out
        or "автомобіль Фіат" in out
        or ("називається" not in out.lower() and ("Fiat" in out or "Фіат" in out))
    )
    assert "розім'ятити" not in out
    assert "врізався" in out or "врізалася" in out
    assert "промчала" not in out
    assert "Fiat" in out or "Фіат" in out


def test_hf7_double_past_and_synonym_stack():
    from engines.translation_naturalizer import polish_lines
    from engines.mt.dirty_mt import residual_dirty_after_naturalize

    raw = (
        "Джордж-молодший який на той момент був повністю одужав після травм, "
        "стояв на фінішній прямій на гоночний трек. З того часу, як його майже "
        "смертельний досвід, він зрозумів, що батько був певною мірою правий мав рацію."
    )
    en = (
        "Two years later, George Jr. who by this point was fully recovered from his "
        "injuries, stood at the finish line at a race track. Since his near-death "
        "experience, he realized his dad had been kind of right."
    )
    out = polish_lines(
        [raw],
        source_segments=[en],
        tgt_lang="uk",
        src_lang="en",
        use_llm=False,
        app_dir=ROOT,
    )[0]
    assert "був повністю одужав" not in out
    assert "повністю одужав" in out
    assert "правий мав рацію" not in out
    assert "гоночного треку" in out or "гоночному треку" in out
    assert "З того часу, як його майже смертельний досвід" not in out
    assert not residual_dirty_after_naturalize(en, out, tgt_lang="uk")


def test_hf7_residuals_fail_fast_qa_until_fixed():
    from engines.mt.dirty_mt import naturalizer_noop_is_bug
    from engines.tps.fast_qa import run_fast_qa

    dirty_nat = (
        "18-річний хлопець на ім'я Джорджа-молодшого проїжджав через рідне місто."
    )
    assert naturalizer_noop_is_bug(GL_BAD_SEG1_EN, dirty_nat, dirty_nat, tgt_lang="uk")
    qa = run_fast_qa(
        GL_BAD_SEG1_EN,
        dirty_nat,
        context={
            "target_lang": "uk",
            "raw_mt": dirty_nat,
            "naturalized": dirty_nat,
        },
    )
    assert not qa.passed


def test_hf7_star_wars_franchise_identity():
    from engines.translation_naturalizer import polish_lines

    en = (
        "George Jr. is better known today as George Lucas and his film franchise "
        "will star wars."
    )
    raw = (
        "Джордж-молодший, відомий сьогодні, як Джордж Лукас і його кінофраншиза "
        "стане «Зоряними війнами»."
    )
    out = polish_lines(
        [raw],
        source_segments=[en],
        tgt_lang="uk",
        src_lang="en",
        use_llm=False,
        app_dir=ROOT,
    )[0]
    assert "«Зоряні війни»" in out
    assert "стане «Зоряними війнами»" not in out


def test_hf8_crash_mt_garbage_naturalized():
    from engines.translation_naturalizer import naturalize_uk

    raw = (
        "Так два тижні раніше, коли Джордж зробив це, а потім щось відбулося, "
        "Так само, що це ще один автомобіль на великій швидкості промчав дорогою "
        "і розім'яти на автомобілі Юра так сильно, що Джордж-молодший був "
        "вказаний з автомобіля, але вижив"
    )
    out = naturalize_uk(raw)
    assert "розім'яти" not in out
    assert "Юра" not in out
    assert "вказаний" not in out
    assert "врізалася" in out or "вилетів" in out


def test_hf8_near_death_not_appended_when_paraphrased():
    from engines.dsal.clause_coverage import restore_missing_clauses
    from engines.dsal.pre_lock_polish import strip_orphan_clause_tails

    en = (
        "So since his near-death experience, George Jr. had realized that really "
        "has dad had been kind of right that in some ways he was wasting his potential."
    )
    uk = (
        "Після майже смертельного досвіду, Джордж-молодший зрозумів, що його батько "
        "був певною мірою правий, що в деяких випадках він марнував свій потенціал."
    )
    restored, cov = restore_missing_clauses(uk, en)
    assert "досвід на межі смерті" not in restored
    assert cov.coverage >= 1.0 or not cov.missing

    polluted = uk.rstrip(".") + ", досвід на межі смерті"
    clean = strip_orphan_clause_tails(polluted, original=en)
    assert "досвід на межі смерті" not in clean
