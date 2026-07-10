"""Tests for TubeDub Planner Agent v3.0."""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engines.ai_core.golden_dataset import GOLDEN_MANIFEST_KEYS, compare_manifest_keys
from engines.ai_core.capability_matrix import build_capability_matrix
from engines.ai_core.smoke_tests import run_smoke_tests
from engines.ai_core.planner_agent import PlannerAgent


@pytest.fixture
def tiny_video(tmp_path):
    """Minimal valid-ish media file for ffprobe (may lack audio)."""
    path = tmp_path / "sample_en_test.mp4"
    # Empty file — planner should warn but not crash
    path.write_bytes(b"\x00" * 64)
    return path


@pytest.fixture
def planner(tmp_path, monkeypatch):
    out = tmp_path / "output"
    out.mkdir()
    monkeypatch.setattr(
        "engines.ai_core.planner_agent._MANIFESTS_DIR",
        out / "manifests",
    )
    monkeypatch.setattr(
        "engines.ai_core.planner_agent._OUTPUT_DIR",
        out,
    )
    return PlannerAgent(output_dir=out)


def test_creates_uuid(planner, tiny_video):
    result = planner.run(str(tiny_video), "uk", source_lang="en", task_id="t-uuid")
    uid = result.updated_state["project_uuid"]
    uuid.UUID(uid)
    assert result.updated_state["project_uuid"] == uid


def test_manifest_structure(planner, tiny_video):
    result = planner.run(str(tiny_video), "uk", source_lang="en", task_id="t-struct")
    manifest = result.updated_state["manifest"]
    cmp = compare_manifest_keys(manifest)
    assert cmp["ok"], f"missing keys: {cmp['missing']}"
    assert manifest["ai_core_version"] == "3.0"
    assert manifest["planner_version"] == "3.0"
    assert manifest["target_lang"] == "uk"
    assert manifest["source_lang"] == "en"


def test_read_only_no_file_modification(planner, tiny_video):
    before = tiny_video.read_bytes()
    mtime_before = tiny_video.stat().st_mtime
    planner.run(str(tiny_video), "uk", task_id="t-readonly")
    assert tiny_video.read_bytes() == before
    assert tiny_video.stat().st_mtime == mtime_before


def test_capability_matrix():
    cap = build_capability_matrix()
    assert "ffmpeg" in cap
    assert "llm" in cap
    assert "asr" in cap
    assert "tts" in cap
    assert "gpu" in cap


def test_complexity_score(planner, tiny_video):
    result = planner.run(str(tiny_video), "uk", task_id="t-complex")
    complexity = result.updated_state["complexity_score"]
    assert complexity in ("LOW", "MEDIUM", "HIGH", "EXTREME")


def test_smoke_tests_run():
    cap = build_capability_matrix()
    smoke = run_smoke_tests(cap)
    assert "tests" in smoke
    assert smoke["total"] >= 5
    assert "all_passed" in smoke


def test_planner_report_json(planner, tiny_video, tmp_path):
    result = planner.run(str(tiny_video), "uk", task_id="t-report")
    report_path = Path(result.updated_state["planner_report_path"])
    assert report_path.is_file()
    with open(report_path, encoding="utf-8") as fh:
        report = json.load(fh)
    assert report["planner_version"] == "3.0"
    assert "warnings" in report
    assert "errors" in report
    assert report["project_uuid"] == result.updated_state["project_uuid"]


def test_integration_import():
    from engines.ai_core.planner_agent import PlannerAgent as PA
    from engines.ai_core import PlannerAgent as PA2

    assert PA is PA2
    assert PA.VERSION == "3.0"


def test_golden_keys_complete():
    """Contract keys used by golden_dataset match ProjectManifest fields."""
    from engines.ai_core.contracts import ProjectManifest

    field_names = set(ProjectManifest.__dataclass_fields__.keys())  # type: ignore[attr-defined]
    assert GOLDEN_MANIFEST_KEYS <= field_names
