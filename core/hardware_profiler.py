"""Hardware Profiler — automatic host capability detection (TZ #7 §1).

On first run, detects CPU / RAM / GPU / disk / OS characteristics so that the
Performance Optimizer can configure every component automatically. Every probe
is best-effort: missing optional dependencies (``psutil``, ``torch``) degrade
gracefully to safe defaults instead of raising.

No fixed performance settings are stored here — this module only *describes* the
hardware. Tuning decisions live in ``core/performance_optimizer.py``.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("tubedub.hardware_profiler")


@dataclass
class CPUInfo:
    model: str = ""
    physical_cores: int = 0
    logical_cores: int = 0
    frequency_mhz: float = 0.0
    arch: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "physical_cores": self.physical_cores,
            "logical_cores": self.logical_cores,
            "frequency_mhz": round(self.frequency_mhz, 0),
            "arch": self.arch,
        }


@dataclass
class MemoryInfo:
    total_gb: float = 0.0
    available_gb: float = 0.0
    speed_mhz: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_gb": round(self.total_gb, 2),
            "available_gb": round(self.available_gb, 2),
            "speed_mhz": round(self.speed_mhz, 0),
        }


@dataclass
class GPUInfo:
    model: str = ""
    vram_gb: float = 0.0
    available: bool = False
    cuda: bool = False
    metal: bool = False
    rocm: bool = False
    directml: bool = False
    device_count: int = 0

    @property
    def backend(self) -> str:
        if self.cuda:
            return "cuda"
        if self.rocm:
            return "rocm"
        if self.directml:
            return "directml"
        if self.metal:
            return "metal"
        return "cpu"

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "vram_gb": round(self.vram_gb, 2),
            "available": self.available,
            "cuda": self.cuda,
            "metal": self.metal,
            "rocm": self.rocm,
            "directml": self.directml,
            "device_count": self.device_count,
            "backend": self.backend,
        }


@dataclass
class DiskInfo:
    kind: str = "unknown"  # ssd | hdd | nvme | unknown
    free_gb: float = 0.0
    total_gb: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "free_gb": round(self.free_gb, 2),
            "total_gb": round(self.total_gb, 2),
        }


@dataclass
class HardwareProfile:
    """Complete host description (TZ #7 §1)."""

    cpu: CPUInfo = field(default_factory=CPUInfo)
    memory: MemoryInfo = field(default_factory=MemoryInfo)
    gpu: GPUInfo = field(default_factory=GPUInfo)
    disk: DiskInfo = field(default_factory=DiskInfo)
    os_name: str = ""
    os_version: str = ""
    platform_str: str = ""
    profiled_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cpu": self.cpu.to_dict(),
            "memory": self.memory.to_dict(),
            "gpu": self.gpu.to_dict(),
            "disk": self.disk.to_dict(),
            "os_name": self.os_name,
            "os_version": self.os_version,
            "platform": self.platform_str,
            "profiled_at": self.profiled_at,
        }

    def signature(self) -> str:
        """Stable identity for this machine (used as Performance DB key)."""
        return "|".join(
            [
                self.cpu.model or "cpu",
                str(self.cpu.logical_cores),
                f"{self.memory.total_gb:.0f}gb",
                self.gpu.model or self.gpu.backend,
                self.os_name,
            ]
        )


