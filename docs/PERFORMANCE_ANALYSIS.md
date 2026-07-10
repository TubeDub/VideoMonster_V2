# Performance analysis workflow (TubeDub)

## Golden rule

**Do not change architecture, models, prompts, or translation algorithms until full
performance data is collected and a specific bottleneck is identified.**

Optimizations are allowed only after profiling shows which stage or function
consumes disproportionate time.

## Phase 1 — Diagnostics (current priority)

### Benchmark clip

Standard file: `data/stress_tests/benchmark_video.mp4`

If missing, `engines/benchmark_video.ensure_benchmark_video()` copies the E2E
speech clip (`uploads/test_e2e_speech.mp4`).

### Run benchmark

```bash
python scripts/benchmark_pipeline.py
VM_PERF_DEBUG=1 python scripts/benchmark_pipeline.py
VM_PERF_PROFILE=1 python scripts/benchmark_pipeline.py
```

Every dub run also writes artifacts in `finally` (no extra step needed).

### Artifacts (per task)

| File | Location | Purpose |
|------|----------|---------|
| `performance_report.json` | `output/diagnostics/<task_id>/` | Stage durations, %, bottleneck, LLM stats, CPU/RAM |
| `timeline.json` | same folder | Ordered start/end events, idle gaps |
| `pipeline_timing_<task_id>.json` | `output/` | Live timer snapshot |
| `pipeline_performance.json` | diagnostics folder | Segment-level averages / slow segments |
| `cprofile_<task_id>.prof` | `output/dev/` | Function-level profile (benchmark + `VM_PERF_PROFILE=1`) |

### Environment flags

| Variable | Effect |
|----------|--------|
| `VM_PERF_DEBUG=1` | Extra fields in `performance_report.json` (dev only, not shown in user UI) |
| `VM_PERF_PROFILE=1` | cProfile during benchmark run |
| `VM_DEBUG_MODE=1` | Existing debug learning mode (translation stage logs) |

### Bottleneck rule (TZ §3)

If one stage is **>50%** of total time, `performance_report.json` sets:

```json
"bottleneck": {
  "stage": "tts",
  "percent_of_total": 52.3,
  "exceeds_threshold": true
}
```

**Next step:** profile that stage with cProfile / py-spy / scalene — not random code changes.

### Profiling tools

1. **Built-in:** `VM_PERF_PROFILE=1` + `scripts/benchmark_pipeline.py`
2. **External (recommended for deep dives):**
   - `py-spy record -o profile.svg -- python run.py`
   - `scalene scripts/benchmark_pipeline.py`

## Phase 2 — Fix causes (after data)

Only after identifying the hot stage/function:

- Remove redundant LLM client re-initialization
- Remove unjustified `time.sleep` delays
- Reduce redundant disk I/O
- Apply targeted fix to the measured bottleneck

## Phase 3 — Resilience (after fix)

Use existing `engines/pipeline_orchestrator/stage_retry.py` (`run_with_retry`) for
transient API failures. Add jitter when extending retry policy.

## Version comparison

Keep `performance_report.json` + `timeline.json` from each release and compare
`stages_sec` / `bottleneck` side by side — this is the traceability requirement
from the TZ.
