# ADR-005 — Pipeline State Machine

## Status
Accepted (MASTER TZ v3.0 P6; amended Master Spec Part 1 Foundations v6.0)

## Context
Pipeline stages could effectively roll back (e.g. re-translate after lock).
Part 1 defines the canonical forward-only path with Semantic-aware states.

## Decision
Forward-only FSM (Part 1 canonical):

```
NEW → RECOGNIZED → SENTENCE_READY → TRANSLATED → VALIDATED → LOCKED
  → PLANNED → SPEECH_READY → SCHEDULED → MERGED → EXPORTED
```

Legacy aliases (normalized on parse/advance):
- `TRANSCRIBED` → `RECOGNIZED`
- `TTS_READY` → `SPEECH_READY`
- `OPTIMIZED` → `PLANNED`

Optional intermediate: `MERGED → HANDOFF → EXPORTED`.

Allowed skips (still forward):
- `RECOGNIZED → TRANSLATED` (walks `SENTENCE_READY`)
- `LOCKED → SPEECH_READY` (walks `PLANNED`)
- `PLANNED → SCHEDULED` (walks `SPEECH_READY`)

Reverse transitions raise `PipelineStateError`.

## Consequences
Resume/checkpoint logic must advance or restart cleanly — never rewind state.
See ADR-012 and `docs/FOUNDATIONS_PART1.md`.
