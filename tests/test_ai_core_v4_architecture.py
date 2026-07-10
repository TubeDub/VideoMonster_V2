"""Tests for AI Core 4.0 architecture validation and contract enforcement."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engines.ai_core.architecture_validation import (
    AI_CORE_VERSION,
    ArchitectureMetrics,
    pipeline_checkpoint,
    write_architecture_validation,
)
from engines.ai_core.contract_enforcement import validate_segment_writes
from engines.ai_core.gatekeeper_peer import check_upstream_gate


def test_contract_enforcement_blocks_forbidden_write():
    before = {"index": 0, "translated_text": "Hi"}
    after = {"index": 0, "translated_text": "Hi", "semantic_text": "Привіт"}
    violations = validate_segment_writes("translation", before, after)
    assert violations
    assert "forbidden_write:semantic_text" in violations
    assert not validate_segment_writes("semantic", before, after)


def test_gatekeeper_peer_minimal():
    manifest = {"project_uuid": "abc"}
    state = {"segments": [{"index": 0}], "translation_agent_status": "success"}
    ok, errors, warnings = check_upstream_gate("semantic", manifest, state)
    assert ok
    assert not errors


def test_pipeline_checkpoint_progression():
    assert pipeline_checkpoint({}) == "start"
    assert pipeline_checkpoint({"source_segments": ["a"]}) == "post_stt"
    assert pipeline_checkpoint({"translation_agent_path": True}) == "post_translation"
    assert pipeline_checkpoint({"quality_agent_path": True}) == "post_ai_core_text"


def test_architecture_validation_json(tmp_path):
    metrics = ArchitectureMetrics(task_id="t-arch")
    metrics.record_agent("semantic", execution_time_ms=120.5, peer_ok=True)
    metrics.record_peer_return({"segment_index": 0, "error_code": "missing_translation"})
    metrics.pipeline_status = "completed"
    path = write_architecture_validation("t-arch", metrics, app_dir=tmp_path)
    assert path.is_file()
    data = path.read_text(encoding="utf-8")
    assert AI_CORE_VERSION in data
    assert "peer_validation" in data
    assert "ux_validation" in data
