# ADR-003 — Scheduler Owner of Timing

## Status
Accepted (Freeze TZ P1/P4)

## Context
Many modules wrote `start_ms`/`end_ms` directly, breaking Single Owner and architecture rules.

## Decision
Scheduler API (`update_time` / `request_time`) is the sole post-LOCK timing mutator.
Direct assignment outside Scheduler fails architecture tests.

## Consequences
Conflict resolver, slot-fit, and optimizer route timing through Scheduler.
