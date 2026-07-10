"""Performance Monitor — live resource & pipeline telemetry (TZ #7 §12).

Continuously samples CPU / GPU / RAM / VRAM / temperatures plus pipeline state
(processing speed, current chunk size, queue depths, active agents) and exposes
them for the AI Orchestrator and the Performance Optimizer.

All probes are best-effort and degrade gracefully. This module only *reads*
telemetry and never changes dubbing behaviour directly — decisions are made by
the Performance Optimizer.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger("tubedub.performance_monitor")


def monitor_enabled() -> bool:
    return str(os.getenv("VM_PERF_MONITOR", "1")).strip().lower() not in (
        "0", "false", "no", "off",
    )


@dataclass
class PerformanceSample:
    cpu_percent: float = 0.0
    ram_percent: float = 0.0
    ram_used_gb: float = 0.0
    ram_total_gb: float = 0.0
    gpu_percent: float = 0.0
    vram_percent: float = 0.0
    vram_used_gb: float = 0.0
    vram_total_gb: float = 0.0
    gpu_available: bool = False
    cpu_temp_c: float = 0.0
    gpu_temp_c: float = 0.0
    processing_speed: float = 0.0  # items/sec
    current_chunk_size: int = 0
    queue_depth: int = 0
    active_agents: int = 0
    sampled_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cpu_percent": round(self.cpu_percent, 1),
            "ram_percent": round(self.ram_percent, 1),
            "ram_used_gb": round(self.ram_used_gb, 2),
            "ram_total_gb": round(self.ram_total_gb, 2),
            "gpu_percent": round(self.gpu_percent, 1),
            "vram_percent": round(self.vram_percent, 1),
            "vram_used_gb": round(self.vram_used_gb, 2),
            "vram_total_gb": round(self.vram_total_gb, 2),
            "gpu_available": self.gpu_available,
            "cpu_temp_c": round(self.cpu_temp_c, 1),
            "gpu_temp_c": round(self.gpu_temp_c, 1),
            "processing_speed": round(self.processing_speed, 3),
            "current_chunk_size": self.current_chunk_size,
            "queue_depth": self.queue_depth,
            "active_agents": self.active_agents,
            "sampled_at": self.sampled_at,
        }


class PerformanceMonitor:
    """Thread-safe periodic sampler for live performance telemetry (§12)."""

    _MAX_HISTORY = 120

    def __init__(self, *, interval_s: float = 2.0) -> None:
        self.interval_s = interval_s
        self._lock = threading.Lock()
        self._last = PerformanceSample()
        self._history: deque[PerformanceSample] = deque(maxlen=self._MAX_HISTORY)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._psutil: Any = None
        self._psutil_checked = False
        self._torch: Any = None
        self._torch_checked = False
        # Pipeline-state providers (set by whoever owns the pipeline).
        self._pipeline_provider: Callable[[], dict[str, Any]] | None = None
        # Running accumulators for processing speed.
        self._items_done = 0
        self._speed_window: deque[tuple[float, int]] = deque(maxlen=30)

    # ── Pipeline metric wiring (§12) ─────────────────────────────────

    def set_pipeline_provider(self, provider: Callable[[], dict[str, Any]] | None) -> None:
        """Register a callable returning {queue_depth, active_agents, chunk_size}."""
        with self._lock:
            self._pipeline_provider = provider

    def record_items(self, count: int) -> None:
        """Report completed work items for processing-speed calculation."""
        if count <= 0:
            return
        with self._lock:
            self._items_done += count
            self._speed_window.append((time.monotonic(), self._items_done))

    def _processing_speed(self) -> float:
        if len(self._speed_window) < 2:
            return 0.0
        (t0, n0), (t1, n1) = self._speed_window[0], self._speed_window[-1]
        dt = t1 - t0
        if dt <= 0:
            return 0.0
        return (n1 - n0) / dt

    # ── Sampling ─────────────────────────────────────────────────────

    def _ensure_psutil(self) -> Any:
        if not self._psutil_checked:
            self._psutil_checked = True
            try:
                import psutil

                self._psutil = psutil
            except Exception:
                self._psutil = None
        return self._psutil

    def _ensure_torch(self) -> Any:
        if not self._torch_checked:
            self._torch_checked = True
            try:
                import torch

                self._torch = torch if torch.cuda.is_available() else None
            except Exception:
                self._torch = None
        return self._torch

    def sample(self) -> PerformanceSample:
        s = PerformanceSample()
        psutil = self._ensure_psutil()
        if psutil is not None:
            try:
                s.cpu_percent = float(psutil.cpu_percent(interval=None))
                vm = psutil.virtual_memory()
                s.ram_percent = float(vm.percent)
                s.ram_total_gb = vm.total / (1024**3)
                s.ram_used_gb = (vm.total - vm.available) / (1024**3)
            except Exception:
                pass
            s.cpu_temp_c = self._cpu_temp(psutil)

        torch = self._ensure_torch()
        if torch is not None:
            try:
                s.gpu_available = True
                free, total = torch.cuda.mem_get_info()
                used = total - free
                s.vram_total_gb = total / (1024**3)
                s.vram_used_gb = used / (1024**3)
                s.vram_percent = (used / total * 100.0) if total else 0.0
                try:
                    s.gpu_percent = float(torch.cuda.utilization())
                except Exception:
                    s.gpu_percent = s.vram_percent
            except Exception:
                pass
        s.gpu_temp_c = self._gpu_temp()

        s.processing_speed = self._processing_speed()

        provider = None
        with self._lock:
            provider = self._pipeline_provider
        if provider is not None:
            try:
                pstate = provider() or {}
                s.queue_depth = int(pstate.get("queue_depth", 0))
                s.active_agents = int(pstate.get("active_agents", 0))
                s.current_chunk_size = int(pstate.get("chunk_size", 0))
            except Exception:
                pass

        with self._lock:
            self._last = s
            self._history.append(s)
        return s

    @staticmethod
    def _cpu_temp(psutil: Any) -> float:
        try:
            temps = psutil.sensors_temperatures()
            if not temps:
                return 0.0
            for key in ("coretemp", "k10temp", "cpu_thermal", "acpitz"):
                if key in temps and temps[key]:
                    return float(temps[key][0].current)
            # Fallback: first available sensor.
            for entries in temps.values():
                if entries:
                    return float(entries[0].current)
        except Exception:
            pass
        return 0.0

    def _gpu_temp(self) -> float:
        # Try NVML via pynvml, then nvidia-smi.
        try:
            import pynvml

            pynvml.nvmlInit()
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
            pynvml.nvmlShutdown()
            return float(temp)
        except Exception:
            pass
        try:
            import shutil
            import subprocess

            smi = shutil.which("nvidia-smi")
            if smi:
                out = subprocess.run(
                    [smi, "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=5,
                )
                line = (out.stdout or "").strip().splitlines()
                if line and line[0].strip().isdigit():
                    return float(line[0].strip())
        except Exception:
            pass
        return 0.0

    # ── Background loop ──────────────────────────────────────────────

    def start(self) -> None:
        if not monitor_enabled():
            return
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop, name="perf-monitor", daemon=True
            )
            self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.sample()
            except Exception as exc:  # noqa: BLE001
                logger.debug("[PERFMON] sample error: %s", exc)
            self._stop.wait(self.interval_s)

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            t = self._thread
        if t and t.is_alive():
            t.join(timeout=2.0)

    # ── Accessors ────────────────────────────────────────────────────

    def last(self) -> PerformanceSample:
        with self._lock:
            return self._last

    def history(self, *, limit: int = 60) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._history)[-limit:]
        return [s.to_dict() for s in items]

    def averages(self) -> dict[str, float]:
        """Averaged metrics for self-learning (§8)."""
        with self._lock:
            items = list(self._history)
        if not items:
            return {}
        n = len(items)
        return {
            "avg_cpu_percent": round(sum(s.cpu_percent for s in items) / n, 1),
            "avg_gpu_percent": round(sum(s.gpu_percent for s in items) / n, 1),
            "avg_ram_percent": round(sum(s.ram_percent for s in items) / n, 1),
            "avg_vram_percent": round(sum(s.vram_percent for s in items) / n, 1),
            "avg_queue_depth": round(sum(s.queue_depth for s in items) / n, 1),
            "avg_active_agents": round(sum(s.active_agents for s in items) / n, 1),
            "processing_speed": round(
                sum(s.processing_speed for s in items) / n, 3
            ),
            "samples": n,
        }

    def get_status(self) -> dict[str, Any]:
        return {
            "enabled": monitor_enabled(),
            "interval_s": self.interval_s,
            "running": bool(self._thread and self._thread.is_alive()),
            "last": self.last().to_dict(),
            "averages": self.averages(),
        }


_monitor: PerformanceMonitor | None = None
_monitor_lock = threading.Lock()


def get_performance_monitor() -> PerformanceMonitor:
    global _monitor
    if _monitor is None:
        with _monitor_lock:
            if _monitor is None:
                _monitor = PerformanceMonitor()
    return _monitor


def reset_performance_monitor() -> None:
    global _monitor
    with _monitor_lock:
        if _monitor is not None:
            _monitor.stop()
        _monitor = None
