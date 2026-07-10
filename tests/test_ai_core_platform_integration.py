"""Integration tests for AI Core Platform v3.x architectural layers (TZ)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_base_agent_exists():
    from engines.ai_core.agents.base import Agent, BaseAgent

    assert issubclass(BaseAgent, Agent)


def test_orchestrator_agent_base():
    from engines.ai_core.orchestrator_agent_base import OrchestratorAgentBase

    class Demo(OrchestratorAgentBase):
        agent_id = "demo"

        def run(self, manifest, state, task_id):
            return self.to_result(status="success", state=state)

    r = Demo().run({}, {}, "t1")
    assert r.status == "success"
    assert "protocol_version" in (r.metrics or {})


def test_ai_memory_service():
    from engines.ai_core.services.ai_memory import get_memory_service, reset_memory_service

    reset_memory_service("mem-test")
    mem = get_memory_service("mem-test")
    mem.record(0, agent="translation", event="retry", retry_count=1)
    mem.record(0, agent="timing", event="shorten")
    assert mem.shorten_count(0) == 1
    assert not mem.should_use_compact_strategy(0, threshold=2)
    mem.record(0, agent="timing", event="compact_translation")
    assert mem.should_use_compact_strategy(0, threshold=2)
    reset_memory_service("mem-test")


def test_voice_profile_manager():
    from engines.ai_core.services.voice_profile_manager import get_voice_profile_manager

    mgr = get_voice_profile_manager()
    voices = mgr.list_voices("uk")
    assert voices
    profile = mgr.get_profile(voices[0]["id"], "uk")
    assert profile["voice_engine"] == "edge-tts"
    assert profile["average_speech_rate"] > 0


def test_timing_predictor_hierarchy():
    from engines.ai_core.timing_predictor import (
        AdaptiveTimingPredictor,
        BaseTimingPredictor,
        HeuristicTimingPredictor,
        get_timing_predictor,
    )

    h = HeuristicTimingPredictor()
    assert h.predict_ms("Hello world test", "en") > 0
    assert isinstance(get_timing_predictor(), BaseTimingPredictor)
    assert isinstance(AdaptiveTimingPredictor(), BaseTimingPredictor)


def test_quality_gate_pre_tts():
    from engines.ai_core.quality_gate import get_quality_gate

    gate = get_quality_gate()
    result = gate.run_pre_tts("Привіт світ", tgt_lang="uk", segment={"timing_slot_ms": 5000})
    assert hasattr(result, "passed")
    assert result.checks or result.skipped or result.passed


def test_ai_network_dag():
    from engines.ai_core.ai_network.dag import dag_snapshot, get_execution_order, validate_dag

    assert validate_dag()
    order = get_execution_order()
    assert order[0] == "planner"
    assert "quality" in order
    snap = dag_snapshot()
    assert snap["valid"] is True


def test_feature_flag_aliases():
    from engines.ai_core.platform.feature_registry import (
        is_platform_feature_enabled,
        list_platform_features,
    )

    feats = {f["tz_id"]: f["id"] for f in list_platform_features() if f.get("tz_id")}
    assert feats.get("Streaming") == "streaming_pipeline"
    assert feats.get("QualityGate") == "quality_gate"
    assert isinstance(is_platform_feature_enabled("Streaming"), bool)
    assert isinstance(is_platform_feature_enabled("qualitygate"), bool)


def test_observability_collector():
    from engines.ai_core.observability import get_observability, record_agent_execution, reset_observability

    reset_observability("obs-test")
    record_agent_execution("obs-test", "translation", ms=120.5, segments=[{"index": 0}])
    snap = get_observability("obs-test").snapshot()
    assert snap["agents"]
    assert snap["agents"][0]["execution_time_ms"] == 120.5
    reset_observability("obs-test")
