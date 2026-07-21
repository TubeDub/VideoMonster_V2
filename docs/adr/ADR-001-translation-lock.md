# ADR-001 — Translation Lock

## Status
Accepted (Freeze TZ P0/P4)

## Context
Post-validation text was still rewritten by closed-loop / adaptation agents, causing
quality regressions and non-deterministic dubs.

## Decision
After Translation Validation, apply TRANSLATION LOCK. Locked text fields are immutable.
Mutations raise `TranslationLockError` (no silent fix).

## Consequences
Dub Engine is audio/time only after LOCK. Timing overflow becomes Studio-visible, not text rewrite.
