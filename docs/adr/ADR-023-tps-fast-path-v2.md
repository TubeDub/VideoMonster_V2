# ADR-023 — Translation Pipeline Simplification (TPS) / Fast Path v2

**Status:** Accepted (implemented TPS1–TPS6)  
**Date:** 2026-07-17  
**Depends on:** ADR-022 (TPS0 audit)

## Context

Too many modules rewrote the same translation. TPS0 documented duplicate writers
(especially TimingAgent + DSAL + DubbingEngine). Customer requested full TPS1–TPS6.

## Decision

### Contour
```
MT → Naturalizer(rule-first) → Fast QA
  PASS → TQE approve → approved_text → TTS
  FAIL → Meaning/Grammar Retry (1) → Fast QA
           PASS → approve → TTS
           FAIL → LLM Judge (optional, env TPS_LLM_JUDGE) → Fast QA
                    PASS → approve → TTS
                    FAIL → Manual Review
```

### Packages
- `engines/tps/` — Fast QA, owners, approved_text, pipeline, metrics
- Wired in `api/auto_dub_api.py` after MT (default `TPS_ENABLED=1`)
- Orchestrator Semantic/Timing/Grammar **skipped** on TPS success
- DSAL pre-LOCK text adapt **skipped** when `skip_dsal_pre_lock`
- DubbingEngine text adapt **skipped** when TPS / `skip_text_adaptation`
- `final_texts_from_info` prefers `approved_text`

### Single Owner
| Operation | Owner |
|-----------|--------|
| mt_raw | MTEngine |
| naturalize | Naturalizer |
| semantic_rewrite | SemanticRewriteOwner |
| grammar_rewrite | GrammarRewriteOwner |
| timing_text_adapt | TimingMeaningFitOwner |
| final_approve | TQE |
| approved_text | ApprovedTextAPI |

Architecture tests fail on dual writers.

### Single Approved Text
After PASS: `approved_text` + `translation_locked`.  
Post-pass meaning rewrite raises `ApprovedTextMutationError`.  
Cosmetics (SSML/stress) remain TTS-layer only.

### Metrics
`output/sessions/{task_id}/tps_metrics.json` with Fast/Retry/Judge/Manual counts,
latency, LLM calls/segment, reject histogram, dual_writer_violations,
approved_text_mutation_attempts.

## Consequences

- Faster happy path (no always-on agent stack)
- Modules kept as capabilities; routed by TQE/TPS, not deleted
- After APPROVED: duration-only DSAL stamp (`stamp_duration_after_approved`) —
  yellow/red get timing metadata; text stays immutable
- Legacy pre-TTS TQE hard gate skipped when `info["tps"]`
- LLM Judge default ON (`TPS_LLM_JUDGE=0` to disable)
- MT translate + Marian caches keyed by `TPS_PIPELINE_VERSION` (`engines/tps/version.py`)
- Manual Review UI surfaces `needs_manual_review`; edits re-approve `approved_text`
- Monitoring Center shows TPS metrics via `/api/tps/metrics`

## Tests
`tests/test_tps_pipeline.py` covers TZ Part 13 minimum cases.
