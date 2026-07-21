# Translation Recovery Hotfix (TRH) v1.0 — Phase Report

**Date:** 2026-07-17  
**Priority:** P0  
**Status:** Implemented

## Root cause (not model quality)

On `translation_agent` path, Naturalizer **did** run inside TPS, but Review audits kept:
- `naturalized_text = raw_mt`
- `route = direct`
- `naturalizer_executed = False`

So the pipeline looked broken while Final could already be polished.

## Fixes (BUG1–10)

| Bug | Fix |
|-----|-----|
| 1 Naturalizer no-op UI | `sync_audits_trh` writes Nat ≠ Raw; meta after TPS |
| 2 Weak Raw MT | Dirty detector + calque repair + retry re-polish |
| 3 Entities | Project glossary + temporary repair TODOs |
| 4 Gate / Route direct | Route from `tps_path`; dirty blocks silent direct |
| 5 DSAL | `dsal_skip_reason=tps_duration_only_no_text_rewrite` |
| 6 Oversized | Existing `oversized_guard` (HF1) |
| 7 DirtyMT | `engines/mt/dirty_mt.py` + Fast QA `dirty_mt_noop` |
| 8 Explainability | `trh` per segment + `trh_segment_trace.json` |
| 9 Change control | Raw → Naturalized → Retry → Judge → Approved in TRH |
| 10 Cleanup junk | `cleanup.log` in session + `output/logs` |

## Packages

- `engines/trh/` — audit trail + segment_trace
- `engines/tps/pipeline.py` — naturalizer meta, dirty retry, TRH stamp
- `engines/cleanup_engine/` — cleanup.log

## Tests

```bash
python -m pytest tests/test_trh_recovery.py tests/test_hotfix_gl_mt_nat_tqe.py tests/test_tps_pipeline.py -q
```
