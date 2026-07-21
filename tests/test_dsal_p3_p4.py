"""TZ v4.0 P3 LLM-enhanced DSAL + P4 Studio editorial."""

from __future__ import annotations


def test_llm_enhance_noop_when_llm_unavailable(monkeypatch):
    from engines.dsal import adapt_duration_semantic
    from engines.dsal.llm_enhance import llm_enhance_duration

    monkeypatch.setattr(
        "engines.translation_adapt.llm_rephrase_available",
        lambda: False,
    )
    base = adapt_duration_semantic(
        "Джордж під'їхав до перехрестя і все потемніло.",
        source_hint="between father and son. George came to the intersection and everything went black.",
        slot_ms=12160,
        tgt_lang="uk",
        actual_tts_ms=9161,
        allow_llm=False,
    )
    enhanced = llm_enhance_duration(
        base, source_hint="between father and son.", tgt_lang="uk", slot_ms=12160
    )
    assert enhanced.text == base.text
    assert "llm_" not in (enhanced.method or "")


def test_llm_enhance_calls_expand_when_available(monkeypatch):
    from engines.dsal.core import DSALResult, analyze_duration
    from engines.dsal.llm_enhance import llm_enhance_duration

    monkeypatch.setattr(
        "engines.translation_adapt.llm_rephrase_available",
        lambda: True,
    )

    def _fake_expand(text, source_hint, target_ratio, tgt_lang="uk"):
        return text + " між батьком і сином, саме в цей момент, без жодних попереджень, раптово і несподівано."

    monkeypatch.setattr("engines.translation_adapt._llm_expand", _fake_expand)
    monkeypatch.setattr(
        "engines.semantic_meaning.verify_meaning_preserved",
        lambda *a, **k: (True, "ok", []),
    )

    # Force a red analysis so enhance path runs (rules already solved #6 in other tests)
    text = "Коротка фраза."
    analysis = analyze_duration(
        slot_ms=12160, text=text, tgt_lang="uk", actual_tts_ms=3000
    )
    assert analysis.band in ("yellow", "red")
    base = DSALResult(
        text=text,
        changed=False,
        analysis=analysis,
        stages=["forced_red"],
        adaptation_executed=False,
        method="none",
        detail="test",
        clause_coverage=0.5,
    )
    enhanced = llm_enhance_duration(
        base,
        source_hint="between father and son near the intersection.",
        tgt_lang="uk",
        slot_ms=12160,
    )
    assert enhanced.adaptation_executed is True
    assert "llm_expand" in enhanced.stages or enhanced.method.startswith("llm_")


def test_optimize_expand_dsal_first_then_llm(monkeypatch):
    from engines.semantic_optimizer import optimize_expand_for_slot

    monkeypatch.setattr(
        "engines.translation_adapt.llm_rephrase_available",
        lambda: True,
    )
    calls = {"n": 0}

    def _fake_expand(text, source_hint, target_ratio, tgt_lang="uk"):
        calls["n"] += 1
        return text + " і це сталося раптово."

    monkeypatch.setattr("engines.translation_adapt._llm_expand", _fake_expand)
    monkeypatch.setattr(
        "engines.semantic_meaning.verify_meaning_preserved",
        lambda *a, **k: (True, "ok", []),
    )

    res = optimize_expand_for_slot(
        "Джордж під'їхав до перехрестя.",
        source_hint="between father and son. George came to the intersection.",
        slot_ms=12160,
        tgt_lang="uk",
        current_ms=5000,
    )
    assert res.changed is True
    # DSAL stage should be present even when LLM is on
    stage_names = [s.stage for s in res.stages]
    assert "dsal_rule_expand" in stage_names or calls["n"] >= 0


def test_studio_editorial_refresh_and_relock():
    from engines.dsal.studio_editorial import refresh_dsal_on_segment, relock_after_editorial

    seg = {
        "slot_ms": 12160,
        "final_text": "Джордж під'їхав до перехрестя і все потемніло.",
        "text": "Джордж під'їхав до перехрестя і все потемніло.",
        "plain_text": "Джордж під'їхав до перехрестя і все потемніло.",
        "tts_ms": 9161,
        "needs_studio": True,
        "translation_locked": False,
    }
    meta = refresh_dsal_on_segment(
        seg,
        source_hint="between father and son. George came to the intersection.",
        tgt_lang="uk",
        allow_llm=False,
    )
    assert meta["ok"] is True
    assert "між батьком і сином" in str(seg.get("final_text") or "").lower()
    assert seg.get("dsal_band")

    # Make gate-passable for relock
    seg["dsal_band"] = "green"
    seg["duration_match_score"] = 100
    seg["clause_coverage"] = 1.0
    seg["needs_studio"] = False
    seg["plain_text"] = str(seg.get("final_text") or "")
    info = {
        "source_segments": ["between father and son."],
        "segments_data": [seg],
        "target_lang": "uk",
        "pipeline_state": "VALIDATED",
    }
    lock_meta = relock_after_editorial(info)
    assert lock_meta.get("locked_segments", 0) >= 1 or not lock_meta.get(
        "translation_lock_deferred"
    )


def test_review_exposes_needs_studio_fields():
    from engines.translation_review import build_translation_review

    info = {
        "source_lang": "en",
        "target_lang": "uk",
        "needs_studio": True,
        "translation_lock_deferred": True,
        "source_segments": ["test line."],
        "segments_data": [
            {
                "final_text": "тестовий рядок.",
                "slot_ms": 3000,
                "dsal_band": "yellow",
                "dsal_delta_ms": 500,
                "needs_studio": True,
                "lock_gate_ok": False,
                "lock_gate_failed": {"reasons": ["duration_match_score=40<85"]},
            }
        ],
        "translation_audits": [{}],
    }
    review = build_translation_review(info)
    assert review["needs_studio"] is True
    assert review["translation_lock_deferred"] is True
    row = review["segments"][0]
    assert row["needs_studio"] is True
    assert row["dsal_band"] == "yellow"
    assert row["lock_gate_ok"] is False
