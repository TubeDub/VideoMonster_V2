# P5 DSAL v4.0 Report — Benchmark + Governance

**Date:** 2026-07-12  
**Scope:** TZ v4.0 P5 (George Lucas benchmark + Release Certificate)

---

## Verdict

P5 delivered: LLM-off George Lucas 20-seg DSAL benchmark is a **blocking** section of `make certify` / P17 certificate. Core DoD gates pass; stretch avg>90 remains informational until live TTS durations fill the golden.

---

## What shipped

| Piece | Path |
|-------|------|
| Benchmark engine | `engines/dsal/benchmark.py` → `run_dsal_benchmark()` |
| CLI | `scripts/run_p5_dsal_benchmark.py` |
| Make | `make dsal-bench` |
| Certificate | `issue_release_certificate` → section `dsal_benchmark` |
| Report JSON | `output/p5_dsal_benchmark/dsal_benchmark_report.json` |
| Tests | `tests/test_dsal_p5.py` |

Also: `apply_dsal_before_lock(..., allow_llm=, block_merge=)` for clean baseline; lock-gate clause check only when critical EN clauses exist.

---

## Gates

| Gate | Blocking | Latest run |
|------|----------|------------|
| `llm_off_ok` | yes | PASS |
| `seg6_underflow_fixed` (Δ≤15%) | yes | PASS (2.54%, score 100) |
| `clause_coverage_critical` ≥0.85 | yes | PASS (1.0) |
| `must_restore` | yes | PASS |
| `measured_avg_match` | soft | PASS if seg6 ok |
| `stretch_avg_gt_90` | no | FAIL (estimate vs slot; needs LLM/TTS) |

---

## Commands

```bash
make dsal-bench
make certify
```

---

## v4.0 complete

| Phase | Status |
|-------|--------|
| P0 rule DSAL | ✅ |
| P1 clause + block merge + Golden | ✅ |
| P2 SSML + LOCK + audio ±5% | ✅ |
| P3 LLM enhance | ✅ |
| P4 Studio editorial | ✅ |
| P5 Benchmark + governance | ✅ |

**MVP + optional P3–P5 closed.** Next: live George Lucas re-dub on task `0c5ddd…` for production validation.
