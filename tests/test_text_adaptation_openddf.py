"""TubeDub 2.0 stage 2 — text adaptation loop + OpenDDF report."""

from __future__ import annotations

UK_PROBLEM_TEXT = (
    "18-річний хлопець на ім'я Джордж-молодший "
    "їхав через своє рідне місто."
)
UK_PROBLEM_SHORT = (
    "18-річний Джордж-молодший їхав через своє рідне місто."
)
UK_PROBLEM_SOURCE = (
    "An 18-year-old boy named George Jr. was driving through his hometown."
)


def test_softeners_universal_preserves_uk_spaces():
    """Regression: empty regex alt must not strip inter-word spaces."""
    from engines.translation_adapt import _SOFTENERS_UNIVERSAL, _stage_moderate

    text = UK_PROBLEM_TEXT
    moderated = _stage_moderate(text)
    assert moderated == text
    assert moderated.count(" ") == text.count(" ")
    assert len(moderated.split()) == len(text.split())
    assert not _SOFTENERS_UNIVERSAL.sub("", text) == text.replace(" ", "")


def test_uk_problem_segment_optimize_no_truncated_tail():
    from engines.semantic_optimizer import optimize_for_time_budget

    result = optimize_for_time_budget(
        UK_PROBLEM_TEXT,
        source_hint=UK_PROBLEM_SOURCE,
        slot_ms=5000,
        tgt_lang="uk",
        allow_llm=False,
    )
    assert "meaning_rejected_truncated_tail" not in result.stopped_reason


def test_uk_problem_segment_adapt_for_duration_strong(monkeypatch):
    from engines.translation_adapt import adapt_for_duration

    monkeypatch.setattr(
        "engines.translation_adapt._llm_shorten",
        lambda *a, **k: UK_PROBLEM_SHORT,
    )
    out = adapt_for_duration(
        UK_PROBLEM_TEXT,
        6360,
        4960,
        UK_PROBLEM_SOURCE,
        stage="strong",
        tgt_lang="uk",
    )
    assert out.strip() != UK_PROBLEM_TEXT.strip()
    assert "Джордж-молодший" in out


def test_uk_problem_post_tts_cycle_executed(monkeypatch):
    from engines.segment_timing_qa import post_tts_validate_and_retry

    segments = [
        {
            "text": UK_PROBLEM_TEXT,
            "plain_text": UK_PROBLEM_TEXT,
            "translation_text": UK_PROBLEM_TEXT,
            "file": "seg0.mp3",
            "playback_duration": 6360,
            "tts_ms": 6360,
        }
    ]
    timing_map = [{"start": 0, "end": 4960}]

    monkeypatch.setattr(
        "engines.translation_adapt._llm_shorten",
        lambda *a, **k: UK_PROBLEM_SHORT,
    )
    monkeypatch.setattr(
        "engines.segment_timing_qa.ADAPTATION_STAGES",
        ("strong", "strong", "strong", "strong", "strong"),
    )

    def regen_fn(new_text, **kwargs):
        new_ms = int(6360 * len(new_text.split()) / len(UK_PROBLEM_TEXT.split()))
        segments[0]["playback_duration"] = new_ms
        segments[0]["tts_ms"] = new_ms
        return "retry.mp3", new_ms

    stats = post_tts_validate_and_retry(
        segments,
        timing_map,
        source_segments=[UK_PROBLEM_SOURCE],
        voice="uk-UA-OstapNeural",
        target_lang="uk",
        src_lang="en",
        regen_fn=regen_fn,
        max_retries=3,
    )
    trace = segments[0]["text_adaptation_trace"]
    assert stats["adaptation_executed"] is True
    assert trace["executed"] is True
    assert trace["iterations"] > 0
    assert trace["text_before"]
    assert trace["text_after"] != trace["text_before"]
    assert trace["final_tts_duration_ms"] < trace["first_tts_duration_ms"]


def test_segment_duration_fits_tolerance():
    from engines.segment_timing_qa import DURATION_TOLERANCE_MS, segment_duration_fits

    assert segment_duration_fits(5000, 5000)
    assert segment_duration_fits(5000 + DURATION_TOLERANCE_MS, 5000)
    assert not segment_duration_fits(5000 + DURATION_TOLERANCE_MS + 1, 5000)


