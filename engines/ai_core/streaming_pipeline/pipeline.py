"""AI Core 4.2 — streaming text conveyor (translation → semantic → timing → grammar)."""

from __future__ import annotations

import copy
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from engines.ai_core.contracts import AgentExecutionResult
from engines.ai_core.peer_validation import (
    MAX_PEER_RETURNS,
    validate_segment_peer_input,
)
from engines.ai_core.streaming_pipeline.mode import (
    AI_CORE_VERSION_STREAMING,
    STREAM_STAGES,
    TEXT_STREAM_STAGES,
)
from engines.ai_core.streaming_pipeline.report import write_streaming_pipeline_report
from engines.ai_core.streaming_pipeline.snapshot import SegmentSnapshot

logger = logging.getLogger("tubedub.ai_core.streaming_pipeline")

_SENTINEL = object()
_STAGE_ORDER = STREAM_STAGES


@dataclass
class WorkPacket:
    segment_index: int
    list_index: int
    snapshot: SegmentSnapshot
    attempt: int = 0
    queued_at: float = field(default_factory=time.perf_counter)


@dataclass
class StageStats:
    name: str
    processed: int = 0
    busy_ms: float = 0.0
    wait_ms: float = 0.0
    peer_returns: int = 0
    errors: int = 0

    @property
    def utilization(self) -> float:
        total = self.busy_ms + self.wait_ms
        if total <= 0:
            return 0.0
        return round(self.busy_ms / total, 4)


@dataclass
class SegmentTrace:
    index: int
    stages: list[dict[str, Any]] = field(default_factory=list)
    peer_returns: int = 0
    retries: int = 0
    status: str = "pending"
    error: str = ""


