"""CATP v1.0 — Context-Aware Translation Polishing (timing-aware naturalizer)."""

from __future__ import annotations


def test_literary_fits_when_reserve_large():
    from engines.naturalizer_v2.catp import polish_with_budget

    safe = "Джорджа-молодшого не полишало відчуття, що він дуже боявся туди дістатися."
    lit = "йому зовсім не хотілося повертатися додому"
    res = polish_with_budget(
        baseline=safe,
        safe=safe,
        literary=lit,
        slot_ms=8000,
        reserve_ms=1500,
        lang="uk",
    )
    assert res.mode == "extended"
    assert "не хотілося" in res.text or res.selected_variant in ("A", "B", "C")
    assert res.rollback_due_to_length is False


def test_literary_rejected_when_tight_slot():
    from engines.naturalizer_v2.catp import estimate_duration_ms, polish_with_budget

    safe = "він боявся"
    lit = (
        "йому зовсім не хотілося повертатися додому і ще довгий літературний хвіст "
        "про важкі думки дорогою"
    )
    # Slot barely fits the short form — literary must lose
    short_ms = estimate_duration_ms(safe, "uk")
    res = polish_with_budget(
        baseline=safe,
        safe=safe,
        literary=lit,
        slot_ms=max(250, short_ms + 20),
        reserve_ms=10,
        lang="uk",
    )
    assert res.mode == "safe"
    assert "літературний хвіст" not in res.text
    assert res.selected_variant in ("A", "B")
    assert res.rollback_due_to_length or res.selected_variant != "C"
    lit_ms = estimate_duration_ms(lit, "uk")
    assert estimate_duration_ms(res.text, "uk") <= lit_ms


def test_short_synonym_helps_timing():
    from engines.naturalizer_v2.catp import apply_short_synonyms, estimate_duration_ms

    long = "йому зовсім не хотілося повертатися додому"
    short, codes = apply_short_synonyms(long)
    assert codes
    assert estimate_duration_ms(short, "uk") <= estimate_duration_ms(long, "uk")


def test_all_long_handoff_flag():
    from engines.naturalizer_v2.catp import polish_with_budget

    long = (
        "Сьогодні Джорджа-молодшого весь світ знає як Джорджа Лукаса, "
        "і йому зовсім не хотілося повертатися додому з довгим хвостом"
    )
    res = polish_with_budget(
        baseline=long,
        safe=long,
        literary=long + " ще трохи довшого літературного хвоста",
        slot_ms=400,
        reserve_ms=0,
        lang="uk",
    )
    assert res.handoff_to_dsal or res.selected_variant in ("A", "B")
    assert res.duration_after <= res.duration_before + 50 or res.handoff_to_dsal


def test_safe_mode_small_reserve():
    from engines.naturalizer_v2.catp import compute_budget

    b = compute_budget(
        slot_ms=2000,
        reserve_ms=50,
        baseline_text="короткий текст",
        lang="uk",
    )
    assert b.mode == "safe"


def test_extended_mode_large_reserve():
    from engines.naturalizer_v2.catp import compute_budget

    b = compute_budget(
        slot_ms=10000,
        reserve_ms=800,
        baseline_text="короткий текст",
        lang="uk",
    )
    assert b.mode == "extended"


def test_orchestrator_does_not_grow_overflow_on_tight_slot():
    from engines.naturalizer_v2.catp import estimate_duration_ms
    from engines.naturalizer_v2.orchestrator import polish_segment_v2

    raw = (
        "Джорджа-молодшого не полишало відчуття, що він дуже боявся туди дістатися."
    )
    before = estimate_duration_ms(raw, "uk")
    out = polish_segment_v2(
        raw,
        original="George couldn’t help but feel like he was dreading getting home.",
        tgt_lang="uk",
        use_llm=False,
        slot_ms=max(300, before + 30),
        reserve_ms=20,
    )
    after = estimate_duration_ms(out["text"], "uk")
    # Must not explode past allowed budget
    assert after <= before + 120
    catp = out.get("catp") or {}
    if catp.get("rollback_due_to_length"):
        assert len(out["text"]) <= len(raw) + 40


def test_rasm_attributes_text_overflow():
    from engines.rasm.metrics import compute_segment_metrics

    seg = {
        "segment_id": "s1",
        "start_ms": 0,
        "end_ms": 800,
        "fitted_ms": 1400,
        "place_delay_ms": 0,
        "approved_text": (
            "Сьогодні Джорджа-молодшого весь світ знає як Джорджа Лукаса "
            "і дорогою додому його не полишало важке передчуття ще раз"
        ),
        "tgt_lang": "uk",
    }
    m = compute_segment_metrics(seg, index=0)
    assert m.overflow_ms > 0 or m.duration_overflow_ms > 0
    assert m.overflow_cause == "text"
    assert "text_overflow" in m.flags


def test_rasm_attributes_scheduler_overflow():
    from engines.rasm.metrics import compute_segment_metrics

    seg = {
        "segment_id": "s2",
        "start_ms": 0,
        "end_ms": 2000,
        "fitted_ms": 500,
        "place_delay_ms": 1800,
        "approved_text": "коротко",
        "tgt_lang": "uk",
    }
    m = compute_segment_metrics(seg, index=0)
    assert m.overflow_ms > 0
    assert m.overflow_cause == "scheduler"


def test_variant_selection_prefers_fitting_literary():
    from engines.naturalizer_v2.catp import build_variants, compute_budget, select_best_variant

    safe = "він боявся"
    lit = "він трохи хвилювався"
    budget = compute_budget(slot_ms=5000, reserve_ms=1000, baseline_text=safe, lang="uk")
    variants = build_variants(baseline=safe, safe=safe, literary=lit, lang="uk", budget=budget)
    best = select_best_variant(variants)
    assert best is not None
    assert best.fits
