"""LLM call capture + effectiveness report (AutoDub audit TЗ §1/§2/§9/§10)."""

from __future__ import annotations

import engines.translation_adapt as ta
from engines.segment_timing_qa import build_llm_effectiveness_report


def test_llm_call_capture_records_segment_and_texts():
    ta.begin_llm_capture()
    ta.set_llm_context(segment=3, stage="timing_aware")
    ta._record_llm_call(
        "shorten this line", "коротший рядок", finish_reason="stop", ms=120.0, ok=True
    )
    calls = ta.get_llm_calls()
    assert len(calls) == 1
    assert calls[0]["segment"] == 3
    assert calls[0]["stage"] == "timing_aware"
    assert calls[0]["sent"] == "shorten this line"
    assert calls[0]["received"] == "коротший рядок"
    assert calls[0]["usable"] is True


def test_drain_clears_calls():
    ta.begin_llm_capture()
    ta.set_llm_context(segment=0)
    ta._record_llm_call("a", "b", finish_reason="stop", ms=1.0, ok=True)
    drained = ta.drain_llm_calls()
    assert len(drained) == 1
    assert ta.get_llm_calls() == []


def test_effectiveness_report_counts():
    task_info = {
        "llm_calls": [
            {"segment": 0, "usable": True, "finish_reason": "stop"},
            {"segment": 1, "usable": False, "finish_reason": "length"},
        ],
        "post_tts_qa": {"retries": 2},
    }
    segments = [
        {
            "index": 0,
            "rule_rewrite_used": False,
            "llm_rewrite_used": True,
            "adaptation_executed": True,
            "translated_text": "довгий оригінальний рядок тут",
            "text_after_adaptation": "короткий рядок",
            "adaptation_iterations": 2,
        },
        {
            "index": 1,
            "rule_rewrite_used": True,
            "llm_rewrite_used": False,
            "adaptation_executed": True,
            "translated_text": "інший рядок",
            "text_after_adaptation": "інший коротший",
            "optimization_retries": 1,
        },
        {
            "index": 2,
            "rule_rewrite_used": False,
            "llm_rewrite_used": False,
            "adaptation_executed": False,
        },
    ]
    rep = build_llm_effectiveness_report(task_info, segments)
    assert rep["segment_count"] == 3
    assert rep["llm_rewrite_used"] == 1
    assert rep["rule_rewrite_only"] == 1
    assert rep["llm_improved_segments"] == 1
    assert rep["regenerations"] == 2
    assert rep["llm_calls_total"] == 2
    assert rep["llm_calls_usable"] == 1
    assert rep["llm_calls_truncated"] == 1
    assert rep["avg_attempts"] == 1.5  # (2 + 1) / 2


def test_effectiveness_report_no_llm():
    task_info = {"llm_calls": [], "post_tts_qa": {}}
    segments = [{"index": 0, "rule_rewrite_used": True, "adaptation_executed": True}]
    rep = build_llm_effectiveness_report(task_info, segments)
    assert rep["llm_available"] is False
    assert rep["rule_rewrite_only"] == 1
    assert rep["llm_rewrite_used"] == 0


# ---- Architecture P0: no silent LLM skip ----------------------------------

def test_status_records_skip_reason():
    ta.begin_llm_capture()
    ta.set_llm_context(segment=5, stage="timing_aware")
    ta.record_llm_skip("no_endpoint")
    status = {s["segment"]: s for s in ta.get_llm_status()}
    assert status[5]["needed"] is True
    assert status[5]["called"] is False
    assert status[5]["skip_reason"] == "no_endpoint"


def test_status_records_no_rewrite():
    ta.begin_llm_capture()
    ta.set_llm_context(segment=2)
    ta.record_llm_no_rewrite("identical_output")
    status = {s["segment"]: s for s in ta.get_llm_status()}
    assert status[2]["no_rewrite"] is True
    assert status[2]["no_rewrite_reason"] == "identical_output"


