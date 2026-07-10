# Event Bus Core — Stage 1 Architecture

## Goal

Replace linear module-to-module calls with a central **Event Bus**. After this stage,
pipeline agents communicate **only** via typed events — never via direct calls like
`translator.translate()` or `timing.align()`.

Algorithms (Cleaner, Timing, TTS, Mix, translation quality) are **unchanged** —
only the transport layer changes.

## Module layout

| File | Role |
|------|------|
| `core/event_bus.py` | `AsyncEventBus` — publish / subscribe / unsubscribe / broadcast |
| `core/event_types.py` | `BusEvent` envelope + `EventType` enum + TypedDict payloads |
| `core/event_agents.py` | Agent adapters (Cleaner, Translator, Timing, Voice, Mix, Export) |
| `core/event_pipeline.py` | Coordinator — starts agents, kicks off pipeline, waits for completion |

Legacy sync bus remains in `engines/core/events.py` (telemetry only).

## Event envelope (TZ §2)

Every message is a `BusEvent`:

- `event_id`, `event_type`, `project_id`, `chunk_id`
- `timestamp`, `payload`, `priority`, `source_agent`

Raw strings or untyped dicts are rejected at `publish()`.

## Agent chain

Preserves existing algorithm order (translate → align → timing → voice → mix → export):

```
PIPELINE_STARTED
    → Translator → translation_completed
    → Cleaner    → segments_aligned
    → Timing     → timing_completed
    → Voice      → voice_completed
    → Mix        → mix_completed
    → Export     → export_completed → pipeline_completed
```

All six agents run as concurrent `asyncio.create_task()` workers from pipeline start.
They wait on their input event types via dedicated `asyncio.Queue` channels.

## Error isolation (TZ §7)

- Each agent wraps work in try/except inside `run_agent_loop`
- Failures publish `agent_error` — bus and other agents keep running
- Coordinator logs errors but does not abort unless completion timeout

## Integration

| Location | Change |
|----------|--------|
| `api/auto_dub_api.py` → `_prepare_translated_segments` | Uses Event Bus when `VM_EVENT_BUS=1` (default) |
| `GET /api/auto_dub/pipeline_orchestrator/status` | Includes planner stats (separate from bus) |

Set `VM_EVENT_BUS=0` to fall back to legacy direct calls.

## Logging (TZ §6)

```
[EVENT] translator translation_completed Chunk 0 → Cleaner
[BUS] segments_aligned Chunk 0 project=abc12345 Subscribers: 1
```

## Next stages

- **Stage 2**: Wire Timing → Voice → Mix → Export in main `_run_pipeline_inner`
- **Stage 3**: Per-chunk parallelism (chunk_id per segment)
- **Stage 4**: AI Orchestrator publishes/subscribes on same bus

## Tests

`tests/test_event_bus_core.py`
