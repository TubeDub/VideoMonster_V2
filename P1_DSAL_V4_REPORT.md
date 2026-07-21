# P1 DSAL v4.0 Report — Clause Restore + Block Merge + QA + Golden

**Date:** 2026-07-12  
**Scope:** TZ v4.0 P1  
**Golden:** George Lucas en→uk 20 seg (`tests/golden/dub/george_lucas_en_uk_20.json`)

---

## Verdict

P1 delivered on top of P0: critical EN clauses restore to ≥85% coverage, semantic block merge (2–3 yellow/red segs), false `incomplete_sentence` softened for ASR cuts, Review shows DSAL metrics, Golden CI green with LLM off.

---

## What shipped

### Clause coverage (`engines/dsal/clause_coverage.py`)
- Mapped restore: father/son, every dinner, huge argument, real job, near-death
- `compute_clause_coverage` + `restore_missing_clauses`
- Runs even on green band when coverage &lt; 0.85
- Re-applied after compress so compress cannot drop restored clauses

### Block merge (`engines/dsal/block_merge.py`)
- Detect consecutive yellow/red chains (max 3)
- Adapt combined text to `sum(slot_ms)`
- Redistribute sentences back to members
- Wired into `apply_dsal_before_lock` after per-seg DSAL

### QA
- `verify_meaning_preserved`: no `incomplete_sentence` when source is mid-clause / marked incomplete
- `validate_tts_text(..., is_source_segment_incomplete=)`

### Review
- API rows: `slot_ms`, `dsal_delta_ms`, `dsal_band`, `dsal_applied`, `duration_match_score`, `clause_coverage`, `expand_required`
- Export text + dev UI (`dub.js`) show DSAL line

### Golden CI
- `tests/golden/dub/george_lucas_en_uk_20.json` + manifest entry
- `tests/test_dsal_p1.py` — **7 tests**
- Full suite with P0: **13 passed**

---

## Acceptance

| Criterion | Status |
|-----------|--------|
| #5 dinner / argument restored | ✅ |
| #6 father/son + delta &lt; 15% or &lt;1500ms | ✅ |
| clause_coverage ≥ 0.85 on mapped clauses | ✅ |
| Block merge 2–3 segs | ✅ |
| LLM off Golden 20-seg | ✅ |
| Review DSAL fields | ✅ |
| False incomplete_sentence on ASR cut | ✅ |

---

## Still open (P2 / checklist polish)

| Item | Phase |
|------|-------|
| SSML duration-aware (no mid-name break #11) | P2 |
| LOCK gate: duration_match ≥ 85 + entity | P2 |
| Audio fit ±5% only after LOCK; overlap=0 | P2 |
| #4 `Фіат,.` punctuation polish | P2 / naturalizer |
| #13 duplicate «Південної Каліфорнії» | P2 |
| #16/#20 sentence integrity polish | P2 |

---

## Sign-off

Awaiting approval to proceed to **v4.0 P2** (SSML + LOCK complete + audio-fit).
