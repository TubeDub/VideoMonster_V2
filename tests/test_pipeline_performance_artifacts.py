"""Tests for pipeline performance artifacts (TZ diagnostics)."""

from __future__ import annotations

from engines.pipeline_performance_artifacts import (
    BOTTLENECK_THRESHOLD_PCT,
    build_performance_report,
    build_timeline,
    write_performance_artifacts,
)


def test_bottleneck_detection_over_threshold():
    report = build_performance_report(
        "t1",
        app_dir=__import__("pathlib").Path("."),
        pipeline_timer_dict={
            "total_sec": 100.0,
            "stages": {"whisper": 10.0, "tts": 55.0, "mux": 5.0},
            "meta": {},
        },
        success=True,
    )
    bn = report["bottleneck"]
    assert bn is not None
    assert bn["stage"] == "tts"
    assert bn["exceeds_threshold"] is True
    assert bn["percent_of_total"] >= BOTTLENECK_THRESHOLD_PCT


def test_bottleneck_below_threshold():
    report = build_performance_report(
        "t2",
        app_dir=__import__("pathlib").Path("."),
        pipeline_timer_dict={
            "total_sec": 100.0,
            "stages": {"whisper": 30.0, "tts": 25.0, "mux": 20.0},
            "meta": {},
        },
    )
    bn = report["bottleneck"]
    assert bn is not None
    assert bn["exceeds_threshold"] is False


def test_write_artifacts_creates_files(tmp_path):
    task_id = "bench_test_001"
    paths = write_performance_artifacts(
        task_id,
        app_dir=tmp_path,
        pipeline_timer_dict={
            "total_sec": 42.0,
            "stages": {"whisper": 12.0, "translation": 20.0, "tts": 10.0},
            "meta": {},
        },
        success=True,
        video_path="benchmark_video.mp4",
    )
    report_path = tmp_path / "output" / "diagnostics" / task_id / "performance_report.json"
    timeline_path = tmp_path / "output" / "diagnostics" / task_id / "timeline.json"
    assert report_path.is_file()
    assert timeline_path.is_file()
    assert paths["performance_report_json"] == str(report_path)


def test_timeline_has_schema():
    tl = build_timeline(
        "t3",
        app_dir=__import__("pathlib").Path("."),
        pipeline_timer_dict={
            "total_sec": 10.0,
            "stages": {"whisper": 5.0, "tts": 5.0},
        },
    )
    assert tl["schema"] == "tubedub.timeline.v1"
    assert tl["event_count"] >= 2
