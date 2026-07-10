"""Tests for Performance Optimizer + Hardware Profiler (TZ #7)."""

from __future__ import annotations

import os
import time

import pytest

from core.benchmark import BenchmarkEngine, benchmark_enabled, run_benchmark
from core.hardware_profiler import (
    HardwareProfile,
    HardwareProfiler,
    get_hardware_profile,
)
from core.performance_monitor import (
    PerformanceMonitor,
    get_performance_monitor,
    monitor_enabled,
    reset_performance_monitor,
)
from core.performance_optimizer import (
    MODES,
    PerformanceDB,
    PerformanceOptimizer,
    TIER_BALANCED,
    TIER_MINIMAL,
    TIER_ULTRA,
    optimizer_enabled,
    reset_performance_optimizer,
)


@pytest.fixture(autouse=True)
def _isolate_perf(tmp_path, monkeypatch):
    """Each test gets its own performance DB and fresh singletons."""
    monkeypatch.setenv("VM_PERF_DIR", str(tmp_path / "perf"))
    monkeypatch.setenv("VM_PERF_OPTIMIZER", "1")
    monkeypatch.setenv("VM_PERF_MONITOR", "1")
    monkeypatch.setenv("VM_BENCHMARK", "1")
    monkeypatch.setenv("VM_PERF_MODE", "balanced")
    reset_performance_optimizer()
    reset_performance_monitor()
    yield
    reset_performance_optimizer()
    reset_performance_monitor()


# ── Hardware Profiler (§1) ───────────────────────────────────────────


def test_hardware_profile_detects_cpu():
    prof = get_hardware_profile(force=True)
    assert prof.cpu.logical_cores >= 1
    assert prof.cpu.physical_cores >= 1
    assert prof.os_name != ""


def test_hardware_profile_to_dict():
    prof = HardwareProfiler().profile(force=True)
    d = prof.to_dict()
    for key in ("cpu", "memory", "gpu", "disk", "os_name", "platform"):
        assert key in d


def test_hardware_signature_stable():
    p1 = get_hardware_profile(force=True)
    p2 = get_hardware_profile()
    assert p1.signature() == p2.signature()


# ── Benchmark Engine (§2) ────────────────────────────────────────────


def test_benchmark_runs_under_budget():
    engine = BenchmarkEngine(budget_s=30.0)
    result = engine.run(quick=True)
    assert result.total_ms < 35_000  # small headroom
    assert len(result.scores) == 7
    assert result.overall_score > 0


def test_benchmark_all_categories_scored():
    result = run_benchmark(quick=True)
    for cat in ("whisper", "llm", "tts", "disk_write", "disk_read", "audio", "mix"):
        assert cat in result.scores
        assert result.scores[cat] > 0


def test_benchmark_enabled_flag():
    os.environ["VM_BENCHMARK"] = "1"
    assert benchmark_enabled() is True
    os.environ["VM_BENCHMARK"] = "0"
    assert benchmark_enabled() is False
    os.environ.pop("VM_BENCHMARK", None)


# ── Performance Optimizer (§3–§5) ────────────────────────────────────


def test_optimizer_initializes_plan(tmp_path):
    opt = PerformanceOptimizer(app_dir=tmp_path)
    plan = opt.initialize(quick=True)
    assert plan.tier in (TIER_ULTRA, TIER_BALANCED, TIER_MINIMAL, "High", "Light")
    assert plan.max_concurrent_tasks >= 1
    assert plan.default_chunk_size >= 3
    assert len(plan.stages) >= 8


def test_optimizer_tier_selection_ultra():
    opt = PerformanceOptimizer()
    prof = HardwareProfile()
    prof.cpu.logical_cores = 32
    prof.memory.total_gb = 64.0
    prof.gpu.available = True
    prof.gpu.vram_gb = 24.0
    from core.benchmark import BenchmarkResult

    bench = BenchmarkResult(overall_score=200.0)
    assert opt._select_tier(prof, bench) == TIER_ULTRA


def test_optimizer_tier_selection_minimal():
    opt = PerformanceOptimizer()
    prof = HardwareProfile()
    prof.cpu.logical_cores = 2
    prof.memory.total_gb = 4.0
    prof.gpu.available = False
    from core.benchmark import BenchmarkResult

    bench = BenchmarkResult(overall_score=30.0)
    assert opt._select_tier(prof, bench) == TIER_MINIMAL


def test_optimizer_device_selection_gpu_whisper():
    opt = PerformanceOptimizer()
    prof = HardwareProfile()
    prof.gpu.available = True
    prof.gpu.vram_gb = 8.0
    assert opt._device_for("whisper", prof) == "gpu"


def test_optimizer_device_selection_cpu_llm():
    opt = PerformanceOptimizer()
    prof = HardwareProfile()
    prof.gpu.available = True
    prof.gpu.vram_gb = 16.0
    assert opt._device_for("ai_adaptation", prof) == "cpu"


