"""P16.2 / P16.6 — Resource management + memory budget sampling."""

from __future__ import annotations

import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ResourceSnapshot:
    ts: float
    rss_mb: float | None = None
    vms_mb: float | None = None
    cpu_percent: float | None = None
    open_files: int | None = None
    threads: int | None = None
    temp_dir_mb: float | None = None
    temp_wav_count: int = 0
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _psutil():
    try:
        import psutil

        return psutil
    except ImportError:
        return None


def take_resource_snapshot(
    *,
    temp_dirs: list[Path] | None = None,
) -> ResourceSnapshot:
    snap = ResourceSnapshot(ts=time.time())
    psutil = _psutil()
    if psutil is not None:
        proc = psutil.Process(os.getpid())
        mem = proc.memory_info()
        snap.rss_mb = round(mem.rss / (1024 * 1024), 2)
        snap.vms_mb = round(mem.vms / (1024 * 1024), 2)
        snap.cpu_percent = proc.cpu_percent(interval=0.05)
        try:
            snap.open_files = len(proc.open_files())
        except Exception:
            snap.open_files = None
        snap.threads = proc.num_threads()
    dirs = list(temp_dirs or [])
    if not dirs:
        dirs = [Path(tempfile.gettempdir()) / "videomonster"]
    total = 0
    wavs = 0
    for d in dirs:
        if not d.exists():
            continue
        for p in d.rglob("*"):
            if p.is_file():
                try:
                    total += p.stat().st_size
                except OSError:
                    continue
                if p.suffix.lower() in {".wav", ".mp3", ".ogg"}:
                    wavs += 1
    snap.temp_dir_mb = round(total / (1024 * 1024), 2)
    snap.temp_wav_count = wavs
    return snap


def cleanup_temp_wavs(
    directories: list[Path],
    *,
    older_than_sec: float = 3600,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Remove orphaned temp audio files older than threshold."""
    now = time.time()
    removed: list[str] = []
    kept = 0
    freed = 0
    for root in directories:
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() not in {".wav", ".mp3", ".ogg", ".tmp"}:
                continue
            try:
                age = now - p.stat().st_mtime
                size = p.stat().st_size
            except OSError:
                continue
            if age < older_than_sec:
                kept += 1
                continue
            if dry_run:
                removed.append(str(p))
                freed += size
                continue
            try:
                p.unlink()
                removed.append(str(p))
                freed += size
            except OSError:
                kept += 1
    return {
        "removed": len(removed),
        "kept": kept,
        "freed_mb": round(freed / (1024 * 1024), 3),
        "paths": removed[:50],
    }


def assert_no_resource_leak(
    before: ResourceSnapshot,
    after: ResourceSnapshot,
    *,
    max_rss_growth_mb: float = 250.0,
    max_thread_growth: int = 32,
    max_temp_growth_mb: float = 500.0,
) -> list[str]:
    """Return list of leak warnings (empty = OK for CI thresholds)."""
    issues: list[str] = []
    if before.rss_mb is not None and after.rss_mb is not None:
        growth = after.rss_mb - before.rss_mb
        if growth > max_rss_growth_mb:
            issues.append(f"rss_growth={growth:.1f}MB > {max_rss_growth_mb}")
    if before.threads is not None and after.threads is not None:
        growth_t = after.threads - before.threads
        if growth_t > max_thread_growth:
            issues.append(f"thread_growth={growth_t} > {max_thread_growth}")
    if before.temp_dir_mb is not None and after.temp_dir_mb is not None:
        growth_tmp = after.temp_dir_mb - before.temp_dir_mb
        if growth_tmp > max_temp_growth_mb:
            issues.append(f"temp_growth={growth_tmp:.1f}MB > {max_temp_growth_mb}")
    return issues
