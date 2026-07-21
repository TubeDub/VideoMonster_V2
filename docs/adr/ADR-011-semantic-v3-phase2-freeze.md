# ADR-011: Semantic V3 Phase 2 — Native Meaning Pipeline (Architecture Freeze)

**Status:** Accepted  
**Date:** 2026-07-12  
**Phases:** P31–P50  

## Context

Phase 1 introduced Meaning-First units (`SemanticSentence`) but still exported via
`to_pipeline_arrays()` (bridge) into Whisper-shaped segment lists. Translation
still ran on arrays. Whisper remained an implicit segment owner in the AutoDub loop.

## Decision

1. **P31 — No bridge.** Production path uses `run_semantic_v3_phase2()` +
   `phase2_to_orchestrator_arrays()`. Export is derived from Speech/Audio Units,
   not Whisper segments. `to_pipeline_arrays()` is deprecated.
2. **Native TE.** `translate_sentences_native()` accepts only `SemanticSentence`.
   When `VM_SEMANTIC_V3_NATIVE_TE=1` (default), AutoDub sets `skip_translate` and
   preloads `translated_segments` from Phase 2.
3. **Ownership chain:** Word → Sentence → Speech Unit → Audio Unit → Timeline →
   Scheduler. Whisper is ASR + word timestamps only.
4. **Scheduler 2.0** operates only on `AudioUnit`. Overlap raises
   `ArchitectureViolation` (P47 No Double Voice).
5. **P50 Architecture Freeze.** Changes to Sentence Builder, Translation Engine,
   Semantic Lock, Speech Units, Word Model, or Scheduler require: ADR, dedicated PR,
   Regression + Golden Dataset, Architecture Review.

## Consequences

- Feature flag: `VM_SEMANTIC_V3=1` (or `semantic_v3`).
- Native TE: `VM_SEMANTIC_V3_NATIVE_TE=1` (default).
- Max merge: `VM_SEMANTIC_MAX_MERGE` (default 5).
- Phase 1 `run_semantic_v3_from_asr` remains for compatibility tests only.

## Related

- ADR-010 Meaning-First Sentences
- `docs/SEMANTIC_V3_PHASE2_REPORT.md`
- `engines/semantic_v3/phase2.py`
