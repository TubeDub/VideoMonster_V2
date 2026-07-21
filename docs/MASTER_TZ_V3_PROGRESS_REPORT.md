# MASTER TECHNICAL SPECIFICATION v3.0 — Progress Report

**Date:** 2026-07-12  
**Customer directive:** «сделай все» (proceed all phases with TZ defaults)  
**Defaults applied:** Translation Freeze ON · StreamDub out of spine · Identity hard-fail in production handoff  

---

## Phase status

| Phase | Status | Deliverable |
|-------|--------|-------------|
| P0 Inventory | ✅ | `docs/ARCHITECTURE_REPORT_MASTER_TZ_V3_P0.md` |
| P1 Translation Freeze | ✅ | `docs/TRANSLATION_ENGINE_FREEZE_P1.md` |
| P2 LOCK harden | ✅ | Lock gate on legacy `post_tts_validate_and_retry`; `ArchitectureViolation` |
| P3 Immutable Segment | ✅ | Existing whitelist + locked rewrite blocked |
| P4 Single Owner | ✅ partial | Studio timing via Scheduler when `segment_id` present |
| P5 Contracts | ✅ existing v1 | Unchanged (frozen versions) |
| P6 FSM | ✅ | `OPTIMIZED` state + ADR-005 + ATO advances OPTIMIZED |
| P7 Dub isolation | ✅ existing | Package import lint unchanged; TE freeze protects soft coupling |
| P8 Scheduler | ✅ partial | Studio edit path uses `update_time` |
| P9 AudioTimingOptimizer | ✅ | Overflow/Underflow managers wired into ladder |
| P10 Overflow Manager | ✅ | `engines/pipeline_integrity/overflow_manager.py` |
| P11 Underflow Manager | ✅ | `engines/pipeline_integrity/underflow_manager.py` |
| P12 Smart Adaptation | ⏸ Freeze | Deferred (TE Freeze — no DSAL/prompt changes) |
| P13 Quality Evaluator | ⏸ | Existing scores; full evaluator later |
| P14 UUID / Identity | ✅ | Handoff `hard_fail=True` in auto_dub |
| P15 Runtime Validator | ✅ existing | — |
| P16 Recovery | ✅ existing | — |
| P17 Diagnostics | ✅ existing | Flag propagation improved earlier (DSAL max) |
| P18 Benchmark | ⏸ | Scale gates not run in this pass |
| P19 Golden | ⏸ | Existing golden DSAL; full golden gate later |
| P20 Architecture tests | ✅ | `tests/test_master_tz_v3_architecture.py` |
| P21 Perf budget | ⏸ | `measure_budget` exists; hard enforce later |
| P22 Plugin SDK | ⏸ stub | — |
| P23 Release governance | ✅ partial | Reports + ADR |
| P24 Live defects | ✅ partial | Roots addressed (lock silent rewrite, overflow state, identity hard-fail, FSM, Studio owner) |

---

## Root causes closed in this pass

1. Legacy post-TTS text rewrite after LOCK → **blocked** + Overflow/Underflow managers  
2. Missing `OPTIMIZED` FSM → **added**  
3. Missing `ArchitectureViolation` → **added**  
4. Studio direct `start_ms` writes on edit API → **Scheduler**  
5. Identity silent repair at handoff → **hard-fail** in production path  
6. Overflow treated as text-fixable error → **pipeline state with recovery_plan**  

## Explicitly not changed (P1 Freeze)

- Translation prompts / DSAL / semantic optimizer rewrite logic  
- StreamDub convergence  

## Remaining DoD items (next iterations)

- Full Studio split/merge paths still need Scheduler for all timing writes  
- Eliminate all residual direct `start_ms` assigns (conflict_resolver intermediates)  
- P12 multi-variant selection (requires UNFREEZE-TE PR)  
- P18/P19/P21/P22 hard CI gates  
- Live re-run on `т.json` task to prove zero overlap / correct adaptation flags  

## Tests

```
pytest tests/test_master_tz_v3_architecture.py tests/test_translation_lock_p0.py -q
```
