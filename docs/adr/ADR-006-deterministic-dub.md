# ADR-006 — Deterministic Dub

## Status
Accepted (Freeze TZ P2/P4)

## Context
Identical projects produced different timing outcomes due to random filenames and heuristic order.

## Decision
`AudioTimingOptimizer` applies a fixed level order and exposes a SHA-256 fingerprint of
timing-relevant state. Same input + settings ⇒ same fingerprint and timing fields.

## Consequences
Benchmarks and regression tests can assert fingerprint equality on goldens.
