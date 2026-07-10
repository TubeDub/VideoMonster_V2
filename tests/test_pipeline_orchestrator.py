"""Tests for Pipeline Orchestrator foundation + LLM Orchestrator."""

from __future__ import annotations

import time

import pytest

from engines.llm_orchestrator.model_pool import LLMModelInfo, LLMModelPool, ModelTier
from engines.llm_orchestrator.router import assess_segment_difficulty, route_segment
from engines.pipeline_orchestrator.conveyor import PipelineConveyor, StageConfig, WorkItem
from engines.pipeline_orchestrator.resource_planner import ResourcePlanner


def test_resource_planner_returns_dynamic_workers():
    planner = ResourcePlanner()
    snap = planner.snapshot(force=True)
    assert snap.cpu_cores >= 1
    plan = planner.plan_stage("translation", segment_count=20)
    assert plan.workers >= 1
    assert plan.queue_size >= 4
    assert plan.batch_size >= 1
    assert plan.timeout_scale >= 1.0


def test_resource_planner_bottleneck_detection():
    planner = ResourcePlanner()
    planner.record_throughput("translation", 10.0)
    planner.record_throughput("tts", 2.0)
    assert planner._bottleneck_stage() == "tts"
    tts_plan = planner.plan_stage("tts", segment_count=10)
    assert tts_plan.bottleneck is True


def test_segment_difficulty_routes_complex_to_strong():
    d = assess_segment_difficulty(
        "George Jr. told Haskell about USC and Hollywood cinematography.",
        "Джордж розповів про Голлівуд.",
        target_lang="uk",
    )
    assert d.tier in (ModelTier.STANDARD, ModelTier.STRONG)
    assert d.score >= 0.25


def test_segment_difficulty_short_is_light():
    d = assess_segment_difficulty("Hello.", "Привіт.")
    assert d.tier == ModelTier.LIGHT


def test_model_pool_tier_classification():
    pool = LLMModelPool()
    with pool._lock:
        pool._models = {
            "qwen2.5:3b": LLMModelInfo(
                name="qwen2.5:3b", provider="ollama", param_b=3.0,
                tier=ModelTier.LIGHT, adequate=False,
            ),
            "qwen2.5:14b": LLMModelInfo(
                name="qwen2.5:14b", provider="ollama", param_b=14.0,
                tier=ModelTier.STRONG, adequate=True,
            ),
        }
        pool._discovered_at = time.monotonic()
    diff = assess_segment_difficulty(
        "He applied to USC film school in Hollywood.",
        "",
    )
    model = route_segment(pool, diff, require_adequate=True)
    assert model is not None
    assert model.adequate is True
    assert model.name == "qwen2.5:14b"


def test_pipeline_conveyor_runs_stages_in_parallel():
    order: list[str] = []
    lock = __import__("threading").Lock()

    def make_handler(name: str, delay: float = 0.05):
        def handler(item: WorkItem) -> WorkItem:
            with lock:
                order.append(f"{name}:{item.segment_index}")
            time.sleep(delay)
            item.payload[name] = True
            return item

        return handler

    stages = [
        StageConfig("a", make_handler("a", 0.02)),
        StageConfig("b", make_handler("b", 0.02)),
    ]
    conveyor = PipelineConveyor(stages, task_id="test-conv")
    items = [WorkItem(segment_index=i, payload={}) for i in range(3)]
    results = conveyor.run(items)
    assert len(results) == 3
    for r in results:
        assert r.payload.get("a") and r.payload.get("b")
    report = conveyor.report()
    assert report["metrics"]["a"]["processed"] == 3
    assert report["metrics"]["b"]["processed"] == 3


def test_llm_orchestrator_respects_circuit_open(monkeypatch):
    from engines.llm_orchestrator.orchestrator import LLMOrchestrator, LLMTask

    monkeypatch.setenv("VM_LLM_ORCHESTRATOR", "1")

    def fake_circuit_open():
        return True

    monkeypatch.setattr("engines.translation_adapt.circuit_open", fake_circuit_open)

    orch = LLMOrchestrator(pool=LLMModelPool())
    result = orch.run_sync(
        LLMTask(segment_index=0, prompt="test", source_text="USC Hollywood")
    )
    assert result.ok is False
    assert result.skip_reason == "llm_circuit_open"
