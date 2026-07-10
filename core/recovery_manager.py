"""Recovery Manager — fault tolerance layer (TZ #5 §1–§14).

Localises every failure: only the damaged line/chunk is retried, never the
whole film. Problematic chunks go to the Parking Queue so the pipeline keeps
moving. Dynamic timeouts, stall detection, crash recovery, logging and stats.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from core.micro_validator import MicroValidator, ValidationResult, get_validator

logger = logging.getLogger("tubedub.recovery")


def recovery_enabled() -> bool:
    return str(os.getenv("VM_RECOVERY", "1")).strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


class RecoveryAction(str, Enum):
    """What to do after a failure."""

    RETRY_LINE = "retry_line"
    RETRY_CHUNK = "retry_chunk"
    PARK = "park"
    FALLBACK = "fallback"
    CONTINUE = "continue"
    ABORT = "abort"


@dataclass
class RetryPolicy:
    """Per-agent retry strategy (§5)."""

    max_retries: int = 3
    backoff_base_s: float = 1.0
    backoff_max_s: float = 10.0
    allow_fallback: bool = True

    @classmethod
    def from_env(cls, stage: str) -> RetryPolicy:
        key = stage.upper().replace("-", "_")
        return cls(
            max_retries=int(os.getenv(f"VM_RECOVERY_{key}_RETRIES", "3")),
            backoff_base_s=float(os.getenv(f"VM_RECOVERY_{key}_BACKOFF", "1.0")),
            allow_fallback=os.getenv(f"VM_RECOVERY_{key}_FALLBACK", "1").strip()
            not in ("0", "false"),
        )


# Default policies per stage.
DEFAULT_POLICIES: dict[str, RetryPolicy] = {
    "translator": RetryPolicy(max_retries=3, backoff_base_s=2.0),
    "review": RetryPolicy(max_retries=2),
    "timing": RetryPolicy(max_retries= 2),
    "voice": RetryPolicy(max_retries=3, backoff_base_s=1.5),
    "mix": RetryPolicy(max_retries=2),
    "export": RetryPolicy(max_retries=2),
    "cleaner": RetryPolicy(max_retries=2),
}


@dataclass
class RecoveryEvent:
    """One recorded failure/recovery (§13)."""

    timestamp: float
    agent: str
    chunk_id: int
    reason: str
    model: str = ""
    recovered_at: float = 0.0
    retries: int = 0
    used_fallback: bool = False
    line_index: int = -1
    action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "agent": self.agent,
            "chunk_id": self.chunk_id,
            "reason": self.reason,
            "model": self.model,
            "recovered_at": self.recovered_at,
            "retries": self.retries,
            "used_fallback": self.used_fallback,
            "line_index": self.line_index,
            "action": self.action,
        }


@dataclass
class RecoveryStatistics:
    """Aggregate reliability stats (§14)."""

    total_errors: int = 0
    successful_recoveries: int = 0
    timeouts: int = 0
    total_recovery_ms: float = 0.0
    fallback_switches: int = 0
    local_line_fixes: int = 0
    crash_recoveries: int = 0
    parked_chunks: int = 0
    released_from_park: int = 0

    @property
    def avg_recovery_ms(self) -> float:
        return (
            self.total_recovery_ms / self.successful_recoveries
            if self.successful_recoveries
            else 0.0
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_errors": self.total_errors,
            "successful_recoveries": self.successful_recoveries,
            "timeouts": self.timeouts,
            "avg_recovery_ms": round(self.avg_recovery_ms, 1),
            "fallback_switches": self.fallback_switches,
            "local_line_fixes": self.local_line_fixes,
            "crash_recoveries": self.crash_recoveries,
            "parked_chunks": self.parked_chunks,
            "released_from_park": self.released_from_park,
        }


@dataclass
class RunningTask:
    """A task being watched for stalls (§10)."""

    chunk_id: int
    stage: str
    started_at: float
    last_activity: float
    model: str = ""
    cancelled: bool = False


class ParkingQueue:
    """Suspended Queue — problematic chunks wait here (§8)."""

    def __init__(self) -> None:
        self._queue: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._parked: dict[int, Any] = {}

    def park(self, chunk: Any, reason: str = "") -> None:
        cid = getattr(chunk, "chunk_id", -1)
        with self._lock:
            chunk.error = reason
            self._parked[cid] = chunk
        self._queue.put(chunk)
        logger.info("[RECOVERY] parked chunk=%s reason=%s", cid, reason)

    def release_ready(self, max_count: int = 4) -> list[Any]:
        """Return parked chunks when resources are free."""
        released: list[Any] = []
        for _ in range(max_count):
            try:
                chunk = self._queue.get_nowait()
            except queue.Empty:
                break
            cid = getattr(chunk, "chunk_id", -1)
            with self._lock:
                self._parked.pop(cid, None)
            released.append(chunk)
        return released

    def depth(self) -> int:
        return self._queue.qsize()

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "depth": self.depth(),
                "chunk_ids": list(self._parked.keys()),
            }


class RecoveryManager:
    """Central fault-tolerance coordinator (TZ #5)."""

    def __init__(
        self,
        project_id: str = "",
        *,
        app_dir: str | Path | None = None,
        validator: MicroValidator | None = None,
        policies: dict[str, RetryPolicy] | None = None,
    ) -> None:
        self.project_id = project_id
        self.app_dir = Path(app_dir) if app_dir else Path.cwd()
        self.validator = validator or get_validator()
        self.policies = dict(policies or DEFAULT_POLICIES)
        self.parking = ParkingQueue()
        self.stats = RecoveryStatistics()
        self._events: list[RecoveryEvent] = []
        self._lock = threading.RLock()
        self._running: dict[str, RunningTask] = {}
        self._line_retries: dict[tuple[int, str, int], int] = {}  # (chunk, stage, line) → count
        self._chunk_retries: dict[tuple[int, str], int] = {}
        self._stall_thread: threading.Thread | None = None
        self._stall_stop = threading.Event()
        self._log_path = self.app_dir / "logs" / "recovery.log"
        self._stats_path = self.app_dir / "logs" / "recovery_statistics.json"
        self._log_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Dynamic timeouts (§6) ────────────────────────────────────────

    def compute_timeout(
        self,
        stage: str,
        *,
        chunk_segment_count: int = 1,
        model: str = "",
        avg_latency_ms: float = 0.0,
    ) -> float:
        """Dynamic per-agent timeout — never fixed (§6)."""
        try:
            from engines.pipeline_orchestrator.resource_planner import get_planner

            plan = get_planner().plan_stage(stage, segment_count=chunk_segment_count)
            base = 30.0 * plan.timeout_scale
        except Exception:
            base = 60.0

        # Scale with chunk size.
        base *= max(1.0, chunk_segment_count ** 0.5)

        # Use measured model latency if available.
        if avg_latency_ms > 0:
            base = max(base, avg_latency_ms / 1000.0 * 3.0)

        # LLM-heavy stages get more headroom.
        if stage in ("translator", "review"):
            base *= 1.5

        return min(max(base, 15.0), 600.0)

    # ── Failure handling (§1, §4–§5) ─────────────────────────────────

    def decide_action(
        self,
        stage: str,
        chunk_id: int,
        *,
        error: str = "",
        validation: ValidationResult | None = None,
    ) -> tuple[RecoveryAction, list[int]]:
        """Determine recovery action — localise the damage (§4)."""
        policy = self.policies.get(stage) or RetryPolicy.from_env(stage)
        chunk_key = (chunk_id, stage)

        # Validation failures → retry only damaged lines (§4).
        if validation and not validation.ok:
            failed_lines = validation.failed_lines
            if failed_lines:
                retriable = []
                for li in failed_lines:
                    lk = (chunk_id, stage, li)
                    count = self._line_retries.get(lk, 0)
                    if count < policy.max_retries:
                        retriable.append(li)
                if retriable:
                    return RecoveryAction.RETRY_LINE, retriable
            # All lines exhausted → try chunk retry.
            cr = self._chunk_retries.get(chunk_key, 0)
            if cr < policy.max_retries:
                return RecoveryAction.RETRY_CHUNK, []

        # Exception / timeout.
        if error:
            if "timeout" in error.lower():
                with self._lock:
                    self.stats.timeouts += 1
            cr = self._chunk_retries.get(chunk_key, 0)
            if cr < policy.max_retries:
                if policy.allow_fallback and stage in ("translator", "review"):
                    return RecoveryAction.FALLBACK, []
                return RecoveryAction.RETRY_CHUNK, []

        # Exhausted → park (§8).
        return RecoveryAction.PARK, []

    def record_retry(
        self,
        chunk_id: int,
        stage: str,
        *,
        line_index: int = -1,
    ) -> int:
        if line_index >= 0:
            key = (chunk_id, stage, line_index)
            self._line_retries[key] = self._line_retries.get(key, 0) + 1
            with self._lock:
                self.stats.local_line_fixes += 1
            return self._line_retries[key]
        key = (chunk_id, stage)
        self._chunk_retries[key] = self._chunk_retries.get(key, 0) + 1
        return self._chunk_retries[key]

    def backoff(self, stage: str, attempt: int) -> float:
        policy = self.policies.get(stage) or RetryPolicy()
        return min(policy.backoff_base_s * (2 ** max(0, attempt - 1)), policy.backoff_max_s)

    # ── Stall detection (§10) ────────────────────────────────────────

    def track_start(self, chunk_id: int, stage: str, *, model: str = "") -> None:
        key = f"{chunk_id}:{stage}"
        now = time.monotonic()
        with self._lock:
            self._running[key] = RunningTask(
                chunk_id=chunk_id,
                stage=stage,
                started_at=now,
                last_activity=now,
                model=model,
            )

    def track_activity(self, chunk_id: int, stage: str) -> None:
        key = f"{chunk_id}:{stage}"
        with self._lock:
            t = self._running.get(key)
            if t:
                t.last_activity = time.monotonic()

    def track_end(self, chunk_id: int, stage: str) -> None:
        key = f"{chunk_id}:{stage}"
        with self._lock:
            self._running.pop(key, None)

    def check_stalls(self, stall_threshold_s: float = 30.0) -> list[RecoveryEvent]:
        """Find running tasks with no activity past timeout (§10)."""
        stalled: list[RecoveryEvent] = []
        now = time.monotonic()
        with self._lock:
            for key, task in list(self._running.items()):
                idle = now - task.last_activity
                limit = self.compute_timeout(
                    task.stage, chunk_segment_count=1
                )
                if idle > min(stall_threshold_s, limit):
                    task.cancelled = True
                    ev = RecoveryEvent(
                        timestamp=time.time(),
                        agent=task.stage,
                        chunk_id=task.chunk_id,
                        reason="stall_timeout",
                        model=task.model,
                        action=RecoveryAction.PARK.value,
                    )
                    stalled.append(ev)
                    self._events.append(ev)
                    self.stats.total_errors += 1
                    self.stats.timeouts += 1
                    self._running.pop(key, None)
                    self._write_log(ev)
        return stalled

    def start_stall_monitor(self, interval_s: float = 5.0) -> None:
        if self._stall_thread and self._stall_thread.is_alive():
            return
        self._stall_stop.clear()

        def _loop() -> None:
            while not self._stall_stop.wait(interval_s):
                try:
                    self.check_stalls()
                    # Release parked chunks when resources free.
                    released = self.parking.release_ready()
                    if released:
                        with self._lock:
                            self.stats.released_from_park += len(released)
                except Exception:
                    pass

        self._stall_thread = threading.Thread(
            target=_loop, name="recovery-stall-monitor", daemon=True
        )
        self._stall_thread.start()

    def stop_stall_monitor(self) -> None:
        self._stall_stop.set()

    # ── Event logging (§13) ────────────────────────────────────────────

    def register_failure(
        self,
        agent: str,
        chunk_id: int,
        reason: str,
        *,
        model: str = "",
        line_index: int = -1,
        action: RecoveryAction = RecoveryAction.PARK,
    ) -> RecoveryEvent:
        ev = RecoveryEvent(
            timestamp=time.time(),
            agent=agent,
            chunk_id=chunk_id,
            reason=reason,
            model=model,
            line_index=line_index,
            action=action.value,
        )
        with self._lock:
            self._events.append(ev)
            self.stats.total_errors += 1
            if action == RecoveryAction.PARK:
                self.stats.parked_chunks += 1
        self._write_log(ev)
        self._notify_orchestrator(ev)
        return ev

    def register_recovery(
        self,
        agent: str,
        chunk_id: int,
        *,
        started_at: float,
        retries: int = 0,
        used_fallback: bool = False,
    ) -> None:
        recovered_ms = (time.time() - started_at) * 1000.0
        with self._lock:
            self.stats.successful_recoveries += 1
            self.stats.total_recovery_ms += recovered_ms
            if used_fallback:
                self.stats.fallback_switches += 1
        ev = RecoveryEvent(
            timestamp=time.time(),
            agent=agent,
            chunk_id=chunk_id,
            reason="recovered",
            recovered_at=time.time(),
            retries=retries,
            used_fallback=used_fallback,
            action=RecoveryAction.CONTINUE.value,
        )
        with self._lock:
            self._events.append(ev)
        self._write_log(ev)

    def _write_log(self, ev: RecoveryEvent) -> None:
        try:
            line = json.dumps(ev.to_dict(), ensure_ascii=False)
            with open(self._log_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception as exc:
            logger.warning("[RECOVERY] log write failed: %s", exc)

    def _notify_orchestrator(self, ev: RecoveryEvent) -> None:
        try:
            from core.orchestrator import get_orchestrator

            orch = get_orchestrator()
            if orch is None:
                return
            logger.info(
                "[RECOVERY] notifying orchestrator: %s chunk=%s %s",
                ev.agent,
                ev.chunk_id,
                ev.reason,
            )
        except Exception:
            pass

    def save_statistics(self) -> None:
        try:
            data = {
                "project_id": self.project_id,
                "saved_at": time.time(),
                **self.stats.to_dict(),
                "recent_events": [e.to_dict() for e in self._events[-50:]],
                "parking": self.parking.to_dict(),
            }
            from engines.storage.atomic import atomic_write_json

            atomic_write_json(self._stats_path, data)
        except Exception as exc:
            logger.warning("[RECOVERY] stats save failed: %s", exc)

    # ── Crash recovery (§11) ───────────────────────────────────────────

    def restore_from_checkpoint(self, checkpoint_path: str | Path) -> bool:
        """Load pipeline checkpoint after crash (§11)."""
        try:
            from engines.storage.atomic import read_json

            data = read_json(checkpoint_path)
            if not data:
                return False
            with self._lock:
                self.stats.crash_recoveries += 1
            logger.info(
                "[RECOVERY] restored from checkpoint %s (%d chunks)",
                checkpoint_path,
                len(data.get("chunks") or []),
            )
            return True
        except Exception as exc:
            logger.warning("[RECOVERY] checkpoint restore failed: %s", exc)
            return False

    # ── Integrity (§12) ──────────────────────────────────────────────

    def verify_integrity(
        self,
        chunks: list[Any],
        *,
        expected_segment_count: int,
        tts_files: list[str] | None = None,
    ) -> ValidationResult:
        return self.validator.verify_integrity(
            chunks,
            expected_segment_count=expected_segment_count,
            tts_files=tts_files,
        )

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "project_id": self.project_id,
                "enabled": recovery_enabled(),
                "stats": self.stats.to_dict(),
                "parking": self.parking.to_dict(),
                "running_tasks": len(self._running),
                "recent_events": [e.to_dict() for e in self._events[-20:]],
            }


_manager: RecoveryManager | None = None
_manager_lock = threading.Lock()


def get_recovery_manager(
    project_id: str = "",
    *,
    app_dir: str | Path | None = None,
) -> RecoveryManager:
    global _manager
    if _manager is None or (project_id and _manager.project_id != project_id):
        with _manager_lock:
            if _manager is None or (project_id and _manager.project_id != project_id):
                _manager = RecoveryManager(project_id, app_dir=app_dir)
    return _manager
