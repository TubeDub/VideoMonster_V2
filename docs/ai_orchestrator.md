# AI Orchestrator — Stage 2 (TZ #2)

## Goal

After the Event Bus (Stage 1), provide a **single control center** that coordinates all
pipeline agents, monitors system load, eliminates idle time, and manages the conveyor —
without performing any video/translation/TTS work itself.

**Principle:** Agents execute work. The Orchestrator makes decisions.

## Modules

| Module | Role |
|--------|------|
| `core/orchestrator.py` | Agent lifecycle, queues, priorities, stalls, stats, fault tolerance |
| `core/resource_monitor.py` | CPU / RAM / GPU / VRAM sampling (psutil + torch, best-effort) |
| `core/event_pipeline.py` | Wires orchestrator when `VM_ORCHESTRATOR=1` (default on) |

## Agent lifecycle (§1)

Each registered agent is supervised by `AgentSupervisor`:

| State | Meaning |
|-------|---------|
| `idle` | Not running |
| `waiting` | Subscribed, waiting for events |
| `working` | Processing an event |
| `paused` | Suspended by orchestrator (memory pressure or API) |
| `restarting` | Recovering after crash |
| `failed` | Exceeded `max_restarts` (default 5) |

On crash: register error → backoff → auto-restart → requeue chunk if applicable.

## Dynamic dispatch (§2)

No fixed stage order inside the orchestrator. Chunks are submitted with stage-derived
priorities and served via `request_chunk()` in priority order.

## Stall control (§3)

Background `_stall_loop` checks agents in `waiting` longer than `stall_threshold_s`
(default 15s). `_diagnose_stall()` walks `AGENT_UPSTREAM` (e.g. voice → timing → cleaner
→ translator) and reports queue depths / upstream state.

## Resource monitoring (§4)

`_resource_loop` samples every `resource_interval_s` (default 3s) and appends to
project statistics (CPU/GPU/RAM/VRAM averages).

## Priorities (§5)

| Priority | Stages |
|----------|--------|
| Critical | STT, translation |
| High | cleaner, timing |
| Normal | TTS, voice, mix, export |
| Low | review |
| Background | reports, indexing, logs, analytics |

Under memory pressure, Low/Background agents are paused automatically.

## Memory guard (§6)

When RAM or VRAM ≥ 90% (configurable):
- Reduce `max_concurrent` by 1
- Pause Low/Background agents
- Restore limits when pressure drops

## Concurrency limits (§7)

`max_concurrent` is computed from `ResourcePlanner` snapshot:
- GPU available: `max(2, cpu_cores)`
- CPU-only: `max(1, cpu_cores - 1)`

Heavy agents acquire a slot before running; others wait without blocking the bus.

## Global statistics (§8)

`OrchestratorStats` per project: elapsed time, errors, restarts, chunks
requested/finished/requeued, stall recoveries, average CPU/GPU/RAM/VRAM, per-agent busy ms.

## Public API (§9)

```python
orch.request_chunk(agent=None) -> dict | None
orch.finish_chunk(chunk_id, ok=True)
orch.submit_chunk(chunk_id, stage, payload, priority=None)
orch.pause(agent=None)
orch.resume(agent=None)
orch.get_status() -> dict
```

Singleton: `get_orchestrator()` / `set_orchestrator()`.

## Fault tolerance (§10)

Handler exceptions:
1. Publish `agent_error` on the bus
2. Requeue in-flight chunk (increment `attempts`)
3. Continue other agents — project does not stop

## Integration

```
run_pipeline_async()
  ├─ reset_event_bus()
  ├─ build_default_orchestrator(bus, ctx, agents=...)
  ├─ orch.start(project_id)
  ├─ publish PIPELINE_STARTED
  ├─ wait for completion event
  ├─ orch.shutdown()
  └─ return PipelineRunResult (+ orchestrator_status in ctx)
```

HTTP (developer mode): `GET /api/pipeline/orchestrator/status`

## Configuration

| Variable | Default | Meaning |
|----------|---------|---------|
| `VM_ORCHESTRATOR` | `1` | Use AI Orchestrator to supervise agents |
| `VM_EVENT_BUS` | `1` | Use Event Bus pipeline (required) |

## Tests

`tests/test_orchestrator.py` — priorities, chunk queue, memory pressure, stall diagnosis,
pause/resume, lifecycle, fault tolerance.

`tests/test_event_bus_core.py` — Event Bus + full 6-agent chain (unchanged).

## Next stage

**LLM Dispatcher** — intelligent multi-model distribution on top of this orchestrator
(queue-aware routing, model pools, backup-on-failure only).