class StreamingTextPipeline:
    """
    Conveyor-mode text processing. Each stage runs in its own worker thread.
    Segments flow as immutable snapshots; one segment failure never stops the belt.
    """

    def __init__(
        self,
        manifest: dict[str, Any],
        state: dict[str, Any],
        task_id: str,
        *,
        stages: tuple[str, ...] | None = None,
        app_dir=None,
    ):
        self.manifest = manifest
        self.state = state
        self.task_id = task_id
        self.app_dir = app_dir
        chain_stages = stages or TEXT_STREAM_STAGES
        self.stages = tuple(s for s in _STAGE_ORDER if s in chain_stages)
        if not self.stages:
            self.stages = ("semantic", "timing", "grammar")

        raw = list(state.get("segments") or [])
        self.segments: list[dict[str, Any]] = [copy.deepcopy(s) for s in raw]
        self.n = len(self.segments)
        self.lock = threading.Lock()
        self.warnings: list[str] = []
        self.errors: list[str] = []

        self.stage_stats: dict[str, StageStats] = {s: StageStats(name=s) for s in self.stages}
        self.traces: dict[int, SegmentTrace] = {
            int(s.get("index", i)): SegmentTrace(index=int(s.get("index", i)))
            for i, s in enumerate(self.segments)
        }
        self._peer_log: list[dict[str, Any]] = []
        self._started_at = time.perf_counter()

        self._queues: dict[str, queue.Queue] = {
            s: queue.Queue() for s in self.stages
        }
        self._threads: list[threading.Thread] = []

    def run(self) -> AgentExecutionResult:
        t0 = time.perf_counter()
        if self.n == 0:
            return self._empty_result(t0)

        first = self.stages[0]
        for i, seg in enumerate(self.segments):
            idx = int(seg.get("index", i))
            self._queues[first].put(
                WorkPacket(
                    segment_index=idx,
                    list_index=i,
                    snapshot=SegmentSnapshot.from_segment(seg, idx),
                )
            )
        self._queues[first].put(_SENTINEL)

        for stage in self.stages:
            out_stage = self._next_stage(stage)
            workers = 1
            if stage == "voice":
                from engines.ai_core.streaming_pipeline.voice_stage import DEFAULT_VOICE_WORKERS

                workers = max(
                    1,
                    int(self.state.get("streaming_voice_workers") or DEFAULT_VOICE_WORKERS),
                )
            for wi in range(workers):
                thread = threading.Thread(
                    target=self._stage_worker,
                    args=(stage, out_stage, workers),
                    name=f"stream-{stage}-{wi}-{self.task_id[:8]}",
                    daemon=True,
                )
                thread.start()
                self._threads.append(thread)
            if workers > 1:
                for _ in range(workers - 1):
                    self._queues[stage].put(_SENTINEL)

        for thread in self._threads:
            thread.join()

        elapsed = (time.perf_counter() - t0) * 1000
        self.state["segments"] = self.segments
        for stage in self.stages:
            self.state[f"{stage}_agent_path"] = True
            self.state[f"{stage}_agent_status"] = "success"
        if "voice" in self.stages:
            self.state["streaming_voice_done"] = True

        report = self._build_report(elapsed)
        report_path = write_streaming_pipeline_report(self.task_id, report, app_dir=self.app_dir)
        self.state["streaming_pipeline_report_path"] = str(report_path)
        self.state["pipeline_mode"] = "streaming"

        status = "success"
        if self.errors and not all(t.status == "completed" for t in self.traces.values()):
            status = "warning"

        return AgentExecutionResult(
            status=status,
            updated_state=dict(self.state),
            metrics={
                "pipeline_mode": "streaming",
                "ai_core_version": AI_CORE_VERSION_STREAMING,
                "segment_count": self.n,
                "execution_time_ms": round(elapsed, 1),
                "throughput_sps": round(self.n / max(0.001, elapsed / 1000), 3),
                "stage_utilization": {s: self.stage_stats[s].utilization for s in self.stages},
            },
            warnings=self.warnings,
            errors=self.errors,
            execution_time_ms=round(elapsed, 1),
            decision_log=[f"streaming_stages={','.join(self.stages)}"],
        )

    def _empty_result(self, t0: float) -> AgentExecutionResult:
        elapsed = (time.perf_counter() - t0) * 1000
        return AgentExecutionResult(
            status="warning",
            updated_state=dict(self.state),
            metrics={"segment_count": 0},
            warnings=["streaming_no_segments"],
            errors=[],
            execution_time_ms=round(elapsed, 1),
            decision_log=["streaming_empty"],
        )

    def _next_stage(self, stage: str) -> str | None:
        try:
            pos = self.stages.index(stage)
            if pos + 1 < len(self.stages):
                return self.stages[pos + 1]
        except ValueError:
            pass
        return None

    def _prev_stage(self, stage: str) -> str | None:
        try:
            pos = self.stages.index(stage)
            if pos > 0:
                return self.stages[pos - 1]
        except ValueError:
            pass
        return None

    def _stage_worker(self, stage: str, out_stage: str | None, num_workers: int = 1) -> None:
        in_q = self._queues[stage]
        stats = self.stage_stats[stage]
        tgt = str(self.manifest.get("target_lang") or self.state.get("target_lang") or "uk")
        sentinels_seen = 0

        while True:
            wait_t0 = time.perf_counter()
            packet: WorkPacket | object = in_q.get()
            if packet is _SENTINEL:
                sentinels_seen += 1
                if sentinels_seen >= num_workers:
                    if out_stage:
                        self._queues[out_stage].put(_SENTINEL)
                    break
                continue

            assert isinstance(packet, WorkPacket)
            stats.wait_ms += (time.perf_counter() - wait_t0) * 1000
            busy_t0 = time.perf_counter()

            seg_dict = packet.snapshot.as_dict()
            list_index = packet.list_index
            trace = self.traces.get(packet.segment_index)
            if trace is None:
                continue

            for _ in range(MAX_PEER_RETURNS + 1):
                peer_returns = validate_segment_peer_input(
                    stage, seg_dict, target_lang=tgt, manifest=self.manifest
                )
                if not peer_returns or stage == self.stages[0]:
                    break

                stats.peer_returns += 1
                trace.peer_returns += 1
                pr = peer_returns[0]
                self._peer_log.append(
                    {**pr.to_dict(), "stage": stage, "action": "live_peer_return"}
                )
                upstream = pr.receiver_agent
                if packet.attempt >= MAX_PEER_RETURNS or upstream not in self.stages:
                    self.warnings.append(
                        f"segment_{packet.segment_index}:{stage}:max_peer_returns"
                    )
                    break

                packet.attempt += 1
                trace.retries += 1
                self._run_stage(upstream, list_index, seg_dict, trace, stats)
                with self.lock:
                    seg_dict = copy.deepcopy(self.segments[list_index])

            self._run_stage(stage, list_index, seg_dict, trace, stats)
            stats.processed += 1
            try:
                from engines.ai_core.pipeline_heartbeat import emit_ai_core_heartbeat

                emit_ai_core_heartbeat(
                    self.task_id,
                    agent=stage,
                    current_segment=packet.segment_index + 1,
                    segments_done=stats.processed,
                    live_message=f"AI Core stream: {stage} #{packet.segment_index + 1}",
                )
            except Exception:
                pass

            if out_stage:
                with self.lock:
                    fresh = SegmentSnapshot.from_segment(
                        self.segments[list_index], packet.segment_index
                    )
                self._queues[out_stage].put(
                    WorkPacket(
                        segment_index=packet.segment_index,
                        list_index=list_index,
                        snapshot=fresh,
                        attempt=0,
                    )
                )
            else:
                trace.status = "completed"

            stats.busy_ms += (time.perf_counter() - busy_t0) * 1000

    def _run_stage(
        self,
        stage: str,
        list_index: int,
        seg_dict: dict[str, Any],
        trace: SegmentTrace,
        stats: StageStats,
    ) -> None:
        from engines.ai_core.ai_network.bridge import (
            emit_agent_finished,
            emit_agent_started,
            emit_segment_in,
            emit_segment_out,
        )
        from engines.ai_core.reviewer_gate import review_agent_output

        seg_idx = int(seg_dict.get("index", list_index))
        emit_segment_in(self.task_id, stage, seg_idx)
        emit_agent_started(self.task_id, stage, segment_index=seg_idx)
        stage_start = time.perf_counter()
        try:
            updated = self._process_stage(stage, list_index, seg_dict)
            with self.lock:
                self.segments[list_index].update(updated)
            duration_ms = round((time.perf_counter() - stage_start) * 1000, 1)
            trace.stages.append(
                {
                    "agent": stage,
                    "started_at": round(stage_start, 4),
                    "finished_at": round(time.perf_counter(), 4),
                    "duration_ms": duration_ms,
                    "attempt": trace.retries,
                }
            )
            emit_agent_finished(
                self.task_id,
                stage,
                status="success",
                ms=duration_ms,
                segment_index=seg_idx,
            )
            emit_segment_out(self.task_id, stage, seg_idx, status="success")
            review_agent_output(
                self.task_id,
                stage,
                segments=[updated],
                tgt_lang=str(self.manifest.get("target_lang") or ""),
            )
        except Exception as exc:
            stats.errors += 1
            trace.status = "error"
            trace.error = str(exc)
            self.warnings.append(f"segment_{trace.index}:{stage}:{exc}")
            logger.warning("Streaming stage %s seg %s failed: %s", stage, trace.index, exc)

    def _process_stage(self, stage: str, list_index: int, seg: dict[str, Any]) -> dict[str, Any]:
        from engines.ai_core.streaming_pipeline.handlers import (
            process_quality_segment,
            process_reviewer_segment,
            process_voice_prep_segment,
        )

        if stage == "quality":
            return process_quality_segment(
                list_index, seg, manifest=self.manifest, state=self.state, task_id=self.task_id
            )
        if stage == "reviewer":
            return process_reviewer_segment(
                list_index, seg, manifest=self.manifest, state=self.state, task_id=self.task_id
            )
        if stage == "voice_preparation":
            return process_voice_prep_segment(
                list_index, seg, manifest=self.manifest, state=self.state
            )
        if stage == "voice":
            from engines.ai_core.streaming_pipeline.handlers import process_voice_segment_stream

            return process_voice_segment_stream(
                list_index, seg, manifest=self.manifest, state=self.state, task_id=self.task_id
            )

        from engines.ai_core.quality_agent.retry_orchestrator import rerun_agent_for_segment

        class_map = {
            "translation": "TranslationAgent",
            "semantic": "SemanticAgent",
            "timing": "TimingAgent",
            "grammar": "GrammarAgent",
        }
        class_name = class_map.get(stage)
        if not class_name:
            return seg

        self.state["segments"] = self.segments
        result = rerun_agent_for_segment(
            class_name, list_index, self.manifest, self.state, self.task_id
        )
        return result if result else seg

    def _list_index(self, segment_index: int) -> int:
        for i, s in enumerate(self.segments):
            if int(s.get("index", i)) == segment_index:
                return i
        return segment_index

    def _build_report(self, elapsed_ms: float) -> dict[str, Any]:
        completed = sum(1 for t in self.traces.values() if t.status == "completed")
        total_returns = sum(s.peer_returns for s in self.stage_stats.values())
        return {
            "task_id": self.task_id,
            "engine": f"Streaming Pipeline {AI_CORE_VERSION_STREAMING}",
            "segment_count": self.n,
            "stages": list(self.stages),
            "summary": {
                "success": completed >= self.n // 2 if self.n else True,
                "completed_segments": completed,
                "total_elapsed_ms": round(elapsed_ms, 1),
                "throughput_segments_per_sec": round(
                    self.n / max(0.001, elapsed_ms / 1000), 3
                ),
                "peer_returns_total": total_returns,
                "retries_total": sum(t.retries for t in self.traces.values()),
            },
            "agent_utilization": {
                s: {
                    "processed": self.stage_stats[s].processed,
                    "busy_ms": round(self.stage_stats[s].busy_ms, 1),
                    "wait_ms": round(self.stage_stats[s].wait_ms, 1),
                    "utilization": self.stage_stats[s].utilization,
                    "peer_returns": self.stage_stats[s].peer_returns,
                    "errors": self.stage_stats[s].errors,
                }
                for s in self.stages
            },
            "segments": [
                {
                    "index": t.index,
                    "status": t.status,
                    "peer_returns": t.peer_returns,
                    "retries": t.retries,
                    "error": t.error,
                    "stage_transitions": t.stages,
                }
                for t in sorted(self.traces.values(), key=lambda x: x.index)
            ],
            "peer_returns": self._peer_log,
            "monitor": {
                "pipeline_started_at": round(self._started_at, 4),
                "final_queue_depth": {s: self._queues[s].qsize() for s in self.stages},
            },
        }


class StreamingTextPipelineRunner:
    """Orchestrator-compatible runner — one mode, not a permanent agent slot."""

    VERSION = AI_CORE_VERSION_STREAMING

    def run(
        self,
        manifest: dict[str, Any],
        state: dict[str, Any],
        task_id: str,
    ) -> AgentExecutionResult:
        stages = tuple(state.get("streaming_stages") or ())
        if not stages:
            from engines.ai_core.streaming_pipeline.mode import streaming_stages_in_chain

            stages = streaming_stages_in_chain(
                list(state.get("_streaming_chain_names") or TEXT_STREAM_STAGES)
            )
        pipeline = StreamingTextPipeline(
            manifest, state, task_id, stages=stages or None
        )
        return pipeline.run()


def run_streaming_text_pipeline(
    manifest: dict[str, Any],
    state: dict[str, Any],
    task_id: str,
    *,
    stages: tuple[str, ...] | None = None,
    app_dir=None,
) -> AgentExecutionResult:
    pipeline = StreamingTextPipeline(
        manifest, state, task_id, stages=stages, app_dir=app_dir
    )
    return pipeline.run()
