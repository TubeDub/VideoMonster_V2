"""Tests for OpenDDF Analyzer 2.0 report builder."""

from engines.openddf_analyzer_report import (
    ANALYZER_VERSION,
    build_analyzer_v2_report,
    export_analyzer_html,
)


def test_build_analyzer_v2_from_minimal_report():
    raw = {
        "task_id": "t1",
        "target_lang": "uk",
        "segments": [
            {
                "index": 0,
                "original_text": "George Lucas made Star Wars.",
                "translated_text": "Джордж Лукас зняв Зоряні війни.",
                "final_tts_text": "Джордж Лукас зняв Зоряні війни.",
                "slot_ms": 3000,
                "final_tts_duration_ms": 2800,
                "raw_translation": "Джордж Лукас зняв Зоряні війни.",
            }
        ],
    }
    report = build_analyzer_v2_report(raw)
    assert report["analyzer_version"] == ANALYZER_VERSION
    assert len(report["segments"]) == 1
    seg = report["segments"][0]
    assert seg["pipeline_stages"]
    assert seg["timing_detail"]["overflow_band"] in ("green", "yellow", "red")
    assert seg["integrity_checks"]
    assert report["statistics"]["segment_count"] == 1


def test_export_html_contains_task_id():
    report = {"task_id": "abc123", "statistics": {"segment_count": 0}}
    html = export_analyzer_html(report)
    assert "abc123" in html
    assert "OpenDDF Analyzer 2.0" in html