def test_optimizer_user_modes_change_concurrency(tmp_path, monkeypatch):
    opt = PerformanceOptimizer(app_dir=tmp_path)
    monkeypatch.setenv("VM_PERF_MODE", "max_quality")
    plan_q = opt._build_plan()
    monkeypatch.setenv("VM_PERF_MODE", "max_performance")
    opt._plan = None
    plan_p = opt._build_plan()
    # Max performance should allow >= concurrency vs max quality.
    assert plan_p.max_concurrent_tasks >= plan_q.max_concurrent_tasks


def test_optimizer_modes_valid():
    assert "balanced" in MODES
    assert "max_quality" in MODES
    assert "max_performance" in MODES


# ── Performance DB (§9) ──────────────────────────────────────────────


def test_performance_db_roundtrip(tmp_path):
    db = PerformanceDB(tmp_path / "performance.db")
    sig = "test-machine"
    db.save_hardware(sig, {"cpu": {"logical_cores": 8}})
    assert db.get_hardware(sig)["cpu"]["logical_cores"] == 8
    db.save_benchmark(sig, {"overall_score": 99.0})
    assert db.get_benchmark(sig)["overall_score"] == 99.0
    db.record_run(sig, "proj-1", {"avg_ram_percent": 55.0})
    history = db.history(sig)
    assert len(history) == 1
    assert history[0]["avg_ram_percent"] == 55.0


# ── Pressure & prediction (§6–§7, §13) ───────────────────────────────


def test_pressure_critical_ram():
    opt = PerformanceOptimizer()
    ev = opt.evaluate_pressure(ram_percent=95.0)
    assert ev["severity"] == "critical"
    assert "reduce_chunk_size" in ev["actions"]


def test_pressure_apply_reduces_plan():
    opt = PerformanceOptimizer()
    plan = opt.initialize(quick=True)
    original_chunk = plan.default_chunk_size
    original_conc = plan.max_concurrent_tasks
    ev = opt.evaluate_pressure(ram_percent=96.0)
    opt.apply_pressure(plan, ev)
    assert plan.default_chunk_size <= original_chunk
    assert plan.max_concurrent_tasks <= original_conc


def test_predict_pressure_rising_ram():
    opt = PerformanceOptimizer()
    samples = [
        {"ram_percent": 60.0},
        {"ram_percent": 70.0},
        {"ram_percent": 80.0},
        {"ram_percent": 85.0},
    ]
    pred = opt.predict_pressure(samples)
    assert pred["predicted"] is True
    assert "reduce_chunk_size" in pred["recommend"]


# ── Bottleneck detection (§10) ───────────────────────────────────────


def test_bottleneck_detection():
    opt = PerformanceOptimizer()
    durations = {"whisper": 10.0, "translation": 45.0, "tts": 20.0}
    assert opt.detect_bottleneck(durations) == "translation"


def test_bottleneck_rebalance():
    opt = PerformanceOptimizer()
    plan = opt.initialize(quick=True)
    before = plan.stages["translation"].workers
    opt.rebalance_for_bottleneck(plan, "translation")
    assert plan.stages["translation"].workers >= before


# ── Self-learning (§8) ───────────────────────────────────────────────


def test_record_film_persists_history(tmp_path):
    opt = PerformanceOptimizer(app_dir=tmp_path)
    opt.initialize(quick=True)
    opt.record_film("film-1", {"avg_ram_percent": 72.0, "processing_speed": 3.5})
    sig = opt._profile.signature() if opt._profile else "unknown"
    history = opt.db.history(sig)
    assert len(history) >= 1


# ── Performance Monitor (§12) ────────────────────────────────────────


def test_monitor_sample():
    mon = PerformanceMonitor(interval_s=0.1)
    s = mon.sample()
    assert s.sampled_at > 0
    assert s.ram_total_gb >= 0


def test_monitor_record_items_speed():
    mon = PerformanceMonitor()
    mon.record_items(10)
    time.sleep(0.05)
    mon.record_items(20)
    s = mon.sample()
    assert s.processing_speed >= 0


def test_monitor_averages():
    mon = PerformanceMonitor()
    mon.sample()
    mon.sample()
    avgs = mon.averages()
    assert "avg_cpu_percent" in avgs
    assert avgs["samples"] >= 2


def test_monitor_pipeline_provider():
    mon = PerformanceMonitor()
    mon.set_pipeline_provider(lambda: {
        "queue_depth": 5, "active_agents": 3, "chunk_size": 12,
    })
    s = mon.sample()
    assert s.queue_depth == 5
    assert s.active_agents == 3
    assert s.current_chunk_size == 12


def test_flags():
    os.environ["VM_PERF_OPTIMIZER"] = "1"
    assert optimizer_enabled() is True
    os.environ["VM_PERF_MONITOR"] = "1"
    assert monitor_enabled() is True
