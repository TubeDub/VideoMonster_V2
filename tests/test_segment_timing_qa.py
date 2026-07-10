"""Tests — segment timing QA (TZ §9–§11)."""

from __future__ import annotations

from engines.segment_timing_qa import (
    build_final_dub_qa_report,
    build_openddf_segment_diagnostics,
    detect_long_pauses,
    detect_timing_overlaps,
    normalize_timing_map_joints,
)


def test_detect_timing_overlap():
    timing_map = [{"start": 0, "end": 3000}, {"start": 2500, "end": 5000}]
    issues = detect_timing_overlaps(timing_map)
    assert len(issues) == 1
    assert issues[0]["code"] == "overlap"


def test_normalize_timing_map_fixes_overlap():
    timing_map = [{"start": 0, "end": 3000}, {"start": 2500, "end": 5000}]
    fixed, fixes = normalize_timing_map_joints(timing_map)
    assert fixes
    assert detect_timing_overlaps(fixed) == []


def test_detect_long_pause():
    timing_map = [{"start": 0, "end": 1000}, {"start": 2500, "end": 4000}]
    issues = detect_long_pauses(timing_map, max_pause_ms=700)
    assert len(issues) == 1
    assert issues[0]["code"] == "long_pause"


def test_build_final_dub_qa_report_ok():
    task_info = {
        "source_segments": ["Hello world."],
        "segments_data": [
            {
                "index": 0,
                "text": "Привет мир.",
                "plain_text": "Привет мир.",
                "playback_duration": 900,
                "file": "seg0.mp3",
            }
        ],
        "translation_audits": [
            {
                "index": 0,
                "raw_translation": "Привет мир.",
                "final_text": "Привет мир.",
                "tts_text": "Привет мир.",
            }
        ],
        "timing_map": [{"start": 0, "end": 2000}],
        "target_lang": "ru",
        "detected_lang": "en",
    }
    report = build_final_dub_qa_report(task_info)
    assert "ok" in report
    assert "issues" in report


def test_build_openddf_segment_diagnostics_fields():
    task_info = {
        "source_segments": ["Hello."],
        "segments_data": [
            {
                "index": 0,
                "text": "Привет.",
                "plain_text": "Привет.",
                "tts_text": "Привет.",
                "playback_duration": 800,
                "slot_ms": 2000,
            }
        ],
        "translation_audits": [
            {
                "index": 0,
                "whisper_text": "Hello.",
                "raw_translation": "Привет.",
                "naturalized_text": "Привет.",
                "final_text": "Привет.",
                "tts_text": "Привет.",
            }
        ],
        "timing_map": [{"start": 0, "end": 2000}],
        "target_lang": "ru",
        "voice": "ru-RU-DmitryNeural",
    }
    rows = build_openddf_segment_diagnostics(task_info)
    assert len(rows) == 1
    row = rows[0]
    assert row["original_text"] == "Hello."
    assert row["raw_translation"] == "Привет."
    assert row["final_tts_text"] == "Привет."
    assert row["actual_duration_ms"] == 800
    assert row["voice"] == "ru-RU-DmitryNeural"
