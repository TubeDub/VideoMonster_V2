# MASTER TZ v3.0 — P25 / P26 / P27 Report

**Date:** 2026-07-12  
**Trigger:** `STAGE_SNAPSHOT_INTEGRITY` on stage `slot_fit` after audio-identity fixes  

---

## Root cause (not a translation bug)

`begin_stage("slot_fit")` ran, then **AudioTimingOptimizer + UUID lifecycle + identity** mutated segments, and only then `end_stage("slot_fit")` compared snapshots.

So the guard correctly reported: input snapshot ≠ output under `slot_fit` whitelist.

Also forbidden top-level fields were written:
- `slot_fit_error`
- `slot_fit_stretch_only`

(These belong in `timing_meta`, which is already allowlisted.)

---

## Fixes

1. **Stage boundary:** `end_stage("slot_fit")` immediately after slot_fit; new stage `audio_timing` for ATO/UUID.
2. **Diagnostic fields** moved into `timing_meta`.
3. **Post-LOCK:** slot_fit always `skip_text_compression=True` when project locked.
4. **Whitelist:** `audio_timing` stage + overflow/underflow manager fields.

---

## New architecture (as requested)

| Phase | Module | Rule |
|-------|--------|------|
| **P25** | `engines/pipeline_integrity/cow_snapshot.py` | Copy-on-Write working copy; input snapshot frozen |
| **P26** | `engines/pipeline_integrity/segment_transaction.py` | Begin → Validate → Execute → Validate → Commit / Rollback |
| **P27** | `engines/pipeline_integrity/rw_contract.py` | Explicit read/write field sets; illegal write → `ArchitectureViolation` |

Together with LOCK + Immutable Segment + Single Owner + Scheduler, this class of silent segment corruption is prevented at the contract layer, not only detected after the fact.

---

## Tests

`tests/test_master_tz_v3_p25_p27.py`
