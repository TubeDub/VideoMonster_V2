"""P809 Resource Manager + P810 Performance Manager."""

from __future__ import annotations

import time
from typing import Any


class ResourceManager:
    """Centralized CPU/GPU/RAM/VRAM/Disk/Temp/Threads façade."""

    def snapshot(self) -> dict[str, Any]:
        try:
            from engines.production_hardening.resource_manager import take_resource_snapshot

            snap = take_resource_snapshot()
            if hasattr(snap, "to_dict"):
                data = snap.to_dict()
            elif isinstance(snap, dict):
                data = snap
            else:
                data = {
                    "ram_mb": getattr(snap, "rss_mb", None),
                    "threads": getattr(snap, "threads", None),
                    "temp_dir_mb": getattr(snap, "temp_dir_mb", None),
                    "cpu_pct": getattr(snap, "cpu_pct", None),
                }
            data.setdefault("gpu", None)
            data.setdefault("vram_mb", None)
            data.setdefault("disk_free_mb", None)
            return data
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def cleanup_temp(self) -> dict[str, Any]:
        try:
            from engines.production_hardening.resource_manager import cleanup_temp_wavs

            return cleanup_temp_wavs()
        except TypeError:
            try:
                from engines.production_hardening.resource_manager import cleanup_temp_wavs
                from pathlib import Path

                return cleanup_temp_wavs(Path("output"))
            except Exception as exc:
                return {"ok": False, "error": str(exc)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


class PerformanceManager:
    """P810 — stage timings, waits, bottlenecks, queues, GPU/CPU."""

    def __init__(self) -> None:
        self._stages: list[dict[str, Any]] = []

    def record_stage(
        self,
        name: str,
        *,
        elapsed_ms: float,
        wait_ms: float = 0.0,
        queue_depth: int = 0,
        cpu_pct: float | None = None,
        gpu_pct: float | None = None,
    ) -> None:
        self._stages.append(
            {
                "stage": name,
                "elapsed_ms": elapsed_ms,
                "wait_ms": wait_ms,
                "queue_depth": queue_depth,
                "cpu_pct": cpu_pct,
                "gpu_pct": gpu_pct,
                "ts": time.time(),
            }
        )

    def bottlenecks(self, *, threshold_ms: float = 5000.0) -> list[dict[str, Any]]:
        return [s for s in self._stages if float(s.get("elapsed_ms") or 0) >= threshold_ms]

    def report(self) -> dict[str, Any]:
        total = sum(float(s.get("elapsed_ms") or 0) for s in self._stages)
        wait = sum(float(s.get("wait_ms") or 0) for s in self._stages)
        return {
            "stages": list(self._stages),
            "total_ms": round(total, 2),
            "wait_ms": round(wait, 2),
            "bottlenecks": self.bottlenecks(),
            "count": len(self._stages),
        }
