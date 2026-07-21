# Dub Engine Overflow Strategy — Implementation Report

**Date:** 2026-07-13  
**Source:** TZ v1.0 + real run `b.json` (task `56125542b5dd40039b031a1a081c29e6`)  
**Scope:** Dub Engine only (Translation Engine unchanged)

## Evidence from b.json

| Signal | Count / note |
|--------|----------------|
| `adaptation_executed=false` | 20/20 |
| Overflow / video_adapt | ~10 segments |
| `LLM_PROVIDER_FATAL` / `no_endpoint` | 8 segments |
| Summary said ADAPTATION EXECUTED while per-seg said NOT | False success narrative |

Root cause: post-LOCK path jumped to `video_adapt` after trim only; overflow registered without adaptation flag; LLM skip continued as SUCCESS.

## Delivered

| TZ # | Fix |
|------|-----|
| 1 | `register_overflow` + slot_fit stamp `adaptation_executed=true` + decision |
| 2 | Variants always include DSAL/rule rewrite when unlocked; locked path still plans + runs audio chain |
| 5–7 | Multi-variant build + cost + quality scores in `overflow_strategy.py` |
| 8 / 10 | Slot-fit runs **tempo before** gap_absorb/video_adapt |
| 6 | Costs aligned: trim1 / tempo2 / stretch4 / borrow6 / merge10 / rewrite20 / manual100 |
| 11 | `overflow_decision` on every overflow segment |
| 13 / Requirements | `assert_pipeline_may_succeed` before StudioReady — hard fail if overflow + no adaptation |
| 14 | Mandatory `adaptation_skip_reason` whenever `adaptation_executed=false` |
| 15 | Decision-chain snapshot + fail message: `OverflowDetected` + `AdaptationSkipped` + `skip_reason` |
| 16 | **Decision Trace** — ordered stages with SUCCESS/FAILED/SKIPPED(reason); OpenDDF section |
| 17 | Mandatory regression: overflow + `adaptation_executed=false` can never be Pipeline SUCCESS |

## Files

- `engines/dub_engine_v2/overflow_strategy.py`
- `engines/dub_engine_v2/adaptation_decision.py`
- `engines/dub_engine_v2/decision_trace.py` (new — full stage chain)
- `engines/pipeline_integrity/overflow_manager.py`
- `engines/closed_loop_timing.py`
- `engines/audio_timing_optimizer.py`
- `engines/ai_adaptation_engine.py`
- `engines/segment_timing_qa.py` (OpenDDF `decision_trace` + summary)
- `engines/decision_policy/config/default_policy.json`
- `api/auto_dub_api.py` (tempo-before-stretch + success gate with skip_reason)
- `tests/test_dub_overflow_strategy_tz.py`
- `docs/adr/ADR-021-dub-overflow-strategy.md`

## Tests

`tests/test_dub_overflow_strategy_tz.py` — includes Decision Trace + mandatory overflow/false SUCCESS regression.

## Remaining (intentional)

Post-LOCK **text** rewrite remains blocked by Translation Lock (ADR / MASTER TZ). Semantic rewrite after LOCK still requires Studio unlock or pre-LOCK DSAL. Audio strategy chain + success gate close the false-SUCCESS / no-adaptation hole from the real run.

When adaptation does not run, OpenDDF must show e.g. `skip_reason=TranslationLocked` / `LLMUnavailableFallbackFailed` / `DecisionEngineReturnedSkip` — never bare `adaptation_executed=false`.

If father/son audio still appears at the wrong timeline after Decision Trace shows a clean adaptation path, audit **Scheduler → Merge → Audio Timeline** next (binding error, not decision).
