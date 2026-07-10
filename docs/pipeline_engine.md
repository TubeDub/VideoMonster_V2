# Adaptive Chunking + Pipeline Engine — Stage 4 (TZ #4)

## Goal

After Event Bus, AI Orchestrator, and LLM Dispatcher, replace sequential
whole-film processing with a **continuous intelligent conveyor** that keeps
all stages busy simultaneously without degrading translation, timing, or voice quality.

**Principle:** Never wait for the entire film at any stage. Each agent starts
as soon as its chunk arrives.

## Modules

| Module | Role |
|--------|------|
| `core/chunk_manager.py` | Adaptive chunk sizing, chunk state, order merge, checkpoint persistence |
| `core/pipeline_engine.py` | Chunk conveyor, per-stage buffers, pause/resume, balancing, recovery |
| `core/event_pipeline.py` | `run_chunk_pipeline_sync()` entry point with `VM_PIPELINE_ENGINE` flag |

## Full conveyor (§2)

Each chunk flows through:

```
Input → Whisper → Cleaner → Translator → AI Review → Timing → Voice → Mix → Export
```

Whisper is typically done upstream (batch STT); the engine skips it by default
(`skip_stages=("whisper",)`) and starts from Cleaner on STT output segments.

All stages run **simultaneously** — chunk 18 in Cleaner while chunk 12 is in Voice
and chunk 4 in Export (§8).

## Adaptive Chunking (§4–§5)

`ChunkManager.compute_chunk_size()` considers:
- CPU / RAM / GPU / VRAM (via `ResourceMonitor`)
- Measured stage throughput (via `ResourcePlanner`)
- Bottleneck detection

| Condition | Action |
|-----------|--------|
| RAM or VRAM ≥ 90% | Shrink chunk size |
| CPU < 40%, RAM < 70%, no bottleneck | Grow chunk size |
| Stage bottleneck detected | Shrink via `adjust_for_bottleneck()` |

Bounds: `_MIN_CHUNK=1` … `_MAX_CHUNK=32`, default 4 segments per chunk.

## Pipeline buffers (§6)

Each stage has its own `queue.Queue` sized by `ResourcePlanner.plan_stage().queue_size`.
Workers per stage come from `plan_stage().workers`.

## Chunk status (§10)

| Status | Meaning |
|--------|---------|
| `waiting` | Queued, not yet started |
| `running` | Currently in a stage |
| `completed` | All stages done |
| `failed` | Stage error |
| `suspended` | Paused by user |
| `retry` | Re-queued after failure |

## Order preservation (§11)

`ChunkManager.merge_results()` flattens completed chunks back into ordered
`segments` + `timing_map` lists by `segment_indices` — film sequence is never changed.

## Pause / resume (§13)

```python
engine.pause()   # suspends all waiting/running chunks
engine.resume()  # continues without re-processing completed chunks
```

HTTP: `POST /api/pipeline/engine/pause` and `/resume` (developer mode).

## Crash recovery (§14)

Checkpoints saved atomically via `engines.storage.atomic.atomic_write_json`:

```python
config.checkpoint_path = "/path/to/chunks.json"
engine.run(resume=True)  # loads checkpoint, skips completed stages per chunk
```

Each chunk tracks `completed_stages` — recovery resumes from the first pending stage.

## Balancing (§9)

When `ResourcePlanner._bottleneck_stage()` identifies the slowest belt section,
`engine.rebalance()` shrinks chunk size to reduce upstream pressure without
stopping other stages.

## Integration

```python
from core.event_pipeline import run_chunk_pipeline_sync

result = run_chunk_pipeline_sync(
    task_id="task-1",
    source_segments=segments,
    timing_map=timing_map,
    source_lang="en",
    target_lang="uk",
    checkpoint_path="/tmp/chunks.json",
)
```

When `VM_PIPELINE_ENGINE=0`, falls back to the event-bus pipeline (Stage 1).

Default handlers in `build_default_handlers()` delegate to existing engines
(`cleaner`, `translation_pipeline`, `timing_aware_translation`, `tts`) —
**no algorithm changes**.

## Configuration

| Variable | Default | Meaning |
|----------|---------|---------|
| `VM_PIPELINE_ENGINE` | `1` | Use adaptive chunk conveyor |
| `VM_EVENT_BUS` | `1` | Event Bus (fallback path) |
| `VM_ORCHESTRATOR` | `1` | AI Orchestrator supervision |

## HTTP API (developer mode)

- `GET  /api/pipeline/engine/status` — live conveyor + chunk summary
- `POST /api/pipeline/engine/pause` — suspend processing
- `POST /api/pipeline/engine/resume` — continue from saved state

## Tests

`tests/test_pipeline_engine.py` — adaptive sizing, order merge, checkpoint
roundtrip, parallel stages, pause/resume, failure isolation (12 tests).

## Next stage

**Auto Recovery, AI Memory, intelligent caching** — the chunk checkpoint format
and per-stage completion tracking are ready for persistent AI memory and
cross-run cache reuse without reprocessing completed chunks.
