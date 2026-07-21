"""TZ v4.0 P1 — clause restore, block merge, QA, Golden George Lucas."""

from __future__ import annotations

import json
from pathlib import Path

GOLDEN = Path(__file__).resolve().parent / "golden" / "dub" / "george_lucas_en_uk_20.json"


def _load_golden() -> dict:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_clause_restore_seg5_skips_orphan_asr_tail():
    from engines.dsal import adapt_duration_semantic

    g = _load_golden()
    seg = next(s for s in g["segments"] if s["index"] == 5)
    res = adapt_duration_semantic(
        seg["uk"],
        source_hint=seg["en"],
        slot_ms=seg["slot_ms"],
        tgt_lang="uk",
    )
    low = res.text.lower()
    assert ", і за кожною вечерею, і велика суперечка" not in low
    assert "майже кожної вечері" not in low or "справжню роботу" in low


def test_clause_restore_seg6_father_son_prefix():
    from engines.dsal import adapt_duration_semantic

    g = _load_golden()
    seg = next(s for s in g["segments"] if s["index"] == 6)
    res = adapt_duration_semantic(
        seg["uk"],
        source_hint=seg["en"],
        slot_ms=seg["slot_ms"],
        tgt_lang="uk",
        actual_tts_ms=seg.get("actual_tts_ms"),
    )
    assert res.text.lower().startswith("між батьком і сином")
    assert "між батьком і сином" in res.text.lower()
    assert not res.text.lower().endswith("між батьком і сином.")
    assert abs(res.analysis.delta_pct) <= float(seg.get("delta_pct_max") or 15) or (
        abs(res.analysis.delta_ms) < 1500
    )
    assert res.adaptation_executed is True


def test_compute_clause_coverage_reports_missing():
    from engines.dsal import compute_clause_coverage

    cov = compute_clause_coverage(
        "every dinner these days, if he came this huge argument",
        "Він просто не розумів одержимості.",
    )
    assert cov.coverage < 0.85
    assert cov.missing


def test_incomplete_sentence_false_positive_when_source_cut():
    from engines.semantic_meaning import verify_meaning_preserved

    src = (
        "he just didn't get his son's obsession with cars, like why aren't you "
        "able to take that focus and apply it to other things, so we'll get your "
        "real job. And so basically every dinner these days, if he came this huge argument"
    )
    uk = (
        "Він просто не розумів одержимості свого сина автомобілями, "
        "як чому б тобі не взяти фокус і не застосувати його до інших речей"
    )
    ok, reason, _ = verify_meaning_preserved(src, uk, uk)
    assert reason != "incomplete_sentence"
    assert ok or reason in ("preserved_token", "lost_actor", "ok", "unchanged")


def test_semantic_block_merge_two_red_segments():
    from engines.dsal import apply_semantic_block_merges

    segments = [
        {
            "slot_ms": 5600,
            "final_text": (
                "Через два тижні Джордж-молодший лежав у лікарняному ліжку "
                "у відділенні інтенсивної терапії місцевої лікарні і ще довго "
                "розповідав лікарям усі деталі аварії."
            ),
            "tts_ms": 6932,
            "dsal_band": "red",
        },
        {
            "slot_ms": 5000,
            "final_text": "Так два тижні раніше все почалося.",
            "tts_ms": 2000,
            "dsal_band": "red",
        },
    ]
    sources = [
        "Two weeks later George was in hospital intensive care.",
        "So two weeks earlier when George was making that turn.",
    ]
    result = apply_semantic_block_merges(
        segments, source_segments=sources, tgt_lang="uk"
    )
    assert result.merged_blocks >= 1
    assert segments[0].get("block_merge_semantic")


def test_golden_george_lucas_20_dsal_llm_off(monkeypatch):
    """Golden CI: all 20 segs adapt without LLM; #5/#6 clauses; #6 delta."""
    monkeypatch.setattr(
        "engines.translation_adapt.llm_rephrase_available",
        lambda: False,
    )
    from engines.translation_validation import apply_dsal_before_lock

    g = _load_golden()
    info = {
        "target_lang": "uk",
        "source_segments": [s["en"] for s in g["segments"]],
        "segments_data": [
            {
                "slot_ms": s["slot_ms"],
                "final_text": s["uk"],
                "tts_ms": s.get("actual_tts_ms") or 0,
            }
            for s in g["segments"]
        ],
    }
    summary = apply_dsal_before_lock(info)
    assert summary["adapted"] >= 1

    # Per-segment must_restore / must_contain after DSAL
    for spec, seg in zip(g["segments"], info["segments_data"]):
        text = str(seg.get("final_text") or "").lower()
        starts = spec.get("must_start_with")
        if isinstance(starts, str):
            starts = [starts]
        for phrase in starts or []:
            assert text.startswith(phrase.lower()), f"seg#{spec['index']} should start with {phrase}"
        for phrase in spec.get("must_restore") or []:
            assert phrase.lower() in text, f"seg#{spec['index']} missing {phrase}"
        for phrase in spec.get("must_contain") or []:
            assert phrase.lower() in text, f"seg#{spec['index']} missing {phrase}"

    # Seg #6 residual
    seg6 = info["segments_data"][5]
    assert seg6.get("adaptation_executed") or seg6.get("dsal_applied")
    delta_pct = abs(float(seg6.get("dsal_delta_ms") or 0)) / max(
        1, float(seg6.get("slot_ms") or 1)
    )
    # After adapt, either tight delta or at least expanded from 2999ms hole
    assert delta_pct <= 0.15 or abs(int(seg6.get("dsal_delta_ms") or 0)) < 1500
    assert "між батьком і сином" in str(seg6.get("final_text") or "").lower()


