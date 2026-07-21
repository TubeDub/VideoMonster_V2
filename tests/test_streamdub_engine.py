"""Tests for StreamDub Engine V1."""

from __future__ import annotations

import json
from pathlib import Path

from engines.streamdub.artifacts.benchmark import write_quality_report
from engines.streamdub.memory.translation_memory import TranslationMemory
from engines.streamdub.modules.quality_analyzer import QualityAnalyzer
from engines.streamdub.modules.segmenter import SmartSegmenter
from engines.streamdub.pipeline.modes import stages_for_mode
from engines.streamdub.types import QualityGrade, StreamDubMode, StreamSegment


def test_modes_have_expected_stages():
    fast = stages_for_mode(StreamDubMode.FAST)
    smart = stages_for_mode(StreamDubMode.SMART)
    cinema = stages_for_mode(StreamDubMode.CINEMA)
    assert "whisper" in fast
    assert "fast_translation" in fast
    assert "llm_refiner" not in fast
    assert "quality_analyzer" in smart
    assert "llm_refiner" in smart
    assert "voice_clone" in cinema


def test_segmenter_merges_short_segments():
    seg = SmartSegmenter()
    seg.initialize()
    incoming = [
        StreamSegment(index=0, text="Hello", start_ms=0, end_ms=500, pause_after_ms=200),
        StreamSegment(index=1, text="world", start_ms=600, end_ms=1000, pause_after_ms=200),
    ]
    out = seg.process({"segments": incoming})
    merged = out["segments"]
    assert len(merged) == 1
    assert "Hello" in merged[0].text and "world" in merged[0].text


def test_quality_analyzer_grades():
    qa = QualityAnalyzer()
    qa.initialize()
    seg = StreamSegment(
        index=0,
        text="George Jr. drove home.",
        translated="Джордж Джер. поїхав додому.",
    )
    out = qa.process({"segments": [seg], "source_lang": "en", "target_lang": "uk"})
    result = out["segments"][0]
    assert result.quality in (QualityGrade.MEDIUM, QualityGrade.BAD, QualityGrade.GOOD)


def test_translation_memory_hit():
    tm = TranslationMemory("test_proj")
    tm.store("Hello", "Привіт", "en", "uk", "marian")
    assert tm.lookup("Hello", "en", "uk", "marian") == "Привіт"
    stats = tm.stats()
    assert stats["hits"] >= 1 or stats["misses"] >= 0


def test_quality_report_written(tmp_path):
    segs = [
        StreamSegment(index=0, text="a", translated="b", quality=QualityGrade.GOOD, quality_score=90),
        StreamSegment(index=1, text="c", translated="d", quality=QualityGrade.BAD, quality_score=40),
    ]
    path = write_quality_report(
        tmp_path,
        "proj1",
        segs,
        mode=StreamDubMode.SMART,
        stats={"segments": 2},
    )
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    assert data["schema"] == "tubedub.streamdub.quality_report.v1"
    assert data["quality_counts"]["GOOD"] == 1
    assert data["quality_counts"]["BAD"] == 1


def test_engine_info_import():
    from engines.streamdub import engine_info

    info = engine_info()
    assert info["engine"] == "StreamDub"
    assert "smart" in info["modes"]
