# ADR-015 — Decision Policy Engine (Master Spec Part 4 v6.0)

## Status

Accepted

## Context

After Semantic Lock, timing problems must be solved by intelligent audio planning
without changing meaning. Existing `adaptive_planning` used a first-fit ladder
with hardcoded order. Part 4 requires multi-strategy evaluation, configurable
costs/profiles, explainability, rollback, and scene-level planning.

P301 Architecture Review documented why a dedicated engine is required
(`docs/ARCHITECTURE_REVIEW_DECISION_POLICY_P301.md`).

## Decision

1. Package: `engines/decision_policy/`
2. Config: `engines/decision_policy/config/default_policy.json` (costs + profiles + weights)
3. Engine chooses strategies only — never mutates text/WAV/Scheduler/Translation
4. Outputs `DecisionGraph` / `DecisionRecord` for Dub Engine executors
5. Wired into `semantic_v3.phase2` after duration prediction

## Consequences

- Policy profile / cost changes = JSON config (or `VM_DECISION_POLICY_CONFIG`)
- Dub Engine 2.0 (Part 5) executes accepted strategy steps
- Debug Studio consumes `context.decision` / `decision_explain`

## Related

- ADR-012 Foundations
- ADR-013 Semantic Core
- ADR-014 Translation Core Freeze
