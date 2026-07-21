# VideoMonster_V2 — Master Spec Part 1 Foundations (v6.0)

**Status:** Implemented  
**Runtime registry:** `engines/pipeline_integrity/foundations.py`  
**ADR:** [ADR-012](adr/ADR-012-foundations-part1.md)

This document is the authoritative index for Part 1. If a new design contradicts
these rules, the new design is wrong.

---

## Principles

| # | Principle | Meaning |
|---|-----------|---------|
| 1 | Meaning First | Work with meaning, not technical ASR segments |
| 2 | Sentence First | Base unit = Semantic Sentence |
| 3 | Audio First | After translation, optimize sound only |
| 4 | Single Responsibility | One module → one job |
| 5 | Single Owner | One object → one owner |
| 6 | Immutable Contracts | Stage exit freezes data; edits create versions |
| 7 | Deterministic Pipeline | Same inputs → same outputs |
| 8 | Explainability | Every automatic decision is explainable |
| 9 | Architecture before AI | AI must not paper over architecture bugs |

---

## Main Pipeline (no bypass)

```
Video → ASR → Words → Sentences → Meaning → Translation → Validation
  → Semantic Lock → Planning → Dub → Scheduler → Alignment
  → Merge → Studio → Export
```

---

## Absolute Invariants

| ID | Rule |
|----|------|
| I1 | Whisper never owns the Pipeline — recognition only |
| I2 | Translation never knows Scheduler |
| I3 | Scheduler never knows Translation |
| I4 | Dub Engine never knows LLM |
| I5 | TTS never changes text |
| I6 | Merge never changes text |
| I7 | Studio never changes Pipeline |
| I8 | No module mutates foreign objects |

---

## Single Owners

| Object | Owner |
|--------|-------|
| Words | Recognition |
| Sentence | Semantic Layer |
| Translation | Translation Engine |
| Timing | Scheduler |
| Speech | Dub Engine |
| Audio | TTS Engine |
| Merge | Merge Engine |
| Export | Studio |

Owner changes are forbidden.

---

## Versioned Contracts

Stamped via `engines/pipeline_integrity/contract_versions.py`:

| Contract | Owner |
|----------|-------|
| Recognition | Recognition |
| Sentence | Semantic Layer |
| Translation | Translation Engine |
| Dub | Dub Engine |
| Scheduler | Scheduler |
| Alignment | Dub Engine |
| Merge | Merge Engine |
| Studio | Studio |
| TTS | TTS Engine |

Each contract has version, compatibility, description, migration notes
(`contract_catalog()`).

---

## State Machine (forward only)

```
NEW → RECOGNIZED → SENTENCE_READY → TRANSLATED → VALIDATED → LOCKED
  → PLANNED → SPEECH_READY → SCHEDULED → MERGED → EXPORTED
```

Legacy aliases: `TRANSCRIBED→RECOGNIZED`, `TTS_READY→SPEECH_READY`,
`OPTIMIZED→PLANNED`. Optional `HANDOFF` between MERGED and EXPORTED.
Rollback raises `PipelineStateError`.

---

## Entities

Word → SemanticSentence → SpeechUnit → AudioUnit → Timeline

---

## Definition of Done (Part 1)

- [x] Invariants documented (this file + `foundations.py`)
- [x] Owners defined (`SINGLE_OWNERS` + `FIELD_OWNERS`)
- [x] Contracts versioned (full catalog)
- [x] State Machine implemented (`pipeline_state.py`)
- [x] Architecture tests (`tests/test_master_spec_part1_foundations.py`)
- [x] ADR updated (ADR-012, ADR-004/005)

---

## Next

Part 2 — Semantic Core (Words → Sentences → Meaning → Context).
