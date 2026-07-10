"""Resource monitor for the AI Orchestrator (TZ #2 §4).

Samples CPU / RAM / GPU / VRAM every few seconds. All probes are best-effort:
missing ``psutil`` or ``torch`` degrade gracefully to safe defaults instead of
raising, so the orchestrator never crashes on a minimal host.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ResourceSample:
    cpu_percent: float = 0.0
    ram_percent: float = 0.0
    ram_used_gb: float = 0.0
    ram_total_gb: float = 0.0
    gpu_percent: float = 0.0
    vram_percent: float = 0.0
    vram_used_gb: float = 0.0
    vram_total_gb: float = 0.0
    gpu_available: bool = False
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
            "sampled_at": self.sampled_at,
        }


class ResourceMonitor:
    """Periodic, thread-safe resource sampler."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last: ResourceSample = ResourceSample()
        self._has_psutil: bool | None = None
        self._torch: Any = None
        self._torch_checked = False

    def _ensure_psutil(self) -> Any:
        if self._has_psutil is None:
            try:
                import psutil  # noqa: F401

                self._has_psutil = True
            except Exception:
                self._has_psutil = False
        if self._has_psutil:
            import psutil

            return psutil
        return None

    def _ensure_torch(self) -> Any:
        if not self._torch_checked:
            self._torch_checked = True
            try:
                import torch

                self._torch = torch if torch.cuda.is_available() else None
            except Exception:
                self._torch = None
        return self._torch

    def sample(self) -> ResourceSample:
        s = ResourceSample()
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

        with self._lock:
            self._last = s
        return s

    def last(self) -> ResourceSample:
        with self._lock:
            return self._last

    def memory_pressure(self, *, ram_limit: float = 90.0, vram_limit: float = 90.0) -> bool:
        s = self.last()
        if s.ram_percent >= ram_limit:
            return True
        if s.gpu_available and s.vram_percent >= vram_limit:
            return True
        return False
