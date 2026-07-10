"""Performance Optimizer — automatic hardware-aware tuning (TZ #7).

Combines the Hardware Profiler (§1) and Benchmark Engine (§2) into an automatic
performance profile (§3), a dynamic resource plan (§4), per-stage device
selection (§5), thermal/memory safeguards (§6–§7), self-learning (§8), a
Performance Database (§9), bottleneck detection (§10), user modes (§11), and
load prediction (§13).

Design rules (TZ #7):
* No fixed performance settings — everything is derived from hardware + measured
  benchmark + accumulated history.
* Never degrades dubbing quality: only parallelism, queue sizes, chunk sizes,
  device placement, and resource limits change.
* Never crashes on low-memory: it reduces load instead of failing.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from core.benchmark import BenchmarkResult, run_benchmark
from core.hardware_profiler import HardwareProfile, get_hardware_profile

logger = logging.getLogger("tubedub.performance_optimizer")

# Hardware profile tiers (§3).
TIER_ULTRA = "Ultra"
TIER_HIGH = "High"
TIER_BALANCED = "Balanced"
TIER_LIGHT = "Light"
TIER_MINIMAL = "Minimal"
TIERS = (TIER_ULTRA, TIER_HIGH, TIER_BALANCED, TIER_LIGHT, TIER_MINIMAL)

# User modes (§11) — never change model quality, only resource usage.
MODE_MAX_QUALITY = "max_quality"
MODE_BALANCED = "balanced"
MODE_MAX_PERFORMANCE = "max_performance"
MODES = (MODE_MAX_QUALITY, MODE_BALANCED, MODE_MAX_PERFORMANCE)

# Canonical dubbing stages.
STAGE_WHISPER = "whisper"
STAGE_CLEANER = "cleaner"
STAGE_TRANSLATION = "translation"
STAGE_AI_ADAPTATION = "ai_adaptation"
STAGE_TTS = "tts"
STAGE_TIMING = "timing"
STAGE_MIX = "mix"
STAGE_EXPORT = "export"
ALL_STAGES = (
    STAGE_WHISPER, STAGE_CLEANER, STAGE_TRANSLATION, STAGE_AI_ADAPTATION,
    STAGE_TTS, STAGE_TIMING, STAGE_MIX, STAGE_EXPORT,
)

# Stages that can benefit from GPU acceleration when present.
_GPU_CAPABLE = frozenset({STAGE_WHISPER, STAGE_TTS, STAGE_MIX})
# Stages that are LLM/CPU bound.
_LLM_STAGES = frozenset({STAGE_TRANSLATION, STAGE_AI_ADAPTATION})

# Safety thresholds (§6–§7).
_RAM_SOFT_LIMIT = 80.0
_RAM_HARD_LIMIT = 92.0
_VRAM_SOFT_LIMIT = 82.0
_TEMP_SOFT_C = 82.0
_TEMP_HARD_C = 90.0


def optimizer_enabled() -> bool:
    return str(os.getenv("VM_PERF_OPTIMIZER", "1")).strip().lower() not in (
        "0", "false", "no", "off",
    )


def _current_mode() -> str:
    m = str(os.getenv("VM_PERF_MODE", MODE_BALANCED)).strip().lower()
    return m if m in MODES else MODE_BALANCED


@dataclass
class StageResourcePlan:
    """Per-stage tuning derived automatically (§4, §5)."""

    stage: str
    device: str  # cpu | gpu | hybrid
    workers: int
    chunk_size: int
    max_in_flight: int
    queue_size: int
    memory_limit_mb: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ResourcePlan:
    """Full automatic plan for the whole pipeline (§4)."""

    tier: str
    mode: str
    max_concurrent_tasks: int
    cpu_workers: int
    gpu_workers: int
    default_chunk_size: int
    ram_budget_gb: float
    vram_budget_gb: float
    use_gpu: bool
    stages: dict[str, StageResourcePlan] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "mode": self.mode,
            "max_concurrent_tasks": self.max_concurrent_tasks,
            "cpu_workers": self.cpu_workers,
            "gpu_workers": self.gpu_workers,
            "default_chunk_size": self.default_chunk_size,
            "ram_budget_gb": round(self.ram_budget_gb, 2),
            "vram_budget_gb": round(self.vram_budget_gb, 2),
            "use_gpu": self.use_gpu,
            "stages": {k: v.to_dict() for k, v in self.stages.items()},
            "notes": self.notes,
        }


# ── Performance Database (§9) ────────────────────────────────────────


class PerformanceDB:
    """SQLite store: hardware profile, benchmark, history, recommendations."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init()

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path), check_same_thread=False)

    def _init(self) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS hardware_profiles (
                        signature TEXT PRIMARY KEY,
                        profile TEXT NOT NULL,
                        updated_at REAL
                    );
                    CREATE TABLE IF NOT EXISTS benchmarks (
                        signature TEXT PRIMARY KEY,
                        result TEXT NOT NULL,
                        updated_at REAL
                    );
                    CREATE TABLE IF NOT EXISTS performance_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        signature TEXT NOT NULL,
                        project_id TEXT DEFAULT '',
                        metrics TEXT NOT NULL,
                        recorded_at REAL
                    );
                    CREATE TABLE IF NOT EXISTS recommendations (
                        signature TEXT PRIMARY KEY,
                        params TEXT NOT NULL,
                        updated_at REAL
                    );
                    CREATE INDEX IF NOT EXISTS idx_history_sig
                        ON performance_history(signature);
                    """
                )
                conn.commit()
            finally:
                conn.close()

    def save_hardware(self, signature: str, profile: dict[str, Any]) -> None:
        self._upsert("hardware_profiles", "profile", signature, profile)

    def get_hardware(self, signature: str) -> dict[str, Any] | None:
        return self._get("hardware_profiles", "profile", signature)

    def save_benchmark(self, signature: str, result: dict[str, Any]) -> None:
        self._upsert("benchmarks", "result", signature, result)

    def get_benchmark(self, signature: str) -> dict[str, Any] | None:
        return self._get("benchmarks", "result", signature)

    def save_recommendation(self, signature: str, params: dict[str, Any]) -> None:
        self._upsert("recommendations", "params", signature, params)

    def get_recommendation(self, signature: str) -> dict[str, Any] | None:
        return self._get("recommendations", "params", signature)

    def _upsert(self, table: str, col: str, signature: str, value: dict[str, Any]) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    f"INSERT OR REPLACE INTO {table} (signature, {col}, updated_at) "
                    f"VALUES (?, ?, ?)",
                    (signature, json.dumps(value, ensure_ascii=False), time.time()),
                )
                conn.commit()
            finally:
                conn.close()

    def _get(self, table: str, col: str, signature: str) -> dict[str, Any] | None:
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute(
                    f"SELECT {col} FROM {table} WHERE signature=?", (signature,)
                ).fetchone()
            finally:
                conn.close()
        if not row:
            return None
        try:
            return json.loads(row[0])
        except Exception:
            return None

    def record_run(self, signature: str, project_id: str, metrics: dict[str, Any]) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT INTO performance_history (signature, project_id, metrics, recorded_at) "
                    "VALUES (?, ?, ?, ?)",
                    (signature, project_id, json.dumps(metrics, ensure_ascii=False), time.time()),
                )
                conn.commit()
            finally:
                conn.close()

    def history(self, signature: str, *, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT metrics, recorded_at FROM performance_history "
                    "WHERE signature=? ORDER BY recorded_at DESC LIMIT ?",
                    (signature, limit),
                ).fetchall()
            finally:
                conn.close()
        out: list[dict[str, Any]] = []
        for metrics, at in rows:
            try:
                d = json.loads(metrics)
                d["recorded_at"] = at
                out.append(d)
            except Exception:
                continue
        return out


# ── Performance Optimizer ────────────────────────────────────────────


class PerformanceOptimizer:
    """Automatic hardware-aware performance controller (TZ #7)."""

    def __init__(
        self,
        *,
        app_dir: str | Path | None = None,
        db: PerformanceDB | None = None,
    ) -> None:
        self.app_dir = Path(app_dir) if app_dir else Path.cwd()
        self._lock = threading.RLock()
        self._profile: HardwareProfile | None = None
        self._benchmark: BenchmarkResult | None = None
        self._plan: ResourcePlan | None = None
        db_dir = os.getenv("VM_PERF_DIR") or str(self.app_dir / "data" / "performance")
        self.db = db or PerformanceDB(Path(db_dir) / "performance.db")

    # ── Bootstrap (§1–§3) ────────────────────────────────────────────

    def initialize(self, *, force_benchmark: bool = False, quick: bool = True) -> ResourcePlan:
        """Profile hardware, benchmark (or reuse cached), build the plan."""
        with self._lock:
            self._profile = get_hardware_profile()
            sig = self._profile.signature()
            self.db.save_hardware(sig, self._profile.to_dict())

            cached = None if force_benchmark else self.db.get_benchmark(sig)
            if cached is not None:
                self._benchmark = _benchmark_from_dict(cached)
            else:
                self._benchmark = run_benchmark(quick=quick)
                self.db.save_benchmark(sig, self._benchmark.to_dict())

            self._plan = self._build_plan()
            self.db.save_recommendation(sig, self._plan.to_dict())
            return self._plan

    def plan(self) -> ResourcePlan:
        with self._lock:
            if self._plan is None:
                return self.initialize()
            return self._plan

    # ── Tier selection (§3) ──────────────────────────────────────────

    def _select_tier(self, prof: HardwareProfile, bench: BenchmarkResult) -> str:
        cores = prof.cpu.logical_cores
        ram = prof.memory.total_gb
        vram = prof.gpu.vram_gb
        gpu = prof.gpu.available
        score = bench.overall_score

        # Weighted capability heuristic; no fixed user tuning.
        if gpu and vram >= 16 and cores >= 16 and ram >= 32 and score >= 120:
            return TIER_ULTRA
        if gpu and vram >= 8 and cores >= 8 and ram >= 16:
            return TIER_HIGH
        if cores >= 8 and ram >= 16:
            return TIER_BALANCED
        if cores >= 4 and ram >= 8:
            return TIER_LIGHT
        return TIER_MINIMAL

    # ── Dynamic Resource Manager (§4) ────────────────────────────────

    def _build_plan(self) -> ResourcePlan:
        prof = self._profile or get_hardware_profile()
        bench = self._benchmark or run_benchmark(quick=True)
        tier = self._select_tier(prof, bench)
        mode = _current_mode()

        cores = max(1, prof.cpu.logical_cores)
        phys = max(1, prof.cpu.physical_cores or cores)
        ram = max(1.0, prof.memory.total_gb)
        gpu = prof.gpu.available
        vram = prof.gpu.vram_gb

        # Mode multiplier — only affects parallelism/resource usage (§11).
        mode_mult = {
            MODE_MAX_QUALITY: 0.6,
            MODE_BALANCED: 1.0,
            MODE_MAX_PERFORMANCE: 1.35,
        }[mode]

        # Base concurrency from cores, respecting RAM headroom.
        ram_task_cap = max(1, int(ram / 2.0))  # ~2GB per heavy task
        base_concurrent = max(1, min(cores - 1, ram_task_cap))
        max_concurrent = max(1, round(base_concurrent * mode_mult))

        cpu_workers = max(1, min(cores, round(phys * mode_mult)))
        gpu_workers = (max(1, prof.gpu.device_count) if gpu else 0)

        # Chunk size scales with RAM & benchmark; smaller on weak hosts (§4).
        default_chunk = self._default_chunk_size(tier, ram, bench, mode_mult)

        ram_budget = ram * (_RAM_SOFT_LIMIT / 100.0)
        vram_budget = vram * (_VRAM_SOFT_LIMIT / 100.0) if gpu else 0.0

        plan = ResourcePlan(
            tier=tier,
            mode=mode,
            max_concurrent_tasks=max_concurrent,
            cpu_workers=cpu_workers,
            gpu_workers=gpu_workers,
            default_chunk_size=default_chunk,
            ram_budget_gb=ram_budget,
            vram_budget_gb=vram_budget,
            use_gpu=gpu,
        )
        plan.notes.append(f"tier={tier} mode={mode} score={bench.overall_score:.0f}")

        for stage in ALL_STAGES:
            plan.stages[stage] = self._plan_stage(
                stage, prof, bench, tier, mode_mult, default_chunk, max_concurrent
            )

        # Apply accumulated learning from prior runs (§8).
        self._apply_history(plan, prof.signature())
        return plan

    def _default_chunk_size(
        self, tier: str, ram: float, bench: BenchmarkResult, mult: float
    ) -> int:
        base = {
            TIER_ULTRA: 32,
            TIER_HIGH: 24,
            TIER_BALANCED: 16,
            TIER_LIGHT: 10,
            TIER_MINIMAL: 6,
        }[tier]
        if ram < 8:
            base = min(base, 8)
        # Faster hosts (higher score) can afford larger chunks.
        if bench.overall_score >= 150:
            base = int(base * 1.25)
        return max(3, int(base * mult))

    # ── Device selection per stage (§5) ──────────────────────────────

    def _plan_stage(
        self,
        stage: str,
        prof: HardwareProfile,
        bench: BenchmarkResult,
        tier: str,
        mult: float,
        default_chunk: int,
        max_concurrent: int,
    ) -> StageResourcePlan:
        gpu = prof.gpu.available
        cores = max(1, prof.cpu.logical_cores)
        ram = max(1.0, prof.memory.total_gb)

        device = self._device_for(stage, prof)

        # Worker counts per stage type.
        if stage == STAGE_WHISPER:
            workers = 1 if device == "cpu" else max(1, min(2, prof.gpu.device_count or 1))
        elif stage in _LLM_STAGES:
            workers = 1 if (not gpu) else 2
            workers = max(1, round(workers * mult))
        elif stage == STAGE_TTS:
            cap = 8 if ram >= 16 else 4
            workers = max(2, min(cap, round((cores // 2) * mult)))
        elif stage in (STAGE_CLEANER, STAGE_TIMING):
            workers = max(1, round((cores // 2) * mult))
        else:  # mix, export
            workers = max(1, round((cores // 3 or 1) * mult))

        workers = max(1, min(workers, cores))

        # Chunk size per stage — LLM stages stay smaller for latency.
        chunk = default_chunk
        if stage in _LLM_STAGES and device == "cpu":
            chunk = max(3, default_chunk // 2)

        in_flight_mult = 2 if stage in (STAGE_WHISPER, STAGE_TTS) + tuple(_LLM_STAGES) else 3
        max_in_flight = max(workers, workers * in_flight_mult)
        queue_size = max(4, max_in_flight * 2)
        if ram < 8:
            queue_size = max(4, min(queue_size, max_in_flight + 2))

        mem_limit_mb = int((prof.memory.total_gb * 1024) * 0.15 / max(1, workers))

        return StageResourcePlan(
            stage=stage,
            device=device,
            workers=workers,
            chunk_size=chunk,
            max_in_flight=max_in_flight,
            queue_size=queue_size,
            memory_limit_mb=max(128, mem_limit_mb),
        )

    def _device_for(self, stage: str, prof: HardwareProfile) -> str:
        """Automatic CPU/GPU/Hybrid selection per stage (§5)."""
        override = os.getenv(f"VM_DEVICE_{stage.upper()}")
        if override:
            v = override.strip().lower()
            if v in ("cpu", "gpu", "hybrid"):
                return v
        if not prof.gpu.available:
            return "cpu"
        if stage == STAGE_WHISPER:
            return "gpu"
        if stage == STAGE_TTS:
            # TTS benefits from GPU when VRAM is generous, else hybrid.
            return "gpu" if prof.gpu.vram_gb >= 6 else "hybrid"
        if stage == STAGE_MIX:
            return "hybrid" if prof.gpu.vram_gb >= 4 else "cpu"
        if stage in _LLM_STAGES:
            # LLM usually runs via external dispatcher; keep CPU placement here.
            return "cpu"
        return "cpu"

    # ── Self-learning (§8) ───────────────────────────────────────────

    def record_film(self, project_id: str, metrics: dict[str, Any]) -> None:
        """Persist averaged run metrics for future tuning (§8)."""
        prof = self._profile or get_hardware_profile()
        self.db.record_run(prof.signature(), project_id, metrics)

    def _apply_history(self, plan: ResourcePlan, signature: str) -> None:
        history = self.db.history(signature, limit=10)
        if not history:
            return
        # If prior runs consistently ran hot on RAM, shrink chunks proactively.
        ram_peaks = [h.get("avg_ram_percent", 0.0) for h in history if h.get("avg_ram_percent")]
        if ram_peaks and (sum(ram_peaks) / len(ram_peaks)) >= _RAM_SOFT_LIMIT:
            plan.default_chunk_size = max(3, int(plan.default_chunk_size * 0.8))
            plan.max_concurrent_tasks = max(1, plan.max_concurrent_tasks - 1)
            plan.notes.append("history: reduced chunk/concurrency (RAM pressure)")
        # If prior runs were fast and cool, allow slightly larger chunks.
        speeds = [h.get("processing_speed", 0.0) for h in history if h.get("processing_speed")]
        rams = [h.get("avg_ram_percent", 100.0) for h in history]
        if speeds and rams and (sum(rams) / len(rams)) < 60.0:
            plan.notes.append("history: host has headroom")

    # ── Bottleneck detection (§10) ───────────────────────────────────

    def detect_bottleneck(self, stage_durations: dict[str, float]) -> str | None:
        """Return the slowest stage given per-stage cumulative durations (§10)."""
        if not stage_durations:
            return None
        valid = {k: v for k, v in stage_durations.items() if v and v > 0}
        if len(valid) < 2:
            return None
        return max(valid.items(), key=lambda kv: kv[1])[0]

    def rebalance_for_bottleneck(
        self, plan: ResourcePlan, bottleneck: str
    ) -> ResourcePlan:
        """Give the slowest CPU-bound stage more workers (§10)."""
        sp = plan.stages.get(bottleneck)
        if sp is None:
            return plan
        prof = self._profile or get_hardware_profile()
        if sp.device in ("cpu", "hybrid") and sp.workers < prof.cpu.logical_cores:
            sp.workers = min(sp.workers + 1, prof.cpu.logical_cores)
            sp.max_in_flight = max(sp.workers, sp.workers * 2)
            plan.notes.append(f"bottleneck_boost: {bottleneck} -> {sp.workers} workers")
        return plan

    # ── Thermal / memory safeguards (§6, §7, §13) ────────────────────

    def evaluate_pressure(
        self,
        *,
        ram_percent: float = 0.0,
        vram_percent: float = 0.0,
        cpu_temp_c: float = 0.0,
        gpu_temp_c: float = 0.0,
    ) -> dict[str, Any]:
        """Decide load adjustments from live metrics (§6, §7). Never aborts."""
        actions: list[str] = []
        severity = "ok"

        if ram_percent >= _RAM_HARD_LIMIT or vram_percent >= 95.0:
            severity = "critical"
            actions += [
                "reduce_chunk_size", "reduce_concurrency",
                "increase_queue_wait", "free_temp_objects",
            ]
        elif ram_percent >= _RAM_SOFT_LIMIT or vram_percent >= _VRAM_SOFT_LIMIT:
            severity = "warning"
            actions += ["reduce_chunk_size", "free_temp_objects"]

        hottest = max(cpu_temp_c, gpu_temp_c)
        if hottest >= _TEMP_HARD_C:
            severity = "critical"
            actions += ["reduce_concurrency", "throttle_gpu"]
        elif hottest >= _TEMP_SOFT_C:
            if severity == "ok":
                severity = "warning"
            actions.append("reduce_concurrency")

        return {
            "severity": severity,
            "actions": sorted(set(actions)),
            "ram_percent": round(ram_percent, 1),
            "vram_percent": round(vram_percent, 1),
            "cpu_temp_c": round(cpu_temp_c, 1),
            "gpu_temp_c": round(gpu_temp_c, 1),
        }

    def apply_pressure(self, plan: ResourcePlan, evaluation: dict[str, Any]) -> ResourcePlan:
        """Mutate the plan in response to pressure (§7) — reduce, never crash."""
        actions = set(evaluation.get("actions", ()))
        if "reduce_chunk_size" in actions:
            plan.default_chunk_size = max(2, int(plan.default_chunk_size * 0.7))
            for sp in plan.stages.values():
                sp.chunk_size = max(2, int(sp.chunk_size * 0.7))
        if "reduce_concurrency" in actions:
            plan.max_concurrent_tasks = max(1, plan.max_concurrent_tasks - 1)
            for sp in plan.stages.values():
                if sp.workers > 1:
                    sp.workers -= 1
        if "increase_queue_wait" in actions:
            for sp in plan.stages.values():
                sp.queue_size = min(512, int(sp.queue_size * 1.5) + 1)
        if evaluation.get("severity") != "ok":
            plan.notes.append(f"pressure[{evaluation.get('severity')}]: {sorted(actions)}")
        return plan

    def predict_pressure(self, samples: list[dict[str, float]]) -> dict[str, Any]:
        """Forecast upcoming RAM/thermal pressure from a trend (§13)."""
        if len(samples) < 3:
            return {"predicted": False, "reason": "insufficient_samples"}
        rams = [s.get("ram_percent", 0.0) for s in samples[-5:]]
        slope = (rams[-1] - rams[0]) / max(1, len(rams) - 1)
        projected = rams[-1] + slope * 3  # ~3 samples ahead
        predicted = projected >= _RAM_SOFT_LIMIT and slope > 0
        return {
            "predicted": bool(predicted),
            "metric": "ram_percent",
            "current": round(rams[-1], 1),
            "slope": round(slope, 2),
            "projected": round(projected, 1),
            "recommend": ["reduce_chunk_size"] if predicted else [],
        }

    # ── Status ───────────────────────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": optimizer_enabled(),
                "mode": _current_mode(),
                "profile": self._profile.to_dict() if self._profile else None,
                "benchmark": self._benchmark.to_dict() if self._benchmark else None,
                "plan": self._plan.to_dict() if self._plan else None,
                "db_path": str(self.db.db_path),
            }


def _benchmark_from_dict(d: dict[str, Any]) -> BenchmarkResult:
    r = BenchmarkResult()
    r.scores = {k: float(v) for k, v in (d.get("scores") or {}).items()}
    r.durations_ms = {k: float(v) for k, v in (d.get("durations_ms") or {}).items()}
    r.total_ms = float(d.get("total_ms", 0.0))
    r.overall_score = float(d.get("overall_score", 0.0))
    r.gpu_used = bool(d.get("gpu_used", False))
    r.ran_at = float(d.get("ran_at", time.time()))
    return r


_optimizer: PerformanceOptimizer | None = None
_optimizer_lock = threading.Lock()


def get_performance_optimizer(*, app_dir: str | Path | None = None) -> PerformanceOptimizer:
    global _optimizer
    if _optimizer is None:
        with _optimizer_lock:
            if _optimizer is None:
                _optimizer = PerformanceOptimizer(app_dir=app_dir)
    return _optimizer


def reset_performance_optimizer() -> None:
    global _optimizer
    with _optimizer_lock:
        _optimizer = None
