# Performance Optimizer + Hardware Profiler — Stage 7 (TZ #7)

## Goal

Automatically analyse the user's hardware, build a performance profile, and tune
every VideoMonster component for maximum throughput **without degrading dubbing
quality**.

**Principle:** The user never configures performance manually. VideoMonster
detects capabilities and uses them optimally.

## Modules

| Module | Role |
|--------|------|
| `core/hardware_profiler.py` | Detect CPU / RAM / GPU / disk / OS (§1) |
| `core/benchmark.py` | Sub-60s micro-benchmark (§2) |
| `core/performance_optimizer.py` | Tier selection, resource plan, pressure, learning (§3–§11, §13) |
| `core/performance_monitor.py` | Live CPU/GPU/RAM/VRAM/queue telemetry (§12) |
| `data/performance/performance.db` | SQLite store: profile, benchmark, history (§9) |

## Architecture

```
First run / API init
  HardwareProfiler.profile()     → CPU, RAM, GPU, disk, OS
  BenchmarkEngine.run()          → whisper/llm/tts/disk/audio/mix scores
  PerformanceOptimizer.initialize()
    → tier: Ultra | High | Balanced | Light | Minimal
    → ResourcePlan (workers, chunks, queues, devices per stage)
    → saved to performance.db

During processing
  PerformanceMonitor.sample()    → live metrics every 2s
  Optimizer.evaluate_pressure()  → RAM/thermal safeguards (§6–§7)
  Optimizer.predict_pressure()   → proactive load reduction (§13)
  Optimizer.detect_bottleneck()  → rebalance slowest stage (§10)

After film
  event_pipeline wrapper         → record_film() self-learning (§8)
```

## Hardware Profiler (§1)

Detects automatically on first call to `get_hardware_profile()`:

- CPU model, physical/logical cores, frequency
- RAM total/available, speed (best-effort)
- GPU model, VRAM, CUDA / Metal / ROCm
- Disk type (SSD/HDD/NVMe), free space
- OS name and version

All probes degrade gracefully without `psutil` or `torch`.

## Benchmark Engine (§2)

Runs seven lightweight micro-workloads in **<60 seconds**:

| Category | Proxy workload |
|----------|----------------|
| whisper | FP math grind |
| llm | String/dict churn |
| tts | Sine wave generation |
| disk_write | Sequential 16 MB write + fsync |
| disk_read | Sequential read |
| audio | FIR-style convolution |
| mix | Packed float summation |

Scores are normalized (100 = reference host). Real Whisper/LLM/TTS engines are
**not** invoked — benchmark measures host capability only.

## Hardware Tiers (§3)

| Tier | Typical host |
|------|-------------|
| Ultra | 16+ cores, 32+ GB RAM, 16+ GB VRAM, score ≥120 |
| High | 8+ cores, 16+ GB RAM, 8+ GB VRAM |
| Balanced | 8+ cores, 16+ GB RAM |
| Light | 4+ cores, 8+ GB RAM |
| Minimal | everything else |

Tier is selected automatically — never set by the user.

## Dynamic Resource Plan (§4–§5)

`ResourcePlan` contains per-stage:

- `device` — `cpu` | `gpu` | `hybrid` (automatic per stage)
- `workers`, `chunk_size`, `max_in_flight`, `queue_size`
- `memory_limit_mb`

Global: `max_concurrent_tasks`, `cpu_workers`, `gpu_workers`, RAM/VRAM budgets.

### Automatic device selection (§5)

| Stage | Default device (GPU present) |
|-------|------------------------------|
| whisper | GPU |
| tts | GPU (VRAM ≥6 GB) else hybrid |
| mix | hybrid (VRAM ≥4 GB) else CPU |
| translation / ai_adaptation | CPU |
| cleaner / timing / export | CPU |

Override per stage: `VM_DEVICE_WHISPER=cpu`

## User Modes (§11)

Modes change **only parallelism and resource usage** — never model quality:

| Mode | Effect |
|------|--------|
| `max_quality` | Lower concurrency (×0.6) — more headroom per task |
| `balanced` | Default (×1.0) |
| `max_performance` | Higher concurrency (×1.35) |

Set via `VM_PERF_MODE` or `POST /api/pipeline/performance/mode`.

## Thermal & Memory Safeguards (§6–§7)

`evaluate_pressure()` monitors:

- RAM ≥80% → warning; ≥92% → critical
- VRAM ≥82% → warning
- CPU/GPU temp ≥82°C → warning; ≥90°C → critical

`apply_pressure()` responds by:

- Reducing chunk sizes (×0.7)
- Reducing concurrency (−1 worker)
- Increasing queue wait capacity
- **Never** aborting the film

## Self-Learning (§8)

After each film, `record_film()` stores averaged metrics in `performance_history`:

- avg CPU/GPU/RAM/VRAM %
- processing speed, chunk count, segment count

Next `initialize()` reads history and proactively shrinks chunks if prior runs
ran hot on RAM.

## Bottleneck Detection (§10)

`detect_bottleneck(stage_durations)` finds the slowest stage.
`rebalance_for_bottleneck()` gives it +1 worker (within core cap).

## Load Prediction (§13)

`predict_pressure(samples)` extrapolates RAM trend from recent monitor samples
and recommends pre-emptive `reduce_chunk_size` before pressure hits.

## Performance Monitor (§12)

`PerformanceMonitor` samples every 2 seconds:

- CPU / GPU / RAM / VRAM %
- CPU / GPU temperature (best-effort)
- processing speed (items/sec)
- queue depth, active agents, chunk size (via `set_pipeline_provider()`)

Data is available to the AI Orchestrator via `get_status()` / `averages()`.

## HTTP Endpoints

```
GET  /api/pipeline/performance/status?init=1
POST /api/pipeline/performance/mode        {"mode": "balanced"}
GET  /api/pipeline/performance/monitor?sample=1
POST /api/pipeline/performance/benchmark?quick=1
```

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `VM_PERF_OPTIMIZER` | `1` | Enable Performance Optimizer |
| `VM_PERF_MONITOR` | `1` | Enable live monitor |
| `VM_BENCHMARK` | `1` | Enable benchmark on init |
| `VM_PERF_MODE` | `balanced` | User performance mode |
| `VM_PERF_DIR` | `data/performance` | SQLite DB directory |
| `VM_DEVICE_{STAGE}` | — | Override device per stage |

## What is NOT changed (TZ constraint)

- Translation quality / model selection
- Event Bus, AI Orchestrator, LLM Dispatcher, Pipeline Engine core
- Processing algorithms (Whisper, TTS, Mix, Cleaner, Timing)
- User interface

Only the performance management layer is added.

## Tests

```bash
python -m pytest tests/test_performance.py -q
```

Coverage: hardware detection, benchmark budget, tier selection, resource plan,
device selection, user modes, performance DB, pressure/prediction, bottleneck
rebalance, self-learning, live monitor.
