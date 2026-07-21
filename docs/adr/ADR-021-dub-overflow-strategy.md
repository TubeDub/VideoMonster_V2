# ADR-021: Dub Engine Overflow Strategy Chain

## Status
Accepted — 2026-07-13 (amended: mandatory `skip_reason`)

## Context
Real OpenDDF runs (`b.json`, task `56125542…`) showed:
- `adaptation_executed=false` while overflow / `video_adapt` occurred
- `LLM_PROVIDER_FATAL` / `no_endpoint` then silent continue
- Video stretch chosen before tempo
- Pipeline could reach SUCCESS with untreated overflow
- Diagnostics only said `ADAPTATION NOT EXECUTED` with no root-cause code

Translation quality was not the root cause; post-TTS Dub Engine path was.

## Decision
1. Introduce `engines/dub_engine_v2/overflow_strategy.py` as the single costed strategy planner.
2. Strict order: trim → pause → tempo → stretch → borrow → merge → rewrite → manual.
3. Costs: trim=1, pause/tempo=2, stretch=4, borrow=6, merge=10, rewrite=20, manual=100.
4. Every `register_overflow` stamps a decision and sets `adaptation_executed=true` when the planner succeeds.
5. Slot-fit must run tempo before `video_adapt` / `gap_absorb`.
6. Pipeline SUCCESS is forbidden when overflow remains with `adaptation_executed=false`.
7. **Mandatory skip_reason:** `adaptation_executed=false` is illegal without `adaptation_skip_reason`
   (canonical codes in `engines/dub_engine_v2/adaptation_decision.py`).
8. Decision-chain snapshot (`adaptation_decision`) must record overflow/underflow, locks,
   LLM/rule/semantic flags, decision, executed, and skip_reason.
9. Fail report must include: `OverflowDetected` + `AdaptationSkipped` + `skip_reason=…`.
10. **Decision Trace** (`decision_trace` / OpenDDF section): ordered stages
    NeedAdaptation → DecisionEngine → Rule/Semantic → ChosenStrategy → StrategyResult →
    TTS → Scheduler → FinalResult. Each stage ends SUCCESS | FAILED | SKIPPED(reason).
    Silent outcomes are illegal. Strategy `why` is required when a strategy is chosen.
11. Regression test forbids future return of overflow + `adaptation_executed=false` + SUCCESS.

## Consequences
- Locked text still cannot be rewritten post-LOCK; audio chain + decision log run instead; rewrite reserved for unlock / studio.
- Automatic mode still degrades when LLM missing, but Rule/DSAL variants are always planned and audio stages always execute.
- Existing Translation Engine contracts unchanged.
- OpenDDF / Studio diagnostics surface `adaptation_skip_reason` so root cause is visible in one run.
