"""RASM R1–R5 — metrics, detectors, reports, hooks, compare."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _seg(start, end, fitted, **extra):
    row = {
        "segment_id": extra.pop("segment_id", f"s{start}"),
        "start_ms": start,
        "end_ms": end,
        "fitted_ms": fitted,
        "fitted_file": extra.pop("fitted_file", "x.mp3") if fitted else None,
    }
    row.update(extra)
    return row


def test_r1_fits_entirely_green():
    from engines.rasm.metrics import compute_segment_metrics

    m = compute_segment_metrics(_seg(0, 1000, 800), index=0)
    assert m.status == "green"
    assert m.reserve_ms == 200
    assert m.overflow_ms == 0
    assert m.fitted_file_ok


def test_r1_overflow_red():
    from engines.rasm.metrics import compute_segment_metrics

    m = compute_segment_metrics(_seg(0, 1000, 1420), index=0)
    assert m.status == "red"
    assert m.overflow_ms == 420
    assert m.placement_overflow_ms == 420
    assert m.duration_overflow_ms == 420
    assert "overflow" in m.flags


def test_r1_tight_reserve_yellow():
    from engines.rasm.metrics import compute_segment_metrics
    from engines.rasm.config import RasmSettings

    m = compute_segment_metrics(
        _seg(0, 1000, 920),
        index=0,
        settings=RasmSettings(yellow_reserve_ms=150),
    )
    assert m.status == "yellow"
    assert m.reserve_ms == 80
    assert "tight_reserve" in m.flags


def test_r1_no_fitted_is_red():
    from engines.rasm.metrics import compute_segment_metrics

    m = compute_segment_metrics(
        {"segment_id": "a", "start_ms": 0, "end_ms": 1000, "fitted_ms": 0},
        index=0,
    )
    assert m.status == "red"
    assert "no_fitted" in m.flags
    assert m.sync_qc == "SYNC_FAIL"


def test_r1_uses_fitted_not_raw_tts():
    from engines.rasm.metrics import compute_segment_metrics

    m = compute_segment_metrics(
        {
            "segment_id": "a",
            "start_ms": 0,
            "end_ms": 1000,
            "tts_ms": 2000,  # pre-fit — must NOT drive dub_end when fitted exists
            "fitted_ms": 900,
            "fitted_file": "f.mp3",
        },
        index=0,
    )
    assert m.dub_duration_ms == 900
    assert m.overflow_ms == 0


def test_r2_early_late_flags():
    from engines.rasm.metrics import compute_segment_metrics
    from engines.rasm.config import RasmSettings

    cfg = RasmSettings(early_start_threshold_ms=100, late_start_threshold_ms=120)
    early = compute_segment_metrics(
        _seg(500, 1500, 800, place_delay_ms=-200),
        index=0,
        settings=cfg,
    )
    assert early.early_ms == 200
    assert "early_start" in early.flags

    late = compute_segment_metrics(
        _seg(500, 1500, 800, place_delay_ms=200),
        index=0,
        settings=cfg,
    )
    assert late.late_ms == 200
    assert "late_start" in late.flags


def test_r2_overlap_and_gap():
    from engines.rasm.metrics import analyze_segments
    from engines.rasm.config import RasmSettings

    segs = [
        _seg(0, 1000, 1100, segment_id="a"),  # overflows into next
        _seg(1000, 2000, 500, segment_id="b"),
    ]
    rows = analyze_segments(segs, settings=RasmSettings(overlap_epsilon_ms=10, gap_threshold_ms=450))
    assert rows[0].overlap_with_next is True
    assert "overlap" in rows[0].flags

    segs2 = [
        _seg(0, 1000, 400, segment_id="a"),
        _seg(1000, 2000, 400, segment_id="b", place_delay_ms=600),  # gap large
    ]
    # dub_end a = 400; next dub_start = 1600; gap = 1200
    rows2 = analyze_segments(segs2, settings=RasmSettings(gap_threshold_ms=450))
    assert rows2[0].gap_to_next_ms == 1200
    assert "gap" in rows2[0].flags


def test_r4_reports_written(tmp_path):
    from engines.rasm.reports import write_sync_reports

    segs = [
        _seg(0, 1000, 800, segment_id="g"),
        _seg(1000, 2000, 1500, segment_id="r"),
    ]
    out = write_sync_reports("task_demo", segs, app_dir=tmp_path)
    assert out["ok"]
    paths = out["paths"]
    assert Path(paths["json"]).is_file()
    assert Path(paths["html"]).is_file()
    assert Path(paths["csv"]).is_file()
    payload = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
    assert payload["stats"]["segments_total"] == 2
    assert payload["stats"]["red"] >= 1
    html = Path(paths["html"]).read_text(encoding="utf-8")
    assert "RASM Sync Report" in html
    csv_text = Path(paths["csv"]).read_text(encoding="utf-8")
    assert "overflow_ms" in csv_text


def test_r5_hooks_no_post_lock_mutation():
    from engines.rasm.hooks import propose_dsal_compression, apply_tqe_sync_flags

    segs = [
        {
            "segment_id": "locked",
            "start_ms": 0,
            "end_ms": 1000,
            "fitted_ms": 1500,
            "fitted_file": "a.mp3",
            "translation_locked": True,
            "approved_text": "LOCKED TEXT",
        },
        {
            "segment_id": "open",
            "start_ms": 1000,
            "end_ms": 2000,
            "fitted_ms": 1500,
            "fitted_file": "b.mp3",
            "translation_locked": False,
            "translated_text": "open text",
        },
    ]
    before = segs[0]["approved_text"]
    dsal = propose_dsal_compression(segs, info={"translation_locked": False})
    assert dsal["skipped_locked"] >= 1
    assert all(p["segment_id"] != "locked" for p in dsal["proposals"])
    assert any(p["segment_id"] == "open" for p in dsal["proposals"])
    assert all(p.get("mutate_text") is False for p in dsal["proposals"])
    assert segs[0]["approved_text"] == before

    apply_tqe_sync_flags(segs)
    assert segs[0].get("sync_qc") in ("SYNC_FAIL", "SYNC_WARNING")
    assert segs[0]["approved_text"] == before  # still unchanged


def test_r5_before_after_compare(tmp_path):
    from engines.rasm.reports import write_sync_reports
    from engines.rasm.compare import compare_sync_reports

    before_segs = [_seg(0, 1000, 1400, segment_id="a"), _seg(1000, 2000, 1400, segment_id="b")]
    after_segs = [_seg(0, 1000, 800, segment_id="a"), _seg(1000, 2000, 850, segment_id="b")]
    b = write_sync_reports("before", before_segs, app_dir=tmp_path)
    a = write_sync_reports("after", after_segs, app_dir=tmp_path)
    diff = compare_sync_reports(b["paths"]["json"], a["paths"]["json"])
    assert diff["ok"]
    assert diff["summary"]["improved"] is True
    assert diff["deltas"]["red"]["after"] < diff["deltas"]["red"]["before"]


def test_analyze_project_end_to_end(tmp_path):
    from engines.rasm.analyze import analyze_project

    segs = [_seg(0, 1000, 800), _seg(1000, 2000, 1300)]
    result = analyze_project("t1", segs, app_dir=tmp_path, write_reports=True, apply_hooks=True)
    assert result["ok"]
    assert result["phase"] == "R5"
    assert result["stats"]["segments_total"] == 2
    assert result["reports"].get("json")


def test_api_rasm_routes_extended():
    from api import studio_api as mod

    for name in (
        "api_rasm_analyze",
        "api_rasm_report",
        "api_rasm_compare",
        "api_rasm_hooks",
        "api_studio_original",
    ):
        assert hasattr(mod, name)
