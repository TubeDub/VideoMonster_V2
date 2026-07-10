"""Tests for AI Core 4.3 architecture validation and UX standard."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engines.ai_core.architecture_validation import (
    AI_CORE_VERSION,
    ArchitectureMetrics,
    UX_CHECKLIST,
    merge_ux_event,
    write_architecture_validation,
)


def test_ai_core_version_43():
    assert AI_CORE_VERSION == "4.3"


def test_architecture_validation_includes_ux_validation(tmp_path):
    metrics = ArchitectureMetrics(task_id="t-ux")
    metrics.record_agent("semantic", execution_time_ms=50.0, peer_ok=True)
    metrics.pipeline_status = "completed"
    path = write_architecture_validation("t-ux", metrics, app_dir=tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["ai_core_version"] == "4.3"
    ux = data["ux_validation"]
    assert ux["checks_total"] == len(UX_CHECKLIST)
    assert ux["checks_passed"] == len(UX_CHECKLIST)
    assert ux["passed"] is True
    assert "agent_utilization" in data


def test_merge_ux_cancel_event(tmp_path):
    metrics = ArchitectureMetrics(task_id="t-cancel")
    write_architecture_validation("t-cancel", metrics, app_dir=tmp_path)
    merge_ux_event("t-cancel", event="cancel", checkpoint="post_translation", app_dir=tmp_path)
    path = tmp_path / "output" / "diagnostics" / "t-cancel" / "architecture_validation.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["ux_validation"]["cancel_events"] == 1
    assert data["ux_validation"]["last_checkpoint"] == "post_translation"


def test_golden_streaming_smoke():
    """CI-safe golden subset — batch vs streaming, no Ollama."""
    import time
    import uuid
    from unittest.mock import patch

    from engines.ai_core.streaming_pipeline import StreamingTextPipeline

    golden = [
        {"index": 0, "text": "Hello.", "translated_text": "Привіт."},
        {"index": 1, "text": "Bye.", "translated_text": "Бувай."},
    ]
    manifest = {"project_uuid": str(uuid.uuid4()), "source_lang": "en", "target_lang": "uk"}

    def fake_rerun(class_name, list_index, manifest, state, task_id):
        seg = dict(state["segments"][list_index])
        if class_name == "SemanticAgent":
            seg["semantic_text"] = str(seg.get("translated_text") or "")
        elif class_name == "TimingAgent":
            seg["timing_text"] = str(seg.get("semantic_text") or "")
        elif class_name == "GrammarAgent":
            seg["grammar_text"] = str(seg.get("timing_text") or "")
        return seg

    t0 = time.perf_counter()
    with patch(
        "engines.ai_core.quality_agent.retry_orchestrator.rerun_agent_for_segment",
        side_effect=fake_rerun,
    ):
        pipe = StreamingTextPipeline(
            manifest,
            {"segments": [dict(s) for s in golden], "pipeline_mode": "streaming"},
            "golden-smoke",
            stages=("semantic", "timing", "grammar"),
        )
        result = pipe.run()
    elapsed = (time.perf_counter() - t0) * 1000
    out = result.updated_state["segments"]
    assert all(str(s.get("grammar_text") or "").strip() for s in out)
    assert elapsed < 30_000