def test_post_tts_adaptation_loop_runs_on_overflow(monkeypatch):
    from engines.segment_timing_qa import post_tts_validate_and_retry

    segments = [
        {
            "text": "Дуже довгий текст для озвучення сегмента.",
            "plain_text": "Дуже довгий текст для озвучення сегмента.",
            "file": "seg0.mp3",
            "playback_duration": 6000,
            "tts_ms": 6000,
        }
    ]
    timing_map = [{"start": 0, "end": 5000}]
    calls = {"n": 0}

    from types import SimpleNamespace

    monkeypatch.setattr(
        "engines.semantic_optimizer.optimize_llm_rephrase_for_slot",
        lambda *a, **k: SimpleNamespace(
            text="Коротший текст.", changed=True, stopped_reason="fits_after_llm"
        ),
    )

    def regen_fn(text, **kwargs):
        calls["n"] += 1
        segments[0]["playback_duration"] = 4800
        segments[0]["tts_ms"] = 4800
        return f"retry_{calls['n']}.mp3", 4800

    stats = post_tts_validate_and_retry(
        segments,
        timing_map,
        source_segments=["A very long English source segment here."],
        voice="uk-UA-OstapNeural",
        target_lang="uk",
        src_lang="en",
        regen_fn=regen_fn,
        max_retries=3,
    )
    assert stats["adaptation_executed"] is True
    assert stats["retries"] >= 1
    trace = segments[0].get("text_adaptation_trace") or {}
    assert trace.get("executed")
    assert trace.get("first_tts_duration_ms") == 6000
    assert trace.get("final_tts_duration_ms") == 4800


def test_detect_post_tts_overflow_without_112pct_gate():
    """Overflow when tts > slot + tolerance, even below POST_TTS_OVERFLOW_RATIO."""
    from engines.segment_timing_qa import detect_post_tts_deviations

    seg = {
        "file": "seg0.mp3",
        "playback_duration": 5500,
        "tts_ms": 5500,
    }
    timing_map = [{"start": 0, "end": 5000}, {"start": 6000, "end": 8000}]
    issues = detect_post_tts_deviations(seg, 0, timing_map)
    overflow = [i for i in issues if i["code"] == "duration_overflow"]
    assert overflow
    assert overflow[0]["tts_ms"] == 5500
    assert overflow[0]["slot_ms"] == 5000


def test_post_tts_adaptation_runs_when_overflow_below_112pct(monkeypatch):
    from engines.segment_timing_qa import post_tts_validate_and_retry

    segments = [
        {
            "text": "Дуже довгий текст.",
            "plain_text": "Дуже довгий текст.",
            "file": "seg0.mp3",
            "playback_duration": 5500,
            "tts_ms": 5500,
        }
    ]
    timing_map = [{"start": 0, "end": 5000}, {"start": 6000, "end": 8000}]

    from types import SimpleNamespace

    monkeypatch.setattr(
        "engines.semantic_optimizer.optimize_llm_rephrase_for_slot",
        lambda *a, **k: SimpleNamespace(
            text="Коротше.", changed=True, stopped_reason="fits_after_llm"
        ),
    )

    def regen_fn(text, **kwargs):
        segments[0]["playback_duration"] = 4900
        segments[0]["tts_ms"] = 4900
        return "retry.mp3", 4900

    stats = post_tts_validate_and_retry(
        segments,
        timing_map,
        source_segments=["Long source."],
        voice="uk-UA-OstapNeural",
        target_lang="uk",
        src_lang="en",
        regen_fn=regen_fn,
        max_retries=3,
    )
    trace = segments[0]["text_adaptation_trace"]
    assert stats["adaptation_executed"]
    assert trace["executed"]
    assert trace["iterations"] > 0


def test_build_openddf_full_report_flags():
    from engines.segment_timing_qa import build_openddf_full_report

    report = build_openddf_full_report(
        {
            "task_id": "t1",
            "target_lang": "uk",
            "source_segments": ["Hello George."],
            "segments_data": [
                {
                    "text": "Привіт, Джордж.",
                    "plain_text": "Привіт, Джордж.",
                    "file": "a.mp3",
                    "playback_duration": 2000,
                    "tts_ms": 2000,
                    "text_adaptation_trace": {
                        "executed": False,
                        "iterations": 0,
                        "original_duration_ms": 2500,
                        "first_tts_duration_ms": 2000,
                        "final_tts_duration_ms": 2000,
                        "start_time_ms": 0,
                        "end_time_ms": 2500,
                        "timing_source": "timing_map",
                    },
                }
            ],
            "timing_map": [{"start": 0, "end": 2500}],
            "translation_audits": [
                {
                    "index": 0,
                    "whisper_text": "Hello George.",
                    "raw_translation": "Привіт George.",
                    "final_text": "Привіт, Джордж.",
                }
            ],
        }
    )
    assert report["summary"]["adaptation_status"] == "ADAPTATION NOT EXECUTED"
    assert "ADAPTATION NOT EXECUTED" in report["flags"]
    assert report["segments"][0]["first_tts_duration_ms"] == 2000
