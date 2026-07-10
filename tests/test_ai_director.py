"""Tests for engines.ai_director."""

from __future__ import annotations

from engines.ai_director import validate_pipeline, format_report, QualityScore


def test_validate_clean_pipeline():
    report = validate_pipeline(
        source_segments=["Hello world"],
        translated_segments=["Привет мир"],
        timing_map=[{"start": 0, "end": 2000}],
    )
    assert report.score >= 0.7
    assert not report.block_export
    assert report.checks.get("no_placeholders")


def test_validate_placeholder_blocks():
    report = validate_pipeline(
        source_segments=["Hi"],
        translated_segments=["TODO placeholder"],
        timing_map=[{"start": 0, "end": 1000}],
    )
    assert any(i.code == "placeholder" for i in report.issues)
    assert not report.checks.get("no_placeholders")


def test_format_report():
    report = QualityScore(score=0.8, block_export=False, checks={"meaning_length": True})
    text = format_report(report)
    assert "Quality Score" in text
    assert "meaning_length" in text