def test_review_includes_dsal_fields():
    from engines.translation_review import build_translation_review

    info = {
        "source_lang": "en",
        "target_lang": "uk",
        "source_segments": ["between father and son near home."],
        "segments_data": [
            {
                "final_text": "між батьком і сином біля дому.",
                "slot_ms": 12160,
                "dsal_delta_ms": 400,
                "dsal_band": "green",
                "dsal_applied": True,
                "duration_match_score": 92,
                "clause_coverage": 1.0,
                "expand_required": False,
            }
        ],
        "translation_audits": [{}],
    }
    review = build_translation_review(info)
    row = review["segments"][0]
    assert row["slot_ms"] == 12160
    assert row["dsal_band"] == "green"
    assert row["dsal_applied"] is True
    assert row["duration_match_score"] == 92


def test_dsal_expand_never_adds_elaboration_fillers():
    from engines.dsal import adapt_duration_semantic

    text = (
        "Джордж-молодший був дуже розумною дитиною, але також він дуже легко "
        "відволікався, і через це він справді не займався чимось серйозним, "
        "окрім автомобілів."
    )
    res = adapt_duration_semantic(
        text,
        source_hint=(
            "So George Jr. was a very smart kid, but he also got distracted "
            "really easily and because of that, he really had not pursued "
            "anything all that seriously that is except for cars."
        ),
        slot_ms=11520,
        tgt_lang="uk",
    )
    low = res.text.lower()
    assert "саме в цей момент" not in low
    assert "у той самий час" not in low


def test_pre_lock_polish_restores_naspravdi_and_terminal():
    from engines.dsal.pre_lock_polish import apply_pre_lock_polish

    src = (
        "In fact, George Jr. had applied to the prestigious cinematography "
        "program at the University of Southern California, but after sending "
        "off his application, he was pretty sure he would not get in."
    )
    broken = (
        ", Джордж-молодший подав заяву на престижну програму з кінематографії "
        "з Університету Південної Каліфорнії, але після того, як надіслав заяву, "
        "він був цілком упевнений, що його не приймуть до неї"
    )
    out = apply_pre_lock_polish(broken, original=src)
    assert out.startswith("Насправді,")
    assert out.endswith(".")


def test_pre_lock_polish_strips_dsal_fillers():
    from engines.dsal.pre_lock_polish import apply_pre_lock_polish

    src = "It also would go on to completely alter cinema in general forever."
    broken = (
        "Це також повністю змінити кіно. Його фільм буде саме в цей момент "
        "насправді у той самий час"
    )
    out = apply_pre_lock_polish(broken, original=src)
    assert "саме в цей момент" not in out.lower()
    assert "у той самий час" not in out.lower()


def test_strip_fillers_keeps_sentence_initial_naspravdi():
    from engines.dsal.core import strip_dsal_elaboration_fillers

    text = "Насправді, Джордж подав заяву, і це було насправді важливо саме в цей момент"
    out = strip_dsal_elaboration_fillers(text)
    assert out.startswith("Насправді,")
    assert "саме в цей момент" not in out.lower()


def test_pre_lock_polish_jr_period_vin_and_pro():
    from engines.dsal.pre_lock_polish import apply_pre_lock_polish

    out = apply_pre_lock_polish(
        "Тож Джордж-молодший. Ві́н бі́льше не хоче займатися автогонками",
        original="So George Jr. had decided that he really didn't want to race cars anymore.",
    )
    assert "Джордж-молодший. Ві" not in out
    assert "більше не хоче" in out.replace("\u0301", "") or "бі́льше не хоче" in out

    out2 = apply_pre_lock_polish(
        "І Джордж-молодший. Про те, як він подав заявку",
        original="And George Jr. told Haskell about how he had recently applied",
    )
    assert "розповів Хаскелу про те" in out2
    assert "Джордж-молодший. Про" not in out2


def test_pre_lock_polish_capitalizes_father_son_prefix():
    from engines.dsal.pre_lock_polish import apply_pre_lock_polish

    out = apply_pre_lock_polish(
        "між батьком і сином, Джордж під'їхав до перехрестя.",
        original="between father and son. And so George came to this intersection.",
    )
    assert out.startswith("Між батьком і сином")


def test_pre_lock_polish_moves_father_son_to_prefix():
    from engines.dsal.pre_lock_polish import apply_pre_lock_polish

    src = (
        "between father and son. And so George, he came to this intersection "
        "where it was right near his home, and he begins making the turn when he "
        "hears this really loud screeching sound and then everything went black."
    )
    broken = (
        "Джордж під'їхав до перехрестя, де він був біля його дому, і він почав "
        "повертати, коли він почув цей дуже гучний звук і потім все потемніло, "
        "між батьком і сином."
    )
    out = apply_pre_lock_polish(broken, original=src)
    assert out.lower().startswith("між батьком і сином")
    assert not out.lower().rstrip(".").endswith("між батьком і сином")


def test_pre_lock_polish_strips_dinner_orphan_tail():
    from engines.dsal.pre_lock_polish import apply_pre_lock_polish

    src = (
        "he just didn't get his son's obsession with cars, like why aren't you "
        "able to take that focus and apply it to other things, so we'll get your "
        "real job. And so basically every dinner these days, if he came this huge argument"
    )
    broken = (
        "Він просто не розумів одержимості свого сина автомобілями, як чому б тобі "
        "не взяти фокус і не застосувати його до інших речей, отримаєш справжню "
        "роботу, і за кожною вечерею, і велика суперечка"
    )
    out = apply_pre_lock_polish(broken, original=src)
    assert "за кожною вечерею" not in out.lower()
    assert "велика суперечка" not in out.lower()
    assert "справжню роботу" in out.lower()
