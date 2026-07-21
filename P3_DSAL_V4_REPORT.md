# P3 DSAL v4.0 Report — LLM-enhanced Duration Adaptation

**Date:** 2026-07-12  
**Scope:** TZ v4.0 P3 (optional LLM polish after rules)

---

## Verdict

LLM is an **enhancement after rule-based DSAL**, never a hard dependency. When the provider is available and the segment is still yellow/red, one expand/compress LLM pass runs with meaning + clause re-check.

---

## What shipped

| Piece | Detail |
|-------|--------|
| `engines/dsal/llm_enhance.py` | `llm_enhance_duration()` — soft LLM polish |
| `adapt_duration_semantic(..., allow_llm=True)` | Rules first, then optional LLM |
| `apply_dsal_before_lock` | Calls with `allow_llm=True` |
| `optimize_expand_for_slot` | **DSAL first**, then LLM if still short |

Failure modes (keep rule text):
- LLM unavailable / error
- meaning rejected
- foreign script
- no change from model

---

## Acceptance

| Criterion | Status |
|-----------|--------|
| Works with LLM off | ✅ (P0/P1 unchanged) |
| LLM after rules when available | ✅ |
| No hard stop on LLM fatal | ✅ |
| Avg duration_match > 90 | best-effort (needs live LLM run) |

---

## Tests

`tests/test_dsal_p3_p4.py` (LLM cases) — covered in combined suite **25 passed**.
