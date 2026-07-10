"""Benchmark Engine — short automatic capability test (TZ #7 §2).

Runs a fast (<60s total) micro-benchmark to measure the relative speed of the
host across the operations that matter for dubbing:

    whisper · llm · tts · disk write · disk read · audio processing · mix

The benchmark is intentionally lightweight and *self-contained*: it never calls
the real Whisper/LLM/TTS engines (that would be slow and require models).
Instead it exercises representative CPU / memory / disk workloads and derives a
normalized score per category. Scores feed the Performance Optimizer's tier
selection and dynamic resource plan — they do not affect dubbing quality.
"""

from __future__ import annotations

import logging
import math
import os
import struct
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("tubedub.benchmark")

# Global safety budget — the whole benchmark must never exceed this (§2).
_TOTAL_BUDGET_S = 60.0


def benchmark_enabled() -> bool:
    return str(os.getenv("VM_BENCHMARK", "1")).strip().lower() not in (
        "0", "false", "no", "off",
    )


@dataclass
class BenchmarkResult:
    """Per-category timings + derived scores (higher score = faster)."""

    scores: dict[str, float] = field(default_factory=dict)
    durations_ms: dict[str, float] = field(default_factory=dict)
    total_ms: float = 0.0
    overall_score: float = 0.0
    gpu_used: bool = False
    ran_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scores": {k: round(v, 1) for k, v in self.scores.items()},
            "durations_ms": {k: round(v, 1) for k, v in self.durations_ms.items()},
            "total_ms": round(self.total_ms, 1),
            "overall_score": round(self.overall_score, 1),
            "gpu_used": self.gpu_used,
            "ran_at": self.ran_at,
        }


