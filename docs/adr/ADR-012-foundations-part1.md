# ADR-012 — Master Spec Part 1 Foundations (v6.0)

## Status

Accepted

## Context

VideoMonster_V2 accumulated Freeze TZ, Master TZ v3, and Semantic V3 layers.
Part 1 Foundations v6.0 is the single source of truth for principles, invariants,
owners, contracts, and the forward-only state machine. Later phases must not
contradict it.

## Decision

1. **Authoritative registry** lives in `engines/pipeline_integrity/foundations.py`
   (principles, invariants I1–I8, single owners, layers, entities, forbidden
   import edges).
2. **FSM** aligns to Spec Part 1:
   `NEW → RECOGNIZED → SENTENCE_READY → TRANSLATED → VALIDATED → LOCKED →
   PLANNED → SPEECH_READY → SCHEDULED → MERGED → EXPORTED`
   with legacy aliases `TRANSCRIBED`, `TTS_READY`, `OPTIMIZED` and optional
   `HANDOFF`.
3. **Contracts** expand to Recognition, Sentence, Translation, Dub, Scheduler,
   Alignment, Merge, Studio (+ TTS). Catalog via `contract_catalog()`.
4. **Documentation index:** `docs/FOUNDATIONS_PART1.md`.

## Consequences

- New modules must answer the Part 1 design checklist (owner, contract, callers,
  reads/writes, errors, tests, ADRs).
- Architecture tests in `tests/test_master_spec_part1_foundations.py` gate CI.
- Semantic V3 remains Meaning-First implementation under these invariants
  (ADR-010/011).

## Related

- ADR-001 Translation Lock
- ADR-002 Audio First
- ADR-003 Scheduler Owner
- ADR-004 Versioned Contracts (amended)
- ADR-005 State Machine (amended)
- ADR-010 / ADR-011 Semantic V3
