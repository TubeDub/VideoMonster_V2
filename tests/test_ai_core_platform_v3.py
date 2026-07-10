"""Tests for TubeDub AI Core Platform — Master Spec v3.0 Stage 1."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_platform_versions():
    from engines.ai_core.platform import (
        AI_BUS_VERSION,
        MANIFEST_VERSION,
        PLATFORM_SPEC_VERSION,
        PROTOCOL_VERSION,
        platform_versions,
    )

    assert PLATFORM_SPEC_VERSION == "3.0"
    assert AI_BUS_VERSION == "1.0"
    assert PROTOCOL_VERSION == "1.0"
    assert MANIFEST_VERSION == "3.0"
    pv = platform_versions()
    assert pv["platform_spec_version"] == "3.0"
    assert "ai_bus_version" in pv


def test_capability_registry_schema():
    from engines.ai_core.platform.capability_registry import build_registry

    reg = build_registry()
    assert reg["schema"] == "tubedub.capability_registry.v1"
    ids = {c["id"] for c in reg["capabilities"]}
    assert "llm" in ids
    assert "asr" in ids
    assert "tts" in ids
    assert "lipsync" in ids
    for cap in reg["capabilities"]:
        assert "status" in cap
        assert "label" in cap


def test_capability_matrix_includes_registry():
    from engines.ai_core.capability_matrix import build_capability_matrix

    cap = build_capability_matrix()
    assert "registry" in cap
    assert cap["registry"]["schema"] == "tubedub.capability_registry.v1"


def test_ai_bus_manifest_and_recovery():
    from engines.ai_core.platform import get_bus, reset_bus

    run_id = "platform-bus-test"
    reset_bus(run_id)
    bus = get_bus(run_id)
    manifest = {"pipeline_version": "3.0", "target_lang": "uk", "project_uuid": "abc"}
    bus.publish_manifest(manifest)
    assert bus.get_manifest()["target_lang"] == "uk"
    bus.update_state("planner", {"pipeline_mode": "standard"})
    assert bus.get_state()["pipeline_mode"] == "standard"

    routed = []
    from engines.ai_core.platform.ai_bus import register_recovery_handler

    register_recovery_handler(lambda a: routed.append(a))
    action = bus.route_recovery(
        from_agent="quality",
        to_agent="translation",
        segment_index=2,
        reason="empty_translation",
        priority=1,
    )
    assert action["assigned_agent"] == "translation"
    assert routed and routed[-1]["segment_index"] == 2
    snap = bus.snapshot()
    assert snap["recovery_pending"] >= 1
    reset_bus(run_id)


def test_agent_protocol_mixin():
    from engines.ai_core.platform.agent_protocol import AgentProtocolMixin

    class DemoAgent(AgentProtocolMixin):
        agent_id = "demo"
        agent_version = "0.1"

    agent = DemoAgent()
    meta = agent.protocol_meta()
    assert meta["agent_id"] == "demo"
    assert meta["protocol_version"] == "1.0"
    assert "global_skill_version" in meta
    metrics = agent.execution_metrics(ms=12.5, processed=3)
    assert metrics["execution_time_ms"] == 12.5
    assert metrics["processed_segments"] == 3


def test_project_state_guard():
    from engines.ai_core.platform.project_state import ProjectStateGuard

    before = {"translated_text": "a", "timing_slot_ms": 1000}
    after = {"translated_text": "b", "timing_slot_ms": 1000}
    assert not ProjectStateGuard.validate_segment_write("translation", before, after)
    bad = {"translated_text": "a", "timing_slot_ms": 900}
    violations = ProjectStateGuard.validate_segment_write("translation", before, bad)
    assert any("timing_slot_ms" in v for v in violations)


def test_platform_feature_registry():
    from engines.ai_core.platform.feature_registry import (
        is_platform_feature_enabled,
        list_platform_features,
    )

    feats = list_platform_features()
    ids = {f["id"] for f in feats}
    assert "quality_gate" in ids
    assert "streaming_pipeline" in ids
    assert isinstance(is_platform_feature_enabled("quality_gate"), bool)


def test_freeze_manifest_is_copy():
    from engines.ai_core.platform.project_state import freeze_manifest

    src = {"a": 1, "nested": {"b": 2}}
    frozen = freeze_manifest(src)
    src["a"] = 99
    assert frozen["a"] == 1
