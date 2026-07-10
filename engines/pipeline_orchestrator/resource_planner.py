"""Dynamic resource planner for the Pipeline Orchestrator.

Single authoritative source for *how many* workers, *what* batch size, *how
large* queues and *how long* timeouts should be. Values are derived from:

* CPU core count and logical threads
* Available RAM (best-effort)
* GPU / CUDA presence (via :func:`engines.hardware_probe.probe_hardware`)
* Measured per-stage throughput (rolling window of recent samples)

No fixed constants for production tuning — only safety floors/ceilings.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from engines.hardware_probe import probe_hardware

# Canonical stage names for the full dubbing conveyor.
STAGE_WHISPER = "whisper"
STAGE_CLEANER = "cleaner"
STAGE_MARIAN = "marian"
STAGE_TRANSLATION = "translation"
STAGE_AI_ADAPTATION = "ai_adaptation"
STAGE_LLM = "llm_naturalize"
STAGE_TTS = "tts"
STAGE_TIMING = "timing"
STAGE_MIX = "mix"
STAGE_EXPORT = "export"

ALL_STAGES: tuple[str, ...] = (
    STAGE_WHISPER,
    STAGE_CLEANER,
    STAGE_MARIAN,
    STAGE_TRANSLATION,
    STAGE_AI_ADAPTATION,
    STAGE_LLM,
    STAGE_TTS,
    STAGE_TIMING,
    STAGE_MIX,
    STAGE_EXPORT,
)

# Stages that are typically I/O or LLM-bound (fewer workers, larger queues).
_IO_BOUND_STAGES = frozenset({STAGE_WHISPER, STAGE_AI_ADAPTATION, STAGE_LLM, STAGE_TTS})
# Stages that benefit from CPU parallelism.
_CPU_BOUND_STAGES = frozenset({STAGE_CLEANER, STAGE_MARIAN, STAGE_TRANSLATION, STAGE_TIMING})


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass
class ResourceSnapshot:
    """Point-in-time host capacity."""

    cpu_cores: int
    cpu_threads: int
    ram_gb: float
    gpu_available: bool
    cuda_devices: int
    whisper_device: str
    measured_at: float = field(default_factory=time.monotonic)

    @property
    def is_cpu_only(self) -> bool:
        return not self.gpu_available


@dataclass
class StagePlan:
    """Recommended scheduling parameters for one pipeline stage."""

    stage: str
    workers: int
    batch_size: int
    max_in_flight: int
    queue_size: int
    timeout_scale: float
    bottleneck: bool = False
    notes: list[str] = field(default_factory=list)


@dataclass
class _ThroughputSample:
    stage: str
    items_per_sec: float
    at: float


class ResourcePlanner:
    """Derives dynamic worker/batch/queue/timeout plans from host + measurements."""

    _WINDOW_S = 120.0
    _MAX_SAMPLES = 64

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._samples: dict[str, deque[_ThroughputSample]] = {
            s: deque(maxlen=self._MAX_SAMPLES) for s in ALL_STAGES
        }
        self._snapshot: ResourceSnapshot | None = None
        self._snapshot_at = 0.0
        self._snapshot_ttl = 60.0

    def snapshot(self, *, force: bool = False) -> ResourceSnapshot:
        now = time.monotonic()
        if (
            not force
            and self._snapshot is not None
            and (now - self._snapshot_at) < self._snapshot_ttl
        ):
            return self._snapshot

        hw = probe_hardware(force=force)
        cores = max(1, os.cpu_count() or 1)
        threads = max(cores, _env_int("VM_PIPELINE_THREADS", cores))
        ram_gb = self._probe_ram_gb()
        gpu = bool(hw.get("cuda_available") or hw.get("torch_cuda"))
        cuda_n = int(hw.get("cuda_devices") or 0)

        self._snapshot = ResourceSnapshot(
            cpu_cores=cores,
            cpu_threads=threads,
            ram_gb=ram_gb,
            gpu_available=gpu,
            cuda_devices=cuda_n,
            whisper_device=str(hw.get("whisper_device") or "cpu"),
        )
        self._snapshot_at = now
        return self._snapshot

    @staticmethod
    def _probe_ram_gb() -> float:
        try:
            import psutil

            return round(psutil.virtual_memory().total / (1024**3), 1)
        except Exception:
            pass
        # Windows fallback without psutil
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return round(stat.ullTotalPhys / (1024**3), 1)
        except Exception:
            return 8.0

    def record_throughput(self, stage: str, items_per_sec: float) -> None:
        """Record a measured throughput sample (items/sec) for balancing."""
        if items_per_sec <= 0 or stage not in self._samples:
            return
        with self._lock:
            self._samples[stage].append(
                _ThroughputSample(stage=stage, items_per_sec=items_per_sec, at=time.monotonic())
            )

    def record_stage_duration(
        self, stage: str, *, item_count: int, duration_s: float
    ) -> None:
        if duration_s <= 0 or item_count <= 0:
            return
        self.record_throughput(stage, item_count / duration_s)

    def _recent_throughput(self, stage: str) -> float | None:
        now = time.monotonic()
        with self._lock:
            samples = [
                s.items_per_sec
                for s in self._samples.get(stage, ())
                if (now - s.at) <= self._WINDOW_S
            ]
        if not samples:
            return None
        return sum(samples) / len(samples)

    def _bottleneck_stage(self) -> str | None:
        """Stage with lowest recent throughput (slowest belt section)."""
        rates: list[tuple[float, str]] = []
        for stage in ALL_STAGES:
            t = self._recent_throughput(stage)
            if t is not None and t > 0:
                rates.append((t, stage))
        if len(rates) < 2:
            return None
        rates.sort(key=lambda x: x[0])
        return rates[0][1]

    def plan_stage(self, stage: str, *, segment_count: int = 0) -> StagePlan:
        snap = self.snapshot()
        bottleneck = self._bottleneck_stage()
        is_bn = bottleneck == stage
        notes: list[str] = []

        workers = self._workers_for(stage, snap, segment_count)
        batch = self._batch_for(stage, snap, segment_count)
        in_flight = self._in_flight_for(stage, snap, workers)
        qsize = self._queue_size_for(stage, snap, in_flight, segment_count)
        timeout_scale = self._timeout_scale_for(stage, snap)

        # Balancing: if this stage is the bottleneck, add workers (within caps).
        if is_bn and stage in _CPU_BOUND_STAGES:
            boosted = min(workers + max(1, snap.cpu_cores // 4), self._worker_cap(stage, snap))
            if boosted > workers:
                notes.append(f"bottleneck_boost workers {workers}->{boosted}")
                workers = boosted
                in_flight = self._in_flight_for(stage, snap, workers)

        # If downstream is idle (high throughput) and we're upstream, feed faster.
        if not is_bn and bottleneck:
            bn_rate = self._recent_throughput(bottleneck) or 0
            my_rate = self._recent_throughput(stage) or 0
            if my_rate > 0 and bn_rate > 0 and my_rate < bn_rate * 0.5:
                notes.append(f"feeding_bottleneck={bottleneck}")

        return StagePlan(
            stage=stage,
            workers=workers,
            batch_size=batch,
            max_in_flight=in_flight,
            queue_size=qsize,
            timeout_scale=timeout_scale,
            bottleneck=is_bn,
            notes=notes,
        )

    def plan_all(self, *, segment_count: int = 0) -> dict[str, StagePlan]:
        return {s: self.plan_stage(s, segment_count=segment_count) for s in ALL_STAGES}

    def _worker_cap(self, stage: str, snap: ResourceSnapshot) -> int:
        if stage == STAGE_WHISPER:
            return 1 if snap.whisper_device == "cpu" else min(2, snap.cuda_devices or 1)
        if stage == STAGE_AI_ADAPTATION:
            # LLM concurrency is memory-heavy on CPU
            if snap.is_cpu_only:
                return max(1, min(2, snap.cpu_cores // 4))
            return max(1, min(4, snap.cpu_cores // 2))
        if stage == STAGE_TTS:
            cap = max(2, snap.cpu_threads // 2)
            return min(cap, 8 if snap.ram_gb >= 16 else 4)
        if stage in _CPU_BOUND_STAGES:
            return max(1, min(snap.cpu_threads, snap.cpu_cores))
        return max(1, snap.cpu_threads // 2)

    def _workers_for(self, stage: str, snap: ResourceSnapshot, segment_count: int) -> int:
        cap = self._worker_cap(stage, snap)
        if segment_count > 0:
            cap = min(cap, max(1, segment_count))
        if stage == STAGE_MARIAN:
            # TZ §5: Marian workers = CPU cores - 1 (overridable via VM_MARIAN_WORKERS).
            base = max(1, _env_int("VM_MARIAN_WORKERS", max(1, snap.cpu_cores - 1)))
            return max(1, min(base, cap))
        if stage in (STAGE_AI_ADAPTATION, STAGE_LLM):
            base = max(1, _env_int("VM_LLM_WORKERS", 2 if not snap.is_cpu_only else 1))
            return max(1, min(base, cap))
        base = 1
        if stage in _CPU_BOUND_STAGES:
            base = max(1, snap.cpu_cores // 2)
        elif stage == STAGE_TTS:
            base = max(2, min(4, snap.cpu_threads // 2))
        return max(1, min(base, cap))

    def _batch_for(self, stage: str, snap: ResourceSnapshot, segment_count: int) -> int:
        if stage not in (STAGE_MARIAN, STAGE_TRANSLATION, STAGE_AI_ADAPTATION, STAGE_LLM):
            return 1
        # TZ §2: batch 3–6 sentences when RAM allows.
        if snap.ram_gb < 8:
            return 1
        if segment_count <= 3:
            return 1
        if stage in (STAGE_AI_ADAPTATION, STAGE_LLM) and snap.is_cpu_only:
            return min(2, max(1, segment_count // 10))
        return min(6, max(3, segment_count // 6))

    def _in_flight_for(self, stage: str, snap: ResourceSnapshot, workers: int) -> int:
        multiplier = 2 if stage in _IO_BOUND_STAGES else 3
        cap = workers * multiplier
        if snap.ram_gb < 8:
            cap = min(cap, workers + 1)
        return max(workers, cap)

    def _queue_size_for(
        self,
        stage: str,
        snap: ResourceSnapshot,
        in_flight: int,
        segment_count: int,
    ) -> int:
        base = max(in_flight * 2, 8)
        if segment_count > 0:
            base = min(base, max(in_flight, segment_count))
        if snap.ram_gb >= 16:
            base = min(base * 2, 256)
        return max(4, base)

    def _timeout_scale_for(self, stage: str, snap: ResourceSnapshot) -> float:
        if not snap.is_cpu_only:
            return 1.0
        if stage == STAGE_AI_ADAPTATION:
            return _env_float("VM_PIPELINE_LLM_TIMEOUT_SCALE_CPU", 2.5)
        if stage == STAGE_WHISPER:
            return _env_float("VM_PIPELINE_WHISPER_TIMEOUT_SCALE_CPU", 1.5)
        return _env_float("VM_PIPELINE_TIMEOUT_SCALE_CPU", 1.25)

    def to_dict(self, *, segment_count: int = 0) -> dict[str, Any]:
        snap = self.snapshot()
        plans = self.plan_all(segment_count=segment_count)
        return {
            "snapshot": {
                "cpu_cores": snap.cpu_cores,
                "cpu_threads": snap.cpu_threads,
                "ram_gb": snap.ram_gb,
                "gpu_available": snap.gpu_available,
                "cuda_devices": snap.cuda_devices,
                "whisper_device": snap.whisper_device,
                "cpu_only": snap.is_cpu_only,
            },
            "bottleneck": self._bottleneck_stage(),
            "stages": {
                name: {
                    "workers": p.workers,
                    "batch_size": p.batch_size,
                    "max_in_flight": p.max_in_flight,
                    "queue_size": p.queue_size,
                    "timeout_scale": p.timeout_scale,
                    "bottleneck": p.bottleneck,
                    "notes": p.notes,
                }
                for name, p in plans.items()
            },
        }


_global_planner: ResourcePlanner | None = None
_global_lock = threading.Lock()


def get_planner() -> ResourcePlanner:
    global _global_planner
    with _global_lock:
        if _global_planner is None:
            _global_planner = ResourcePlanner()
        return _global_planner
