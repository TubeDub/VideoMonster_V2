# Auto Recovery + Micro Validator — Stage 5 (TZ #5)

## Goal

After Event Bus, AI Orchestrator, LLM Dispatcher, and Pipeline Engine, make the
system **fully fault-tolerant**: a hang or crash in any model, agent, or chunk
never stops the film.

**Principle:** One failure must not stop the entire pipeline.

## Modules

| Module | Role |
|--------|------|
| `core/recovery_manager.py` | Error handling, retries, timeouts, parking, stalls, logging, stats |
| `core/micro_validator.py` | Per-stage output validation before forwarding downstream |
| `core/pipeline_engine.py` | Integrated recovery + validation in every stage handler |

## Recovery Manager (§1)

`RecoveryManager` handles:
- Dynamic per-agent timeouts (§6) — `compute_timeout(stage, chunk_segment_count, ...)`
- Per-agent retry policies (§5) — configurable via `VM_RECOVERY_{STAGE}_RETRIES`
- Parking Queue (§8) — `ParkingQueue` for suspended chunks
- Stall detection (§10) — background monitor cancels stuck tasks
- Crash recovery (§11) — integrates with chunk checkpoint from Stage 4
- Integrity verification (§12) — post-film check via `MicroValidator`
- Logging (§13) — `logs/recovery.log` (JSON lines)
- Statistics (§14) — `logs/recovery_statistics.json`

## Micro Validator (§2–§3)

Runs after every stage, before passing to the next agent:

```
Translator → Micro Validator → Timing
Voice      → Micro Validator → Mix
```

LLM output checks (§3):
- Valid JSON (when expected)
- Correct line/segment count
- No boilerplate ("Here is the translation", etc.)
- No Markdown fences or bold
- No truncated responses (`...`)
- No empty lines

## Local repair (§4)

On validation failure, **only damaged lines are retried** — never the whole chunk
or film:

```
Chunk 15 → error on line 12 → retry line 12 only
```

After line retries exhausted → chunk retry → fallback model → park.

## Retry strategy (§5)

```
Retry 1 → Retry 2 → Retry 3 → Fallback → Park
```

Per-stage policies in `DEFAULT_POLICIES`; override via env:
`VM_RECOVERY_TRANSLATOR_RETRIES=5`, `VM_RECOVERY_VOICE_BACKOFF=2.0`, etc.

## Dynamic timeouts (§6)

Never fixed. Computed from:
- Chunk segment count
- `ResourcePlanner.timeout_scale`
- Measured model latency
- Stage type (LLM stages get 1.5× headroom)

Range: 15s – 600s.

## Fallback (§7)

On timeout/error with retries left and `allow_fallback=True`, chunk is marked
`use_fallback=True` and LLM Dispatcher selects the next model in the failover chain.

## Parking Queue (§8)

Chunks that cannot be recovered are **parked**, not dropped:
- Pipeline continues processing other chunks
- Orchestrator releases parked chunks when resources free up
- `ParkingQueue.release_ready()` returns them to work

## Stall control (§10)

Background `_stall_monitor` checks running tasks every 5s:
- No activity past dynamic timeout → cancel, park, log, notify Orchestrator

## Crash recovery (§11)

On restart, `ChunkManager.load_checkpoint()` restores:
- Completed stages per chunk (skipped on resume)
- Pending chunks re-enter the conveyor
- Already-finished chunks are never reprocessed

## Integrity check (§12)

After all chunks complete:
- All segment indices present, no gaps
- Correct order preserved
- TTS file count matches segment count

## Logging & statistics

| File | Content |
|------|---------|
| `logs/recovery.log` | JSON lines: timestamp, agent, chunk, reason, model, retries, fallback |
| `logs/recovery_statistics.json` | Aggregate: errors, recoveries, timeouts, line fixes, parks |

## Configuration

| Variable | Default | Meaning |
|----------|---------|---------|
| `VM_RECOVERY` | `1` | Enable fault tolerance layer |
| `VM_RECOVERY_TRANSLATOR_RETRIES` | `3` | Max retries for translator |
| `VM_RECOVERY_{STAGE}_RETRIES` | `3` | Per-stage retry count |
| `VM_RECOVERY_{STAGE}_BACKOFF` | `1.0` | Backoff base seconds |
| `VM_RECOVERY_{STAGE}_FALLBACK` | `1` | Allow model fallback |

## HTTP API (developer mode)

- `GET /api/pipeline/recovery/status` — stats, parking queue, recent events
- `GET /api/pipeline/engine/status` — includes recovery section when active

## Tests

`tests/test_recovery.py` — validator rules, dynamic timeouts, line retry
decision, parking queue, stall detection, logging, stats, pipeline integration
(18 tests).

## Next stage

Architecture is ready for **AI Memory and intelligent caching** — recovery
checkpoints + per-line retry metadata provide the foundation for cross-run
cache reuse without reprocessing.
