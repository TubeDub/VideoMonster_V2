# Pipeline Orchestrator — Architecture (Phase 1)

## Goal

Maximize CPU and resource utilization **without sacrificing translation quality**.
Stages must run as a **conveyor** (pipeline parallelism), not strictly one-after-another.

Quality rules (non-negotiable):
- Never simplify translation for speed
- Never disable AI Adaptation
- Never skip segments
- Never replace AI with raw MT due to timeouts
- Only scheduling changes — not what work is done

## What exists today (before this work)

| Component | Location | Status |
|-----------|----------|--------|
| Text streaming conveyor | `engines/ai_core/streaming_pipeline/pipeline.py` | Per-segment queues for AI Core text agents |
| Parallel TTS | `engines/tts.py` (asyncio) | Per-group parallel synthesis |
| Parallel adaptation | `engines/timing_aware_translation.py` | ThreadPoolExecutor per segment |
| Single LLM + fallback chain | `engines/translation_adapt.py` + `llm_retry_manager.py` | One active model; fallback mutates env |
| Hardware probe | `engines/hardware_probe.py` | CUDA / Whisper device |
| Scattered tuning | Many `VM_*` env vars | Fixed defaults per module |

**Gap:** No single capacity planner; no multi-model pool; main dub worker (`_run_pipeline_inner`) is still stage-sequential for STT → translate → TTS.

## Phase 1 foundation (this delivery)

### 1. Resource Planner (`engines/pipeline_orchestrator/resource_planner.py`)

Single authoritative source for:
- `workers` per stage
- `batch_size` (translation / AI adaptation)
- `max_in_flight` / `queue_size`
- `timeout_scale` (CPU-aware)

Inputs: CPU cores, RAM, GPU, **measured throughput** (rolling window).
Outputs: `StagePlan` per stage; bottleneck detection for balancing.

API: `get_planner().plan_stage("tts", segment_count=20)` → `GET /api/auto_dub/pipeline_orchestrator/status`

### 2. Pipeline Conveyor (`engines/pipeline_orchestrator/conveyor.py`)

Generic queue-based multi-stage runner (generalizes `StreamingTextPipeline`):
- Each stage = worker thread(s) + input queue → output queue
- Worker counts from Resource Planner
- `WorkItem` carries segment payload + stage trace

**Not yet wired** into `_run_pipeline_inner` — that is Phase 2 (see roadmap).

### 3. LLM Orchestrator (`engines/llm_orchestrator/`)

| Module | Role |
|--------|------|
| `model_pool.py` | Discover models, classify tiers (LIGHT / STANDARD / STRONG), track latency |
| `router.py` | Segment difficulty scoring → model tier |
| `orchestrator.py` | Dispatch, backup-on-failure-only, circuit breaker integration |

Integration entry point: `engines/translation_adapt.llm_adapt_segment()`  
Env: `VM_LLM_ORCHESTRATOR=1` (default on), `VM_LLM_ORCH_ALLOW_LIGHT=0` (default — no 3B for dubbing)

### Quality-first routing rules

- Short/simple segments → STANDARD tier (not LIGHT unless `VM_LLM_ORCH_ALLOW_LIGHT=1`)
- Names, abbreviations (USC, Hollywood), idioms, long context → STRONG tier
- Backup model **only** on timeout / empty response — never preemptively
- `circuit_open()` / `can_call_llm()` still gate all calls

## Phased roadmap

### Phase 2 — Wire conveyor into main pipeline
- STT streaming / chunked segments into first queue
- Replace batch translation loop with conveyor stage handlers
- Enable `VM_PIPELINE_CONVEYOR=1` feature flag
- Reuse existing `StreamingTextPipeline` for AI Core block inside conveyor

### Phase 3 — Full stage parallelism
- Whisper segment N+1 while segment N in TTS (requires streaming STT or pre-chunked audio)
- Dynamic back-pressure when LLM is bottleneck
- Idle detection + queue prefetch

### Phase 4 — Multi-host / multi-model scale-out
- Model endpoints as pluggable workers (local Ollama #2, cloud, remote agent)
- LLM Orchestrator pool across endpoints (not competing 70B models on one CPU)
- TTS engine pool

## Configuration

| Variable | Default | Meaning |
|----------|---------|---------|
| `VM_LLM_ORCHESTRATOR` | `1` | Use LLM Orchestrator for `llm_adapt_segment` |
| `VM_LLM_ORCH_ALLOW_LIGHT` | `0` | Allow ≤4B models for simple segments |
| `VM_PIPELINE_LLM_TIMEOUT_SCALE_CPU` | `2.5` | LLM timeout multiplier on CPU-only hosts |
| `OPENAI_API_KEY` | — | Cloud model (STRONG tier, adequate) |

## API

- `GET /api/auto_dub/pipeline_orchestrator/status` — planner + LLM orchestrator stats
- `GET /api/auto_dub/adaptation_capabilities` — includes `recommend_cloud`, `cloud_hint`

## Tests

`tests/test_pipeline_orchestrator.py` — planner, router, conveyor, circuit integration.