class HardwareProfiler:
    """Detects host hardware; caches the result for the process lifetime."""

    _CACHE_TTL = 300.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._profile: HardwareProfile | None = None
        self._profiled_at = 0.0
        self._psutil: Any = None
        self._psutil_checked = False

    # ── Public API ───────────────────────────────────────────────────

    def profile(self, *, force: bool = False) -> HardwareProfile:
        now = time.monotonic()
        with self._lock:
            if (
                not force
                and self._profile is not None
                and (now - self._profiled_at) < self._CACHE_TTL
            ):
                return self._profile
            p = HardwareProfile(
                cpu=self._detect_cpu(),
                memory=self._detect_memory(),
                gpu=self._detect_gpu(),
                disk=self._detect_disk(),
            )
            p.os_name = platform.system()
            p.os_version = platform.version()
            p.platform_str = platform.platform()
            self._profile = p
            self._profiled_at = now
            return p

    # ── Probes ───────────────────────────────────────────────────────

    def _ensure_psutil(self) -> Any:
        if not self._psutil_checked:
            self._psutil_checked = True
            try:
                import psutil

                self._psutil = psutil
            except Exception:
                self._psutil = None
        return self._psutil

    def _detect_cpu(self) -> CPUInfo:
        info = CPUInfo(arch=platform.machine())
        info.logical_cores = os.cpu_count() or 1
        info.physical_cores = info.logical_cores
        info.model = self._cpu_model()

        psutil = self._ensure_psutil()
        if psutil is not None:
            try:
                phys = psutil.cpu_count(logical=False)
                if phys:
                    info.physical_cores = int(phys)
                logical = psutil.cpu_count(logical=True)
                if logical:
                    info.logical_cores = int(logical)
            except Exception:
                pass
            try:
                freq = psutil.cpu_freq()
                if freq and freq.max:
                    info.frequency_mhz = float(freq.max)
                elif freq and freq.current:
                    info.frequency_mhz = float(freq.current)
            except Exception:
                pass
        return info

    @staticmethod
    def _cpu_model() -> str:
        proc = platform.processor()
        if proc:
            return proc
        system = platform.system()
        try:
            if system == "Windows":
                return os.environ.get("PROCESSOR_IDENTIFIER", "") or platform.machine()
            if system == "Darwin":
                out = subprocess.run(
                    ["sysctl", "-n", "machdep.cpu.brand_string"],
                    capture_output=True, text=True, timeout=5,
                )
                return out.stdout.strip() or platform.machine()
            if system == "Linux":
                try:
                    with open("/proc/cpuinfo", encoding="utf-8", errors="ignore") as fh:
                        for line in fh:
                            if line.lower().startswith("model name"):
                                return line.split(":", 1)[1].strip()
                except Exception:
                    pass
        except Exception:
            pass
        return platform.machine() or "unknown"

    def _detect_memory(self) -> MemoryInfo:
        info = MemoryInfo()
        psutil = self._ensure_psutil()
        if psutil is not None:
            try:
                vm = psutil.virtual_memory()
                info.total_gb = vm.total / (1024**3)
                info.available_gb = vm.available / (1024**3)
            except Exception:
                pass
        if info.total_gb <= 0:
            info.total_gb = self._windows_total_ram_gb()
            info.available_gb = info.total_gb
        info.speed_mhz = self._memory_speed_mhz()
        return info

    @staticmethod
    def _windows_total_ram_gb() -> float:
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
            return stat.ullTotalPhys / (1024**3)
        except Exception:
            return 8.0

    @staticmethod
    def _memory_speed_mhz() -> float:
        """Best-effort RAM clock speed (Windows WMIC / Linux dmidecode)."""
        system = platform.system()
        try:
            if system == "Windows":
                wmic = shutil.which("wmic")
                if wmic:
                    out = subprocess.run(
                        [wmic, "memorychip", "get", "Speed"],
                        capture_output=True, text=True, timeout=8,
                    )
                    speeds = [
                        int(x) for x in out.stdout.split()
                        if x.strip().isdigit()
                    ]
                    if speeds:
                        return float(max(speeds))
        except Exception:
            pass
        return 0.0

    def _detect_gpu(self) -> GPUInfo:
        info = GPUInfo()

        # CUDA / VRAM / model via torch.
        try:
            import torch

            if torch.cuda.is_available():
                info.available = True
                info.cuda = True
                info.device_count = torch.cuda.device_count()
                try:
                    info.model = torch.cuda.get_device_name(0)
                except Exception:
                    pass
                try:
                    props = torch.cuda.get_device_properties(0)
                    info.vram_gb = props.total_memory / (1024**3)
                except Exception:
                    pass
            # Apple Metal (MPS).
            elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                info.available = True
                info.metal = True
                info.model = "Apple Silicon GPU (Metal)"
                info.device_count = 1
        except Exception:
            pass

        # ctranslate2 CUDA fallback when torch is absent.
        if not info.available:
            try:
                import ctranslate2

                n = int(ctranslate2.get_cuda_device_count())
                if n > 0:
                    info.available = True
                    info.cuda = True
                    info.device_count = n
            except Exception:
                pass

        # ROCm detection (AMD).
        if not info.available and self._has_rocm():
            info.available = True
            info.rocm = True
            info.model = "AMD GPU (ROCm)"
            info.device_count = 1

        # DirectML (Windows) — optional torch-directml / DXGI present.
        if not info.available and self._has_directml():
            info.available = True
            info.directml = True
            info.model = info.model or "GPU (DirectML)"
            info.device_count = max(info.device_count, 1)

        # nvidia-smi fallback for model/VRAM without torch.
        if info.cuda and (not info.model or info.vram_gb <= 0):
            self._augment_from_nvidia_smi(info)

        return info

    @staticmethod
    def _has_rocm() -> bool:
        if shutil.which("rocminfo") or shutil.which("rocm-smi"):
            return True
        return os.path.exists("/opt/rocm")

    @staticmethod
    def _has_directml() -> bool:
        if platform.system().lower() != "windows":
            return False
        try:
            import torch_directml  # type: ignore

            return int(torch_directml.device_count()) > 0
        except Exception:
            pass
        return bool(shutil.which("dxdiag"))

    @staticmethod
    def _augment_from_nvidia_smi(info: GPUInfo) -> None:
        smi = shutil.which("nvidia-smi")
        if not smi:
            return
        try:
            out = subprocess.run(
                [smi, "--query-gpu=name,memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=8,
            )
            line = (out.stdout or "").strip().splitlines()
            if line:
                parts = [p.strip() for p in line[0].split(",")]
                if parts and not info.model:
                    info.model = parts[0]
                if len(parts) > 1 and info.vram_gb <= 0:
                    try:
                        info.vram_gb = float(parts[1]) / 1024.0
                    except ValueError:
                        pass
        except Exception:
            pass

    def _detect_disk(self) -> DiskInfo:
        info = DiskInfo()
        path = os.getcwd()
        try:
            usage = shutil.disk_usage(path)
            info.free_gb = usage.free / (1024**3)
            info.total_gb = usage.total / (1024**3)
        except Exception:
            pass
        info.kind = self._disk_kind(path)
        return info

    def _disk_kind(self, path: str) -> str:
        system = platform.system()
        try:
            if system == "Linux":
                return self._linux_disk_kind(path)
            if system == "Windows":
                return self._windows_disk_kind()
        except Exception:
            pass
        return "unknown"

    @staticmethod
    def _linux_disk_kind(path: str) -> str:
        try:
            st = os.stat(path)
            major = os.major(st.st_dev)
            minor = os.minor(st.st_dev)  # noqa: F841
            # Walk up /sys/block to find rotational flag / nvme naming.
            for dev in os.listdir("/sys/block"):
                if dev.startswith("nvme"):
                    return "nvme"
            rot_path = "/sys/block"
            for dev in os.listdir(rot_path):
                rot_file = os.path.join(rot_path, dev, "queue", "rotational")
                if os.path.exists(rot_file):
                    with open(rot_file, encoding="utf-8") as fh:
                        if fh.read().strip() == "0":
                            return "ssd"
                        return "hdd"
        except Exception:
            pass
        return "unknown"

    @staticmethod
    def _windows_disk_kind() -> str:
        try:
            ps = shutil.which("powershell")
            if not ps:
                return "unknown"
            out = subprocess.run(
                [ps, "-NoProfile", "-Command",
                 "Get-PhysicalDisk | Select-Object -ExpandProperty MediaType"],
                capture_output=True, text=True, timeout=10,
            )
            text = (out.stdout or "").lower()
            if "nvme" in text:
                return "nvme"
            if "ssd" in text:
                return "ssd"
            if "hdd" in text:
                return "hdd"
        except Exception:
            pass
        return "unknown"


_profiler: HardwareProfiler | None = None
_profiler_lock = threading.Lock()


def get_hardware_profiler() -> HardwareProfiler:
    global _profiler
    if _profiler is None:
        with _profiler_lock:
            if _profiler is None:
                _profiler = HardwareProfiler()
    return _profiler


def get_hardware_profile(*, force: bool = False) -> HardwareProfile:
    return get_hardware_profiler().profile(force=force)
