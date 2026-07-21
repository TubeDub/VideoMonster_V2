"""TZ v4.0 DSAL — Duration-Semantic Adaptation Layer (P0)."""

from __future__ import annotations


def test_analyze_duration_expand_required_on_underflow():
    from engines.dsal import analyze_duration

    # George Lucas #6: slot 12160, TTS ~9161 → ~2999 ms empty
    a = analyze_duration(slot_ms=12160, text="x", tgt_lang="uk", actual_tts_ms=9161)
    assert a.expand_required is True
    assert a.compress_required is False
    assert a.delta_ms == 2999
    assert a.band in ("yellow", "red")
    assert a.delta_pct > 10.0


def test_analyze_duration_compress_required_on_overflow():
    from engines.dsal import analyze_duration

    # George Lucas #7-ish: slot 5600, TTS 6932
    a = analyze_duration(slot_ms=5600, text="x", tgt_lang="uk", actual_tts_ms=6932)
    assert a.compress_required is True
    assert a.expand_required is False
    assert a.band in ("yellow", "red")


def test_clause_restore_between_father_and_son():
    from engines.dsal import adapt_duration_semantic

    uk = (
        "Джордж під'їхав до перехрестя, де він був біля його дому, "
        "і він почав повертати, коли він почув цей дуже гучний звук і потім все потемніло."
    )
    en = (
        "between father and son. And so George, he came to this intersection "
        "where it was right near his home, and he begins making the turn when he "
        "hears this really loud screeching sound and then everything went black."
    )
    res = adapt_duration_semantic(
        uk,
        source_hint=en,
        slot_ms=12160,
        tgt_lang="uk",
        actual_tts_ms=9161,
    )
    assert res.adaptation_executed is True
    assert "між батьком і сином" in res.text.lower()
    assert res.changed is True
    # Acceptance P0: residual underflow under 1500 ms (predicted)
    assert abs(res.analysis.delta_ms) < 1500 or res.analysis.band in ("green", "yellow")


def test_optimize_expand_falls_back_without_llm(monkeypatch):
    from engines.semantic_optimizer import optimize_expand_for_slot

    monkeypatch.setattr(
        "engines.translation_adapt.llm_rephrase_available",
        lambda: False,
    )
    uk = (
        "Джордж під'їхав до перехрестя, де він був біля його дому, "
        "і він почав повертати, коли він почув цей дуже гучний звук і потім все потемніло."
    )
    en = "between father and son. And so George came to this intersection near his home."
    res = optimize_expand_for_slot(
        uk,
        source_hint=en,
        slot_ms=12160,
        tgt_lang="uk",
        current_ms=9161,
    )
    assert res.changed is True
    assert res.stopped_reason == "dsal_rule_expand"
    assert "між батьком і сином" in res.text.lower()


def test_dsal_pre_lock_stamps_flags():
    from engines.translation_validation import apply_dsal_before_lock

    info = {
        "target_lang": "uk",
        "source_segments": [
            "between father and son. And so George came to the intersection near home."
        ],
        "segments_data": [
            {
                "slot_ms": 12160,
                "final_text": (
                    "Джордж під'їхав до перехрестя біля дому і почав повертати, "
                    "коли почув гучний звук і все потемніло."
                ),
                "tts_ms": 9161,
            }
        ],
    }
    summary = apply_dsal_before_lock(info)
    seg = info["segments_data"][0]
    assert summary["adapted"] >= 1
    assert seg.get("expand_required") is True or seg.get("dsal_applied") is True
    assert seg.get("adaptation_executed") is True
    assert "між батьком і сином" in str(seg.get("final_text") or "").lower()


def test_hard_gate_allows_dsal_when_provider_fatal():
    from engines.ai_adaptation_engine import enforce_adaptation_gate

    segments = ["adapted text with clause"]
    segments_data = [
        {
            "requires_llm_adaptation": True,
            "provider_fatal": True,
            "adaptation_executed": True,
            "dsal_applied": True,
            "rule_fallback_applied": True,
        }
    ]
    result = enforce_adaptation_gate(
        segments,
        segments_data=segments_data,
        timing_records=[{"index": 0, "requires_llm_adaptation": True, "provider_fatal": True}],
        llm_status=[{"segment": 0, "needed": True, "called": False, "skip_reason": "provider_fatal"}],
        llm_calls=[],
    )
    assert result.passed is True
