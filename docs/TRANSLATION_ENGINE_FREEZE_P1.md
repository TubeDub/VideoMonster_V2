# Translation Engine Freeze — MASTER TZ v3.0 Phase P1

**Status:** ACTIVE  
**Declared:** 2026-07-12  
**Spec:** Master Technical Specification v3.0  

## Rule

The Translation Engine is a **stable module**. Until an explicit unfreeze decision
and a **dedicated PR**, the following must not change:

- prompts
- rewrite / semantic / grammar pipelines
- glossary
- validation logic (translation meaning/entity)
- adaptation (including DSAL rule/LLM enhance) — changes only via exception PR

## Allowed without unfreeze

- Pipeline integrity (LOCK, FSM, contracts, UUID, identity)
- Scheduler / AudioTimingOptimizer / TTS / Merge / Studio ownership fixes
- Diagnostics / OpenDDF flag correctness
- Architecture tests that **forbid** illegal imports/mutations
- Overflow / Underflow managers that **never** rewrite locked text

## Frozen path prefixes (review gate)

- `engines/translation_*.py`
- `engines/dsal/`
- `engines/ai_core/` (translation/grammar/semantic agents)
- `engines/translation_adapt.py`
- `engines/semantic_optimizer.py` (semantic rewrite stages)
- `engines/ai_adaptation_engine.py`

Exception PRs must name `UNFREEZE-TE` in the title and update this file.

## StreamDub

Out of Freeze spine for Master TZ v3.0 convergence work. Do not mix StreamDub
ownership into the Freeze pipeline without a separate ADR.
