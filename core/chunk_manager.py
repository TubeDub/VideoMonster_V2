"""Adaptive Chunk Manager (TZ #4 §4–§5).

Determines how many segments belong in each processing chunk based on live
system resources and measured stage throughput. Chunk size is never fixed —
it grows when the system is idle and shrinks under memory pressure.

Each chunk carries full state for pause/resume and crash recovery (§13–§14).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from core.resource_monitor import ResourceMonitor
from engines.pipeline_orchestrator.resource_planner import get_planner

logger = logging.getLogger("tubedub.chunk_manager")

# Bounds for adaptive chunk size (number of segments per chunk).
_MIN_CHUNK = 1
_MAX_CHUNK = 32
_DEFAULT_CHUNK = 4


class ChunkStatus(str, Enum):
    """Per-chunk lifecycle (TZ #4 §10)."""

    WAITING = "waiting"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SUSPENDED = "suspended"
    RETRY = "retry"


# Full conveyor stage order (TZ #4 §2). Whisper is optional upstream.
PIPELINE_STAGES: tuple[str, ...] = (
    "whisper",
    "cleaner",
    "translator",
    "review",
    "timing",
    "voice",
    "mix",
    "export",
)


@dataclass
class PipelineChunk:
    """One adaptive chunk flowing through the conveyor."""

    chunk_id: int
    segment_indices: list[int]
    source_segments: list[str] = field(default_factory=list)
    timing_map: list[dict[str, Any]] = field(default_factory=list)
    status: ChunkStatus = ChunkStatus.WAITING
    current_stage: str = ""
    completed_stages: list[str] = field(default_factory=list)
    attempts: int = 0
    error: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    stage_trace: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @property
    def segment_count(self) -> int:
        return len(self.segment_indices)

    def mark_stage(self, stage: str, *, ok: bool = True, error: str = "", busy_ms: float = 0.0) -> None:
        self.current_stage = stage
        self.updated_at = time.time()
        self.stage_trace.append(
            {"stage": stage, "ok": ok, "error": error, "busy_ms": round(busy_ms, 2)}
        )
        if ok and stage not in self.completed_stages:
            self.completed_stages.append(stage)
        if not ok:
            self.error = error
            self.status = ChunkStatus.FAILED

    def is_stage_done(self, stage: str) -> bool:
        return stage in self.completed_stages

    def next_pending_stage(self, stages: tuple[str, ...] = PIPELINE_STAGES) -> str | None:
        for s in stages:
            if s not in self.completed_stages:
                return s
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "segment_indices": self.segment_indices,
            "source_segments": self.source_segments,
            "timing_map": self.timing_map,
            "status": self.status.value,
            "current_stage": self.current_stage,
            "completed_stages": list(self.completed_stages),
            "attempts": self.attempts,
            "error": self.error,
            "payload": dict(self.payload),
            "stage_trace": list(self.stage_trace),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> PipelineChunk:
        return cls(
            chunk_id=int(raw.get("chunk_id", 0)),
            segment_indices=list(raw.get("segment_indices") or []),
            source_segments=list(raw.get("source_segments") or []),
            timing_map=list(raw.get("timing_map") or []),
            status=ChunkStatus(str(raw.get("status") or "waiting")),
            current_stage=str(raw.get("current_stage") or ""),
            completed_stages=list(raw.get("completed_stages") or []),
            attempts=int(raw.get("attempts") or 0),
            error=str(raw.get("error") or ""),
            payload=dict(raw.get("payload") or {}),
            stage_trace=list(raw.get("stage_trace") or []),
            created_at=float(raw.get("created_at") or time.time()),
            updated_at=float(raw.get("updated_at") or time.time()),
        )


class ChunkManager:
    """Adaptive chunk sizing + chunk registry + checkpoint persistence."""

    CHECKPOINT_VERSION = 1

    def __init__(
        self,
        *,
        monitor: ResourceMonitor | None = None,
        min_chunk: int = _MIN_CHUNK,
        max_chunk: int = _MAX_CHUNK,
        ram_limit: float = 90.0,
        vram_limit: float = 90.0,
    ) -> None:
        self.monitor = monitor or ResourceMonitor()
        self.min_chunk = max(1, min_chunk)
        self.max_chunk = max(self.min_chunk, max_chunk)
        self.ram_limit = ram_limit
        self.vram_limit = vram_limit
        self._chunk_size = _DEFAULT_CHUNK
        self._lock = threading.RLock()
        self._chunks: dict[int, PipelineChunk] = {}
        self._project_id = ""

    @property
    def chunk_size(self) -> int:
        with self._lock:
            return self._chunk_size

    # ── Adaptive sizing (§4–§5) ──────────────────────────────────────

    def compute_chunk_size(self) -> int:
        """Determine optimal segments-per-chunk from resources + throughput."""
        sample = self.monitor.sample()
        planner = get_planner()
        snap = planner.snapshot()

        size = self._chunk_size

        # Memory pressure → shrink (§5).
        if sample.ram_percent >= self.ram_limit:
            size = max(self.min_chunk, size - 2)
            logger.info("[CHUNK] RAM %.0f%% — shrink to %d", sample.ram_percent, size)
        elif sample.gpu_available and sample.vram_percent >= self.vram_limit:
            size = max(self.min_chunk, size - 1)
            logger.info("[CHUNK] VRAM %.0f%% — shrink to %d", sample.vram_percent, size)

        # System idle → grow (§5).
        elif sample.cpu_percent < 40 and sample.ram_percent < 70:
            bottleneck = planner._bottleneck_stage()  # noqa: SLF001
            if bottleneck is None:
                size = min(self.max_chunk, size + 1)
            else:
                # Bottleneck exists — don't grow, let planner throttle upstream.
                size = max(self.min_chunk, size)

        # CPU-only hosts: smaller chunks for heavy LLM stages.
        if snap.is_cpu_only and size > 6:
            size = min(size, 6)

        # GPU available: can handle slightly larger chunks for parallel TTS.
        if snap.gpu_available and sample.cpu_percent < 60:
            size = min(self.max_chunk, size + 0)  # no-op guard; growth handled above

        size = max(self.min_chunk, min(self.max_chunk, size))
        with self._lock:
            self._chunk_size = size
        return size

    def adjust_for_bottleneck(self, stage: str) -> None:
        """Shrink chunks when a stage is the belt bottleneck (§9)."""
        with self._lock:
            self._chunk_size = max(self.min_chunk, self._chunk_size - 1)
        logger.info("[CHUNK] bottleneck at %s — size now %d", stage, self._chunk_size)

    # ── Chunk creation (§4) ───────────────────────────────────────────

    def split_segments(
        self,
        source_segments: list[str],
        timing_map: list[dict[str, Any]],
        *,
        project_id: str = "",
        chunk_size: int | None = None,
    ) -> list[PipelineChunk]:
        """Group segments into adaptive chunks preserving index order."""
        if len(source_segments) != len(timing_map):
            raise ValueError(
                f"segment/timing mismatch: {len(source_segments)} vs {len(timing_map)}"
            )
        self._project_id = project_id
        size = chunk_size or self.compute_chunk_size()
        chunks: list[PipelineChunk] = []
        chunk_id = 0
        i = 0
        n = len(source_segments)
        while i < n:
            end = min(i + size, n)
            indices = list(range(i, end))
            chunk = PipelineChunk(
                chunk_id=chunk_id,
                segment_indices=indices,
                source_segments=[source_segments[j] for j in indices],
                timing_map=[timing_map[j] for j in indices],
                status=ChunkStatus.WAITING,
            )
            chunks.append(chunk)
            chunk_id += 1
            i = end
            # Re-evaluate size for next chunk (adaptive mid-run).
            if i < n:
                size = self.compute_chunk_size()

        with self._lock:
            self._chunks = {c.chunk_id: c for c in chunks}
        logger.info(
            "[CHUNK] split %d segments → %d chunks (size≈%d) project=%s",
            n,
            len(chunks),
            size,
            project_id[:8] if project_id else "",
        )
        return chunks

    # ── Registry + status (§10) ────────────────────────────────────────

    def get(self, chunk_id: int) -> PipelineChunk | None:
        with self._lock:
            return self._chunks.get(chunk_id)

    def all_chunks(self) -> list[PipelineChunk]:
        with self._lock:
            return sorted(self._chunks.values(), key=lambda c: c.chunk_id)

    def update_status(self, chunk_id: int, status: ChunkStatus) -> None:
        with self._lock:
            c = self._chunks.get(chunk_id)
            if c:
                c.status = status
                c.updated_at = time.time()

    def status_summary(self) -> dict[str, int]:
        counts: dict[str, int] = {s.value: 0 for s in ChunkStatus}
        with self._lock:
            for c in self._chunks.values():
                counts[c.status.value] = counts.get(c.status.value, 0) + 1
        return counts

    # ── Merge results preserving order (§11) ───────────────────────────

    def merge_results(self) -> tuple[list[str], list[dict[str, Any]]]:
        """Flatten completed chunks back into ordered segment lists."""
        segments: list[str | None] = [None] * self._total_segments()
        timing: list[dict[str, Any] | None] = [None] * self._total_segments()
        with self._lock:
            for chunk in sorted(self._chunks.values(), key=lambda c: c.chunk_id):
                translated = chunk.payload.get("segments") or chunk.source_segments
                tm = chunk.payload.get("timing_map") or chunk.timing_map
                for local_i, global_i in enumerate(chunk.segment_indices):
                    if local_i < len(translated):
                        segments[global_i] = translated[local_i]
                    if local_i < len(tm):
                        timing[global_i] = tm[local_i]
        return (
            [s if s is not None else "" for s in segments],
            [t if t is not None else {"start": 0, "end": 0} for t in timing],
        )

    def _total_segments(self) -> int:
        if not self._chunks:
            return 0
        max_idx = 0
        for c in self._chunks.values():
            if c.segment_indices:
                max_idx = max(max_idx, max(c.segment_indices) + 1)
        return max_idx

    # ── Pause / resume / recovery (§13–§14) ──────────────────────────

    def suspend_all(self) -> None:
        with self._lock:
            for c in self._chunks.values():
                if c.status in (ChunkStatus.WAITING, ChunkStatus.RUNNING):
                    c.status = ChunkStatus.SUSPENDED

    def resume_all(self) -> None:
        with self._lock:
            for c in self._chunks.values():
                if c.status == ChunkStatus.SUSPENDED:
                    c.status = ChunkStatus.WAITING

    def chunks_to_resume(self, stages: tuple[str, ...] = PIPELINE_STAGES) -> list[PipelineChunk]:
        """Return chunks that still need processing (§14)."""
        out: list[PipelineChunk] = []
        with self._lock:
            for c in sorted(self._chunks.values(), key=lambda x: x.chunk_id):
                if c.status == ChunkStatus.COMPLETED:
                    continue
                if c.next_pending_stage(stages) is not None:
                    if c.status in (ChunkStatus.FAILED, ChunkStatus.RETRY):
                        c.attempts += 1
                        c.status = ChunkStatus.WAITING
                        c.error = ""
                    out.append(c)
        return out

    def save_checkpoint(self, path: str | Path) -> None:
        """Persist chunk state for crash recovery (§14)."""
        from engines.storage.atomic import atomic_write_json

        data = {
            "version": self.CHECKPOINT_VERSION,
            "project_id": self._project_id,
            "chunk_size": self._chunk_size,
            "saved_at": time.time(),
            "chunks": [c.to_dict() for c in self.all_chunks()],
        }
        atomic_write_json(path, data)
        logger.info("[CHUNK] checkpoint saved → %s (%d chunks)", path, len(data["chunks"]))

    def load_checkpoint(self, path: str | Path) -> bool:
        """Restore chunk state from checkpoint (§14)."""
        from engines.storage.atomic import read_json

        try:
            data = read_json(path)
        except Exception:
            return False
        if not data or data.get("version") != self.CHECKPOINT_VERSION:
            return False
        self._project_id = str(data.get("project_id") or "")
        self._chunk_size = int(data.get("chunk_size") or _DEFAULT_CHUNK)
        chunks = [PipelineChunk.from_dict(c) for c in (data.get("chunks") or [])]
        with self._lock:
            self._chunks = {c.chunk_id: c for c in chunks}
        logger.info("[CHUNK] checkpoint loaded ← %s (%d chunks)", path, len(chunks))
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self._project_id,
            "chunk_size": self._chunk_size,
            "summary": self.status_summary(),
            "chunks": [c.to_dict() for c in self.all_chunks()],
        }
