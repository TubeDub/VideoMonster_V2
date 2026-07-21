# P0 DSAL v4.0 Report — Duration-Semantic Adaptation Layer

**Date:** 2026-07-12  
**Scope:** TZ v4.0 P0 (rule-based DSAL, no LLM hard dependency)  
**Golden reference:** George Lucas en→uk, task `0c5ddd9925f84fbd9a42dfa6f21816eb`

---

## Verdict

P0 delivered: **DSAL works without LLM**. Underflow triggers `expand_required`, rule-based expand/clause restore runs, and `adaptation_executed` can become `true` when text changes. LLM fatal no longer hard-blocks TTS when rule fallback / DSAL already adapted.

---

## Root cause (pre-fix)

| Symptom | Cause |
|--------|--------|
| `adaptation_executed: false` on all 20 segs | `optimize_expand_for_slot` returned `requires_llm_expansion` when LLM off |
| `requires_llm_adaptation: true` + `LLM_PROVIDER_FATAL` | Hard gate treated provider fatal as stop even with usable text |
| Seg #6 ~2999 ms empty | Underflow not expanded; missing EN clause «between father and son» |
| Padding / tempo as only tools | No pre-LOCK duration-semantic text layer |

---

## What shipped (P0)

### New package `engines/dsal/`
- `analyze_duration` — slot vs predicted/actual TTS → band green/yellow/red, `expand_required` / `compress_required`, `duration_match_score`
- `adapt_duration_semantic` — clause restore → synonym expand → elaborations (expand) or rule compress
- `stamp_dsal_on_segment` — writes `expand_required`, `dsal_band`, `dsal_delta_ms`, `dsal_applied`, `adaptation_executed`, trace

### Wiring
1. **`semantic_optimizer.optimize_expand_for_slot`** — LLM off → DSAL rule expand (not `requires_llm_expansion` stop)
2. **`segment_timing_qa.post_tts_validate_and_retry`** — overflow DSAL compress fallback; expand_required stamped; LLM miss = warning
3. **`closed_loop_timing`** — DSAL compress on shrink path; no hard `requires_llm` when LLM unavailable
4. **`translation_validation.apply_dsal_before_lock`** — DSAL pass **before** TRANSLATION LOCK
5. **`ai_adaptation_engine.enforce_adaptation_gate`** — rule fallback / DSAL allows pass even if `provider_fatal`

---

## Acceptance (P0)

| Criterion | Status |
|-----------|--------|
| `adaptation_executed: true` on yellow/red when text changes | ✅ |
| `expand_required: true` when delta > +10% slot | ✅ (`analyze_duration`) |
| LLM off → rule-based works | ✅ |
| #6 clause «between father and son» → «між батьком і сином» | ✅ |
| #6 residual delta &lt; 1500 ms (predicted) or band green/yellow | ✅ (unit) |
| Hard gate not stop on LLM fatal + DSAL | ✅ |

**Tests:** `tests/test_dsal_v4.py` — 6 passed.

---

## Not in P0 (deferred)

| Item | Phase |
|------|-------|
| Full clause coverage engine (#5 dinner/argument) | P1 |
| Block merge 2–3 segs | P1 |
| Golden CI George Lucas 20-seg | P1 |
| SSML duration-aware breaks | P2 |
| LOCK gate: duration_match ≥ 85 + entity | P2 |
| Audio fit ±5% only after LOCK | P2 |
| LLM-enhanced DSAL | P3 |

---

## Sign-off

Awaiting user approval to proceed to **v4.0 P1** (clause restore coverage, block merge, QA metrics, Golden regression CI).
