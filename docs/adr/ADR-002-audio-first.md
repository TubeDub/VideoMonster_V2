# ADR-002 — Audio First (after Lock)

## Status
Accepted (Freeze TZ P2/P4)

## Context
Text compression was used to fix slot overflow, fighting Translation Engine quality.

## Decision
Pipeline order: Text First → Validation → LOCK → Audio First.
`AudioTimingOptimizer` adjusts only audio/timing (trim, gap, tempo, stretch, crossfade, borrow, overflow).

## Consequences
Overflow is a normal state for Studio. Deterministic audio ladder replaces LLM rewrite post-LOCK.
