"""Tests for Adaptive Chunking + Pipeline Engine (TZ #4)."""

from __future__ import annotations

import time

from core.chunk_manager import (
    ChunkManager,
    ChunkStatus,
    PipelineChunk,
    PIPELINE_STAGES,
)
from core.pipeline_engine import (
    PipelineEngine,
    PipelineEngineConfig,
    pipeline_engine_enabled,
)


def test_chunk_manager_splits_adaptively():
    mgr = ChunkManager(min_chunk=2, max_chunk=4)
    segs = [f"seg{i}" for i in range(10)]
    tm = [{"start": i * 1000, "end": (i + 1) * 1000} for i in range(10)]
    chunks = mgr.split_segments(segs, tm, project_id="t1", chunk_size=3)
    assert len(chunks) >= 3
    assert chunks[0].segment_indices == [0, 1, 2]
    assert sum(c.segment_count for c in chunks) == 10
    assert all(c.status == ChunkStatus.WAITING for c in chunks)


def test_chunk_manager_shrinks_under_memory_pressure():
    mgr = ChunkManager(min_chunk=1, max_chunk=16, ram_limit=50.0)

    class _HighRAM:
        def sample(self):
            class S:
                cpu_percent = 20.0
                ram_percent = 95.0
                vram_percent = 0.0
                gpu_available = False
            return S()

    mgr.monitor = _HighRAM()
    mgr._chunk_size = 8
    size = mgr.compute_chunk_size()
    assert size < 8
    assert size >= 1


def test_chunk_manager_grows_when_idle():
    mgr = ChunkManager(min_chunk=1, max_chunk=16)

    class _Idle:
        def sample(self):
            class S:
                cpu_percent = 10.0
                ram_percent = 40.0
                vram_percent = 0.0
                gpu_available = False
            return S()

    mgr.monitor = _Idle()
    mgr._chunk_size = 4
    size = mgr.compute_chunk_size()
    assert size >= 4


def test_merge_preserves_order():
    mgr = ChunkManager()
    segs = ["a", "b", "c", "d", "e"]
    tm = [{"start": i, "end": i + 1} for i in range(5)]
    chunks = mgr.split_segments(segs, tm, chunk_size=2)
    for c in chunks:
        c.payload["segments"] = [f"T{s}" for s in c.source_segments]
        c.payload["timing_map"] = c.timing_map
        c.status = ChunkStatus.COMPLETED
    merged_segs, merged_tm = mgr.merge_results()
    assert merged_segs == ["Ta", "Tb", "Tc", "Td", "Te"]
    assert len(merged_tm) == 5


def test_checkpoint_roundtrip(tmp_path):
    mgr = ChunkManager()
    segs = ["x", "y", "z"]
    tm = [{"start": 0, "end": 1}, {"start": 1, "end": 2}, {"start": 2, "end": 3}]
    mgr.split_segments(segs, tm, project_id="cp-test", chunk_size=2)
    path = tmp_path / "chunk_cp.json"
    mgr.save_checkpoint(path)

    mgr2 = ChunkManager()
    assert mgr2.load_checkpoint(path) is True
    assert len(mgr2.all_chunks()) == 2
    assert mgr2.all_chunks()[0].source_segments == ["x", "y"]


def test_chunks_to_resume_skips_completed():
    mgr = ChunkManager()
    segs = ["a", "b", "c"]
    tm = [{"start": 0, "end": 1}] * 3
    chunks = mgr.split_segments(segs, tm, chunk_size=3)
    chunks[0].status = ChunkStatus.COMPLETED
    chunks[0].completed_stages = list(PIPELINE_STAGES)
    pending = mgr.chunks_to_resume(("cleaner", "translator"))
    assert len(pending) == 0  # only one chunk, already completed


def test_pipeline_engine_runs_stages_in_parallel():
    trace: list[tuple[int, str]] = []

    def _make_handler(stage: str):
        def _h(chunk: PipelineChunk) -> PipelineChunk:
            trace.append((chunk.chunk_id, stage))
            chunk.payload.setdefault("stages", []).append(stage)
            chunk.payload["segments"] = list(chunk.source_segments)
            return chunk
        return _h

    config = PipelineEngineConfig(
        project_id="parallel-test",
        source_segments=["s0", "s1", "s2", "s3"],
        timing_map=[{"start": i, "end": i + 1} for i in range(4)],
        stages=("cleaner", "translator", "timing"),
        skip_stages=(),
    )
    engine = PipelineEngine(config)
    for stage in ("cleaner", "translator", "timing"):
        engine.register_handler(stage, _make_handler(stage))

    result = engine.run()
    assert result.ok
    assert result.chunks_processed > 0
    assert len(result.segments) == 4
    # Every chunk went through all 3 stages.
    for cid in range(len(engine.chunks.all_chunks())):
        stages_seen = [
            stage for c_id, stage in trace if c_id == cid
        ]
        assert "cleaner" in stages_seen
        assert "translator" in stages_seen
        assert "timing" in stages_seen


def test_pipeline_engine_order_preserved():
    config = PipelineEngineConfig(
        project_id="order-test",
        source_segments=[f"seg{i}" for i in range(6)],
        timing_map=[{"start": i * 100, "end": (i + 1) * 100} for i in range(6)],
        stages=("cleaner",),
        skip_stages=(),
    )

    def _tag(chunk: PipelineChunk) -> PipelineChunk:
        chunk.payload["segments"] = [f"T-{s}" for s in chunk.source_segments]
        return chunk

    engine = PipelineEngine(config)
    engine.register_handler("cleaner", _tag)
    result = engine.run()
    assert result.segments == [f"T-seg{i}" for i in range(6)]


def test_pipeline_engine_pause_resume():
    config = PipelineEngineConfig(
        project_id="pause-test",
        source_segments=["a"],
        timing_map=[{"start": 0, "end": 1000}],
        stages=("cleaner",),
        skip_stages=(),
    )
    engine = PipelineEngine(config)
    engine.register_handler("cleaner", lambda c: c)
    engine.chunks.split_segments(["a"], [{"start": 0, "end": 1000}])
    engine.pause()
    assert not engine._paused.is_set()
    assert engine.chunks.all_chunks()[0].status == ChunkStatus.SUSPENDED
    engine.resume()
    assert engine._paused.is_set()


def test_pipeline_engine_enabled_flag():
    import os

    os.environ["VM_PIPELINE_ENGINE"] = "1"
    assert pipeline_engine_enabled() is True
    os.environ["VM_PIPELINE_ENGINE"] = "0"
    assert pipeline_engine_enabled() is False
    os.environ.pop("VM_PIPELINE_ENGINE", None)


def test_failed_chunk_marked():
    config = PipelineEngineConfig(
        project_id="fail-test",
        source_segments=["x"],
        timing_map=[{"start": 0, "end": 100}],
        stages=("cleaner",),
        skip_stages=(),
    )

    def _boom(chunk: PipelineChunk) -> PipelineChunk:
        raise RuntimeError("stage failure")

    engine = PipelineEngine(config)
    engine.register_handler("cleaner", _boom)
    result = engine.run()
    # With recovery, parked chunks don't stop the pipeline — errors are recorded.
    assert any("stage failure" in e for e in result.errors)


def test_status_summary():
    mgr = ChunkManager()
    mgr.split_segments(["a", "b"], [{"start": 0, "end": 1}] * 2, chunk_size=1)
    summary = mgr.status_summary()
    assert summary.get("waiting", 0) == 2