class BenchmarkEngine:
    """Runs the sub-60s benchmark and produces normalized scores."""

    # Reference times (ms) for a 100-point score. Faster host → higher score.
    _REFERENCE_MS = {
        "whisper": 400.0,
        "llm": 300.0,
        "tts": 250.0,
        "disk_write": 150.0,
        "disk_read": 120.0,
        "audio": 200.0,
        "mix": 180.0,
    }

    def __init__(self, *, budget_s: float = _TOTAL_BUDGET_S) -> None:
        self.budget_s = budget_s
        self._lock = threading.Lock()

    def run(self, *, quick: bool = False) -> BenchmarkResult:
        result = BenchmarkResult()
        started = time.perf_counter()
        scale = 0.35 if quick else 1.0

        tasks: list[tuple[str, Callable[[float], None]]] = [
            ("whisper", self._bench_whisper),
            ("llm", self._bench_llm),
            ("tts", self._bench_tts),
            ("disk_write", self._bench_disk_write),
            ("disk_read", self._bench_disk_read),
            ("audio", self._bench_audio),
            ("mix", self._bench_mix),
        ]

        result.gpu_used = self._gpu_present()

        for name, fn in tasks:
            elapsed_total = time.perf_counter() - started
            if elapsed_total >= self.budget_s * 0.95:
                logger.info("[BENCH] budget exhausted, skipping %s", name)
                result.durations_ms[name] = self._REFERENCE_MS[name]
                result.scores[name] = 100.0
                continue
            remaining = self.budget_s - elapsed_total
            try:
                t0 = time.perf_counter()
                fn(min(remaining, 6.0) * scale)
                dt = (time.perf_counter() - t0) * 1000.0
            except Exception as exc:  # noqa: BLE001
                logger.warning("[BENCH] %s failed: %s", name, exc)
                dt = self._REFERENCE_MS[name]
            result.durations_ms[name] = dt
            result.scores[name] = self._score(name, dt)

        result.total_ms = (time.perf_counter() - started) * 1000.0
        if result.scores:
            result.overall_score = sum(result.scores.values()) / len(result.scores)
        return result

    # ── Scoring ──────────────────────────────────────────────────────

    def _score(self, name: str, duration_ms: float) -> float:
        ref = self._REFERENCE_MS.get(name, 300.0)
        if duration_ms <= 0:
            return 100.0
        # Linear-ish: at reference time → 100; twice as slow → 50.
        return max(1.0, min(1000.0, ref / duration_ms * 100.0))

    @staticmethod
    def _gpu_present() -> bool:
        try:
            import torch

            if torch.cuda.is_available():
                return True
            mps = getattr(torch.backends, "mps", None)
            return bool(mps and mps.is_available())
        except Exception:
            return False

    # ── Micro-workloads (proxies, not real engines) ──────────────────

    @staticmethod
    def _cpu_grind(iterations: int) -> float:
        acc = 0.0
        for i in range(iterations):
            acc += math.sqrt((i % 977) + 1.0) * math.sin(i * 0.001)
        return acc

    def _bench_whisper(self, budget_s: float) -> None:
        # Whisper is FP-heavy transcription — proxy with float math.
        iters = min(600_000, int(300_000 * max(0.2, budget_s)))
        self._cpu_grind(iters)

    def _bench_llm(self, budget_s: float) -> None:
        # LLM is token generation — proxy with string/dict churn.
        n = min(120_000, int(60_000 * max(0.2, budget_s)))
        buf: dict[int, str] = {}
        for i in range(n):
            buf[i % 512] = f"tok{i}"
        _ = "".join(buf.values())

    def _bench_tts(self, budget_s: float) -> None:
        # TTS synthesises waveforms — proxy with sine sample generation.
        n = min(400_000, int(200_000 * max(0.2, budget_s)))
        total = 0.0
        for i in range(n):
            total += math.sin(2.0 * math.pi * 220.0 * (i / 22050.0))

    def _bench_disk_write(self, budget_s: float) -> None:
        size_mb = 16
        payload = os.urandom(1024 * 1024)
        tmp = Path(tempfile.gettempdir()) / f"vm_bench_{os.getpid()}.tmp"
        try:
            with open(tmp, "wb") as fh:
                for _ in range(size_mb):
                    fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
        finally:
            self._bench_read_path = tmp  # reused by read bench

    def _bench_disk_read(self, budget_s: float) -> None:
        tmp = getattr(self, "_bench_read_path", None)
        if not tmp or not Path(tmp).exists():
            # Create a small file to read.
            tmp = Path(tempfile.gettempdir()) / f"vm_bench_r_{os.getpid()}.tmp"
            with open(tmp, "wb") as fh:
                fh.write(os.urandom(8 * 1024 * 1024))
        try:
            with open(tmp, "rb") as fh:
                while fh.read(1024 * 1024):
                    pass
        finally:
            try:
                Path(tmp).unlink(missing_ok=True)
            except Exception:
                pass

    def _bench_audio(self, budget_s: float) -> None:
        # Audio DSP — proxy with a simple FIR-style convolution over samples.
        n = min(200_000, int(100_000 * max(0.2, budget_s)))
        kernel = [0.25, 0.5, 0.25]
        prev2 = prev1 = 0.0
        out = 0.0
        for i in range(n):
            x = math.sin(i * 0.01)
            out = kernel[0] * prev2 + kernel[1] * prev1 + kernel[2] * x
            prev2, prev1 = prev1, x

    def _bench_mix(self, budget_s: float) -> None:
        # Mixing sums/normalises multiple tracks — proxy with packed floats.
        n = min(150_000, int(80_000 * max(0.2, budget_s)))
        acc = bytearray()
        for i in range(n % 4096 + 1):
            acc += struct.pack("<f", math.sin(i * 0.02))
        total = 0.0
        for i in range(n):
            total += (i % 7) * 0.5 - (i % 3) * 0.3


_engine: BenchmarkEngine | None = None
_engine_lock = threading.Lock()


def get_benchmark_engine() -> BenchmarkEngine:
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = BenchmarkEngine()
    return _engine


def run_benchmark(*, quick: bool = False) -> BenchmarkResult:
    return get_benchmark_engine().run(quick=quick)
