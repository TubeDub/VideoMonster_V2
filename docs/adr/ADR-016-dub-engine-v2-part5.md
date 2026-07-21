# ADR-016 — Dub Engine 2.0 (Master Spec Part 5 v6.0)

## Status

Accepted

## Context

After Semantic Lock and Decision Policy, text is immutable. Dub Engine must
plan audio, own timing via Scheduler, prevent overlap/tail-spill, and prepare
lip-sync data — without knowing Translation internals.

## Decision

1. Package: `engines/dub_engine_v2/`
2. Flow: Audio Planning → Phoneme Duration Predict → ATO (fixed order) →
   Scheduler 2.0 → Detectors → LipSync Foundation → Audio QA → Metrics
3. SpeechUnitV2 / AudioUnitV2 / ProjectTimeline are first-class
4. Overflow/Underflow escalate to Decision Policy (no text rewrite)
5. Wired from `semantic_v3.phase2` after Decision Policy

## Consequences

- Scheduler API (`update_audio_time`) is the only timing mutator for AudioUnits
- Golden Audio corpus expansion is a follow-up (P418)
- Part 6 Studio/Diagnostics consumes `audio_metrics` + timeline

## Related

- ADR-003 Scheduler Owner
- ADR-015 Decision Policy
- ADR-014 Translation Core Freeze
- `docs/DUB_ENGINE_V2_PART5_REPORT.md`
