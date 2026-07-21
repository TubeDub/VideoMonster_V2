# TPS Phase Report (TPS1–TPS6)

**Date:** 2026-07-17  
**TPS0:** Accepted by customer directive («сделай все»)  
**ADR:** `docs/adr/ADR-023-tps-fast-path-v2.md`

## Delivered

| Phase | Deliverable |
|-------|-------------|
| TPS1 | Fast QA + statuses `PASS / FAIL_RETRY_* / FAIL_LLM_JUDGE / FAIL_MANUAL_REVIEW` |
| TPS2 | Single Owner registry + dual-writer architecture tests; skip DSAL/DubEngine text adapt on TPS |
| TPS3 | `approved_text` + lock; Review/TTS via `final_texts_from_info` |
| TPS4 | Fast → Retry(1) → Judge → Manual wired after MT |
| TPS5 | `tps_metrics.json` per task |
| TPS6 | Tests + ADR-023 + env kill-switch `TPS_ENABLED=0` |

## How to run

Default: TPS on.  
Legacy orchestrator: set `TPS_ENABLED=0`.  
LLM Judge: set `TPS_LLM_JUDGE=1` (optional).

## Definition of Done (checklist)

- [x] Pre-Simplification Audit (ADR-022)
- [x] Modules not deleted without coverage (routed / skipped, not removed)
- [x] Fast→Retry(1)→Judge→Manual contour
- [x] TQE/TPS as decision router
- [x] Single Owner + dual-writer tests
- [x] No dual timing-adapt on TPS happy path (DSAL+DubEngine adapt skipped)
- [x] Single Approved Text
- [x] Performance Dashboard metrics file
- [x] Architecture tests green (see pytest)
- [x] ADR-023 written

Baseline LLM/ms numbers: collect after first production run from `tps_metrics.json`
(`avg_llm_calls_per_segment`, `avg_segment_ms`) vs pre-TPS logs.
