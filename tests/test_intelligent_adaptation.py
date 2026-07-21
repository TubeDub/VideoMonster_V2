"""Intelligent duration adaptation (TZ: no mechanical truncation, bidirectional).

Covers:
  - hard anti-truncation guarantee (never emit clipped/unfinished sentences)
  - bidirectional expansion never pads with fillers when no LLM is available
  - LLM availability detection (cloud key OR self-hosted base URL)
  - OpenDDF capabilities surface the reasons quality may be limited
"""

from __future__ import annotations

import pytest


def test_llm_rephrase_available_detects_selfhosted(monkeypatch):
    from engines import translation_adapt

    for var in ("OPENAI_API_KEY", "VM_LLM_API_KEY", "VM_OPENAI_API_KEY", "VM_LLM_BASE_URL", "OPENAI_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    # Deterministic regardless of a local LLM running on the dev box.
    monkeypatch.setenv("VM_LLM_AUTODISCOVER", "0")
    translation_adapt._ENDPOINT_CACHE.clear()
    assert translation_adapt.llm_rephrase_available() is False

    # Self-hosted OpenAI-compatible endpoint needs no API key.
    monkeypatch.setenv("VM_LLM_BASE_URL", "http://localhost:11434/v1")
    translation_adapt._ENDPOINT_CACHE.clear()
    monkeypatch.setattr(
        translation_adapt,
        "_resolve_endpoint",
        lambda: {
            "available": True,
            "base_url": "http://localhost:11434/v1",
            "api_key": None,
            "models": [],
        },
    )
    assert translation_adapt.llm_rephrase_available() is True


def test_expand_without_llm_never_pads(monkeypatch):
    """TZ v4.0: short line + no LLM → DSAL rule expand (not garbage filler)."""
    from engines.semantic_optimizer import optimize_expand_for_slot

    for var in ("OPENAI_API_KEY", "VM_LLM_API_KEY", "VM_OPENAI_API_KEY", "VM_LLM_BASE_URL", "OPENAI_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    # Deterministic regardless of a local LLM running on the dev box.
    monkeypatch.setenv("VM_LLM_AUTODISCOVER", "0")

    short = "Джордж їхав додому."
    res = optimize_expand_for_slot(
        short,
        source_hint="George drove home for a very long family dinner tonight downtown.",
        slot_ms=9000,
        tgt_lang="uk",
    )
    # Rule-based DSAL may expand; LLM pad / random filler must not appear.
    assert "..." not in res.text
    assert "ааа" not in res.text.lower()
    if res.changed:
        assert res.stopped_reason == "dsal_rule_expand"
        assert len(res.text) > len(short)
        assert "Джордж" in res.text
    else:
        assert res.text == short
        assert res.stopped_reason in (
            "requires_llm_expansion",
            "dsal_no_change",
            "no_expand_needed",
        )


def test_no_expand_when_already_full(monkeypatch):
    from engines.semantic_optimizer import optimize_expand_for_slot

    monkeypatch.setenv("VM_LLM_BASE_URL", "http://localhost:11434/v1")
    text = "Це доволі довге речення, яке вже добре заповнює відведений часовий слот повністю."
    res = optimize_expand_for_slot(text, source_hint="", slot_ms=3000, tgt_lang="uk")
    # Estimated speech already >= trigger ratio of a short slot → no expansion.
    assert res.stopped_reason == "no_expand_needed"
    assert res.text == text


def test_adapt_segment_reverts_truncated_adaptation(monkeypatch):
    """TZ §1: if optimizer ever returns a truncated line, keep the full sentence."""
    import engines.timing_aware_translation as tat

    original = "Вісімнадцятирічний хлопець на ім’я Джордж їхав через своє рідне місто додому."

    truncated = "Вісімнадцятирічний хлопець на ім’я Джордж їхав через своє рідне"

    from engines.ai_adaptation_engine import AdaptationResult, SegmentAdaptationTrace

    def _fake_ai(*_a, **_k):
        trace = SegmentAdaptationTrace(index=0, chosen_text=truncated)
        return AdaptationResult(
            text=truncated,
            changed=True,
            trace=trace,
            stopped_reason="llm_shorten",
        )

    def _fake_budget(text, slot_ms, *, tgt_lang):
        class B:
            delta_ms = 1000
            tts_estimated_ms = 6000
            target_ms = 4000
            fits = False
        return B()

    import engines.ai_adaptation_engine as aae
    import engines.semantic_optimizer as so
    monkeypatch.setattr(aae, "adapt_segment_ai", _fake_ai)
    monkeypatch.setattr(so, "compute_time_budget", _fake_budget)

    out, rec = tat.adapt_segment_to_slot(
        original,
        source_text="An eighteen year old boy named George drove home through his hometown.",
        slot_ms=4000,
        src_lang="en",
        tgt_lang="uk",
        index=0,
    )
    assert out == original, "truncated adaptation must be rejected"
    assert rec.reason == "rejected_truncation_kept_full"


def test_requires_llm_gate_surfaces_pending(monkeypatch):
    """TZ §3: a requires_llm_adaptation segment must be surfaced with a reason."""
    from engines import segment_timing_qa as stq

    for var in ("OPENAI_API_KEY", "VM_LLM_API_KEY", "VM_OPENAI_API_KEY", "VM_LLM_BASE_URL", "OPENAI_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    # Deterministic regardless of a local LLM running on the dev box.
    monkeypatch.setenv("VM_LLM_AUTODISCOVER", "0")

    # No overflow issues → loop is a no-op, but the gate still inspects flags.
    monkeypatch.setattr(stq, "detect_post_tts_deviations", lambda *a, **k: [])

    seg = {
        "index": 0,
        "segment_id": "s0",
        "file": "g0000.mp3",
        "text": "Довгий текст, який не вдалося адаптувати без LLM.",
        "requires_llm_adaptation": True,
    }
    stats = stq.post_tts_validate_and_retry(
        [seg],
        [{"start": 0, "end": 3000}],
        source_segments=["A long source line that needs shortening."],
        voice="uk-UA-OstapNeural",
        target_lang="uk",
        src_lang="en",
        task_id="t1",
    )
    gate = stats.get("requires_llm_adaptation")
    assert gate is not None
    assert gate["count"] == 1
    assert gate["llm_available"] is False
    assert "AI-модуль" in gate["reason"]


def test_openddf_capabilities_reports_limitations(monkeypatch):
    from engines.segment_timing_qa import _build_openddf_adaptation_capabilities

    for var in ("OPENAI_API_KEY", "VM_LLM_API_KEY", "VM_OPENAI_API_KEY", "VM_LLM_BASE_URL", "OPENAI_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    # Deterministic regardless of a local LLM running on the dev box.
    monkeypatch.setenv("VM_LLM_AUTODISCOVER", "0")
    cap = _build_openddf_adaptation_capabilities({"source_separation": {"success": False}})
    assert cap["llm_rephrase_available"] is False
    assert cap["music_preserved"] is False
    assert any("AI-модуль" in n for n in cap["notes"])


def test_strict_llm_gate_toggle(monkeypatch):
    """TZ §3 strict gate: OFF by default, ON via VM_STRICT_LLM_ADAPTATION."""
    from api.auto_dub_api import _strict_llm_adaptation_enabled

    monkeypatch.delenv("VM_STRICT_LLM_ADAPTATION", raising=False)
    # Default OFF (feature flag not registered → False).
    assert _strict_llm_adaptation_enabled() is False

    for truthy in ("1", "true", "YES", "on"):
        monkeypatch.setenv("VM_STRICT_LLM_ADAPTATION", truthy)
        assert _strict_llm_adaptation_enabled() is True

    for falsy in ("0", "false", "no", "off"):
        monkeypatch.setenv("VM_STRICT_LLM_ADAPTATION", falsy)
        assert _strict_llm_adaptation_enabled() is False


def test_speech_duration_match_helpers():
    from engines.segment_timing_qa import (
        DURATION_MATCH_GOAL_MS,
        SPEECH_UNDERFLOW_EXPAND_MS,
        segment_duration_matches_goal,
        speech_duration_match_score,
        speech_ends_early,
    )

    assert segment_duration_matches_goal(5000, 5200, goal_ms=DURATION_MATCH_GOAL_MS)
    assert not segment_duration_matches_goal(5000, 6000, goal_ms=DURATION_MATCH_GOAL_MS)
    assert speech_duration_match_score(5000, 5000) == 100
    assert speech_ends_early(4000, 5000, min_gap_ms=SPEECH_UNDERFLOW_EXPAND_MS)
    assert not speech_ends_early(4600, 5000, min_gap_ms=SPEECH_UNDERFLOW_EXPAND_MS)


def test_detect_post_tts_underflow_when_speech_ends_early():
    from engines.segment_timing_qa import detect_post_tts_deviations

    seg = {
        "file": "seg0.mp3",
        "playback_duration": 3500,
        "tts_ms": 3500,
    }
    timing_map = [{"start": 0, "end": 5000}]
    issues = detect_post_tts_deviations(seg, 0, timing_map)
    underflow = [i for i in issues if i["code"] == "duration_underflow"]
    assert underflow
    assert underflow[0]["speech_difference_ms"] == 1500


def test_post_tts_expansion_loop_runs_on_underflow(monkeypatch):
    from engines.segment_timing_qa import post_tts_validate_and_retry

    segments = [
        {
            "text": "Не знаю.",
            "plain_text": "Не знаю.",
            "file": "seg0.mp3",
            "playback_duration": 1200,
            "tts_ms": 1200,
        }
    ]
    timing_map = [{"start": 0, "end": 3000}]
    calls = {"n": 0}

    from types import SimpleNamespace

    monkeypatch.setattr(
        "engines.semantic_optimizer.optimize_llm_rephrase_for_slot",
        lambda *a, **k: SimpleNamespace(text=a[0], changed=False),
    )
    monkeypatch.setattr(
        "engines.semantic_optimizer.optimize_expand_for_slot",
        lambda *a, **k: SimpleNamespace(
            text="Чесно кажучи, я не знаю.",
            changed=True,
            stopped_reason="expanded_to_fit",
        ),
    )

    def regen_fn(text, **kwargs):
        calls["n"] += 1
        segments[0]["playback_duration"] = 2700
        segments[0]["tts_ms"] = 2700
        return f"retry_{calls['n']}.mp3", 2700

    stats = post_tts_validate_and_retry(
        segments,
        timing_map,
        source_segments=["I honestly don't know."],
        voice="uk-UA-OstapNeural",
        target_lang="uk",
        src_lang="en",
        regen_fn=regen_fn,
        max_retries=3,
    )
    assert stats["adaptation_executed"] is True
    assert stats["retries"] >= 1
    trace = segments[0].get("text_adaptation_trace") or {}
    assert trace.get("expand_required") is True
    assert trace.get("expand_executed") is True
    assert trace.get("expansion_iterations") >= 1
    assert "llm_expand" in (trace.get("stages") or []) or "dsal_rule_expand" in (
        trace.get("stages") or []
    )
    assert trace.get("duration_match_score", 0) >= 90


def test_expand_with_measured_ms_skips_small_gap(monkeypatch):
    from engines.semantic_optimizer import optimize_expand_for_slot

    monkeypatch.setenv("VM_LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setattr(
        "engines.translation_adapt.llm_rephrase_available",
        lambda: True,
    )
    res = optimize_expand_for_slot(
        "Коротка фраза.",
        source_hint="Short phrase.",
        slot_ms=3000,
        tgt_lang="uk",
        current_ms=2700,
    )
    assert res.changed is False
    assert res.stopped_reason == "no_expand_needed"


def test_rephrase_calls_llm_when_measured_overflows_but_estimate_fits(monkeypatch):
    """CRITICAL BUG regression: measured TTS overflows the slot, but the length
    estimate says it 'fits'. The rephrase MUST still call the LLM instead of
    returning fits_no_change (which silently skipped the LLM for every overflow
    segment in production logs)."""
    import engines.semantic_optimizer as so
    from engines.semantic_optimizer import TimeBudget, optimize_llm_rephrase_for_slot

    # Estimate says the line fits the slot (this is the wrong signal in prod).
    monkeypatch.setattr(
        so,
        "compute_time_budget",
        lambda text, slot_ms, *, tgt_lang: TimeBudget(
            segment_duration_ms=slot_ms,
            tts_estimated_ms=5037,
            delta_ms=0,
            target_ms=slot_ms - 40,
            fits=True,
        ),
    )
    calls = {"n": 0}

    def _fake_rephrase(text, target_ratio, *, source_hint, tgt_lang):
        calls["n"] += 1
        return "Скорочений рядок, що зберігає весь зміст оригіналу повністю."

    monkeypatch.setattr(
        "engines.translation_adapt._stage_semantic_rephrase", _fake_rephrase
    )
    monkeypatch.setattr(
        "engines.semantic_meaning.verify_meaning_preserved",
        lambda *a, **k: (True, "ok", {}),
    )

    res = optimize_llm_rephrase_for_slot(
        "Дуже довгий оригінальний рядок, який насправді не вміщається у слот.",
        source_hint="A very long original line that does not fit the slot.",
        slot_ms=4960,
        tgt_lang="uk",
        max_rounds=1,
        current_ms=6264,  # measured TTS clearly overflows 4960ms slot
    )
    assert calls["n"] >= 1, "LLM rephrase must be attempted on measured overflow"
    assert res.stopped_reason != "fits_no_change"


def test_post_tts_no_oscillation_after_shrink(monkeypatch):
    """After shrinking an overflow, a resulting underflow must NOT trigger an
    expand (that ping-pong is the 'hang on TTS' on slow local models)."""
    from engines import segment_timing_qa as stq

    segments = [
        {
            "text": "Дуже довгий рядок, який переповнює часовий слот повністю.",
            "plain_text": "Дуже довгий рядок, який переповнює часовий слот повністю.",
            "file": "seg0.mp3",
            "playback_duration": 9216,
            "tts_ms": 9216,
        }
    ]
    timing_map = [{"start": 0, "end": 7840}]

    from types import SimpleNamespace

    # Overflow shrink returns a shorter line.
    monkeypatch.setattr(
        "engines.semantic_optimizer.optimize_llm_rephrase_for_slot",
        lambda *a, **k: SimpleNamespace(text="Короткий рядок.", changed=True, stopped_reason="fits_after_llm"),
    )
    expand_calls = {"n": 0}

    def _expand(*a, **k):
        expand_calls["n"] += 1
        return SimpleNamespace(text="Розширений рядок довший.", changed=True, stopped_reason="expanded")

    monkeypatch.setattr("engines.semantic_optimizer.optimize_expand_for_slot", _expand)

    def regen_fn(text, **kwargs):
        # After the shrink the audio lands well UNDER the slot (would tempt an expand).
        segments[0]["playback_duration"] = 7104
        segments[0]["tts_ms"] = 7104
        return "retry.mp3", 7104

    stats = stq.post_tts_validate_and_retry(
        segments,
        timing_map,
        source_segments=["A very long line that overflows the slot."],
        voice="uk-UA-OstapNeural",
        target_lang="uk",
        src_lang="en",
        regen_fn=regen_fn,
        max_retries=5,
    )
    # The post-shrink underflow (7840-7104=736ms) must NOT trigger expansion.
    assert expand_calls["n"] == 0, "must not expand after shrinking (anti-oscillation)"


def test_foreign_script_guard_rejects_cjk_in_ukrainian():
    """Weak local models (qwen2.5:3b) can inject CJK (良心) into a Ukrainian dub.
    The integrity guard must reject such output; a CJK target must be allowed."""
    from engines.sentence_integrity import contains_foreign_script, validate_tts_text

    corrupted = "18 років Джордж молодший пройшов через світ良心 через гурт."
    assert contains_foreign_script(corrupted, "uk") is True
    ok, issues = validate_tts_text(corrupted, tgt_lang="uk")
    assert ok is False
    assert "foreign_script" in issues

    clean = "18-річний Джордж молодший поїхав додому на вечерю через рідне місто."
    assert contains_foreign_script(clean, "uk") is False
    ok2, _ = validate_tts_text(clean, tgt_lang="uk")
    assert ok2 is True

    # A genuinely Chinese target must NOT be rejected for containing CJK.
    assert contains_foreign_script("良心的选择", "zh") is False
    assert contains_foreign_script("良心的选择", "zh-CN") is False


def test_rephrase_rejects_cjk_candidate(monkeypatch):
    """optimize_llm_rephrase_for_slot must not accept a CJK-corrupted rewrite."""
    import engines.semantic_optimizer as so
    from engines.semantic_optimizer import TimeBudget, optimize_llm_rephrase_for_slot

    monkeypatch.setattr(
        so,
        "compute_time_budget",
        lambda text, slot_ms, *, tgt_lang: TimeBudget(
            segment_duration_ms=slot_ms,
            tts_estimated_ms=6000,
            delta_ms=1000,
            target_ms=slot_ms - 40,
            fits=False,
        ),
    )
    monkeypatch.setattr(
        "engines.translation_adapt._stage_semantic_rephrase",
        lambda *a, **k: "Джордж молодший пройшов через світ良心 через гурт.",
    )
    monkeypatch.setattr(
        "engines.semantic_meaning.verify_meaning_preserved",
        lambda *a, **k: (True, "ok", {}),
    )
    res = optimize_llm_rephrase_for_slot(
        "Дуже довгий оригінальний український рядок для сокращення.",
        source_hint="A long original line.",
        slot_ms=4960,
        tgt_lang="uk",
        max_rounds=1,
        current_ms=6264,
    )
    # CJK candidate rejected → text stays original (never corrupted output).
    assert "良心" not in res.text
    assert res.changed is False


def test_rephrase_fits_no_change_when_measured_fits(monkeypatch):
    """Symmetric guard: when the MEASURED duration truly fits, no LLM call."""
    import engines.semantic_optimizer as so
    from engines.semantic_optimizer import TimeBudget, optimize_llm_rephrase_for_slot

    monkeypatch.setattr(
        so,
        "compute_time_budget",
        lambda text, slot_ms, *, tgt_lang: TimeBudget(
            segment_duration_ms=slot_ms,
            tts_estimated_ms=4800,
            delta_ms=0,
            target_ms=slot_ms - 40,
            fits=True,
        ),
    )
    calls = {"n": 0}
    monkeypatch.setattr(
        "engines.translation_adapt._stage_semantic_rephrase",
        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1) or "x",
    )
    res = optimize_llm_rephrase_for_slot(
        "Рядок, що вміщається у слот повністю.",
        source_hint="A line that fits the slot.",
        slot_ms=4960,
        tgt_lang="uk",
        current_ms=4800,  # measured fits within tolerance
    )
    assert res.stopped_reason == "fits_no_change"
    assert calls["n"] == 0