def test_call_clears_skip_and_marks_called():
    ta.begin_llm_capture()
    ta.set_llm_context(segment=7)
    ta.record_llm_skip("breaker_open")
    ta._record_llm_call("p", "переписаний рядок", finish_reason="stop", ms=50.0, ok=True)
    status = {s["segment"]: s for s in ta.get_llm_status()}
    assert status[7]["called"] is True
    assert status[7]["skip_reason"] is None
    assert status[7]["attempts"] == 1


def test_openddf_surfaces_llm_not_called_error():
    from engines.segment_timing_qa import build_openddf_full_report

    task_info = {
        "task_id": "t1",
        "target_lang": "uk",
        "segments_data": [
            {"text": "фінальний текст тут повністю.", "tts_text": "фінальний текст тут повністю.",
             "requires_llm_adaptation": True, "file": "seg0.wav"}
        ],
        "translation_audits": [
            {
                "index": 0,
                "raw_translation": "raw",
                "final_text": "фінальний текст тут повністю.",
                "tts_text": "фінальний текст тут повністю.",
                "requires_llm_adaptation": True,
            }
        ],
        "llm_calls": [],
        "llm_status": [
            {"segment": 0, "needed": True, "called": False, "skip_reason": "no_endpoint",
             "attempts": 0, "no_rewrite": False},
        ],
        "post_tts_qa": {},
    }
    report = build_openddf_full_report(task_info)
    seg0 = report["segments"][0]
    codes = [e["code"] for e in (seg0.get("errors") or [])]
    # No endpoint configured → surfaced as the precise LLM_UNAVAILABLE (a
    # needed-but-not-called segment is never a silent skip).
    assert "LLM_UNAVAILABLE" in codes or "LLM_NOT_CALLED" in codes
    diag = report["llm_diagnostics"]
    assert diag["skip_reasons"].get("no_endpoint") == 1
    assert report["llm_effectiveness"]["llm_not_called_segments"] == 1


def test_openddf_no_false_error_when_llm_actually_called():
    """Regression: when the post-TTS LLM journal is present for a segment, it must
    show llm_called=True and NOT emit a false LLM_NOT_CALLED/LLM_ADAPTATION_FAILED
    error — even if the segment still carries requires_llm_adaptation (the LLM ran
    but kept the original as best). Root cause was a shallow-copy dropping the
    journal so every adapted segment looked un-called."""
    from engines.segment_timing_qa import build_openddf_full_report

    task_info = {
        "task_id": "t2",
        "target_lang": "uk",
        "segments_data": [
            {
                "text": "Джордж молодший поїхав додому на вечерю.",
                "tts_text": "Джордж молодший поїхав додому на вечерю.",
                "requires_llm_adaptation": True,
                "file": "seg0.wav",
            }
        ],
        "translation_audits": [
            {"index": 0, "final_text": "Джордж молодший поїхав додому на вечерю.",
             "tts_text": "Джордж молодший поїхав додому на вечерю."}
        ],
        "llm_calls": [
            {"segment": 0, "stage": "post_tts_retry_1", "provider": "ollama",
             "model": "llama3.1:8b", "sent": "...", "received": "...",
             "finish_reason": "stop", "ms": 1200.0, "usable": True},
        ],
        "llm_status": [
            {"segment": 0, "needed": True, "called": True, "attempts": 1,
             "skip_reason": None, "no_rewrite": False},
        ],
        "post_tts_qa": {},
    }
    report = build_openddf_full_report(task_info)
    seg0 = report["segments"][0]
    assert seg0["llm_called"] is True
    assert seg0["llm_attempts"] == 1
    codes = [e["code"] for e in (seg0.get("errors") or [])]
    assert "LLM_NOT_CALLED" not in codes
    assert "LLM_ADAPTATION_FAILED" not in codes
    assert "LLM_UNAVAILABLE" not in codes
