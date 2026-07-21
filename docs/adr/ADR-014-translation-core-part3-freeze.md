# ADR-014 — Translation Core (Master Spec Part 3 v6.0) + Freeze

## Status

Accepted — **Translation Core FREEZE**

## Context

Part 2 Semantic Core produces `SemanticSentence` units. Part 3 requires a
model-agnostic Translation Core that translates meaning, preserves entities,
supports multi-pass selection, and locks results — without knowing Scheduler,
Dub, TTS, Merge, or Studio.

Legacy `engines/translation_*.py` remains under MASTER TZ TE Freeze for the
array-based path. New Meaning-First work uses `engines/translation_core/`.

## Decision

1. Package: `engines/translation_core/`
2. Contract: `TranslationBackend` (`initialize/translate/health_check/shutdown/capabilities`)
3. Plugins via `register_backend` / `get_backend` (identity, heuristic, mt_bridge + aliases)
4. Path: SemanticSentence → multi-pass → evaluate → validate → Semantic Lock → report
5. Isolation enforced by `invariants.assert_translation_core_isolated`
6. Wired from `engines/semantic_v3/native_translate.py`

## Freeze (P220)

After this ADR, Translation Core is a **stable module**. Changes require:

- dedicated ADR amendment
- dedicated Pull Request
- Unit + Integration + Architecture + Regression tests
- Golden Translation Dataset check (when corpus available)

Env knobs (allowed without unfreeze):

- `VM_TRANSLATION_BACKEND`
- `VM_TRANSLATION_VARIANTS`
- `VM_TRANSLATION_MIN_SIMILARITY`

## Consequences

- New MT/LLM models = new backend plugin only
- Post-lock text mutation is an `ArchitectureViolation`
- Decision Policy Engine (Part 4) consumes Translation Reports / confidence

## Related

- ADR-001 Translation Lock
- ADR-012 Foundations
- ADR-013 Semantic Core
- `docs/TRANSLATION_CORE_PART3_REPORT.md`
- `docs/TRANSLATION_ENGINE_FREEZE_P1.md` (legacy TE)
