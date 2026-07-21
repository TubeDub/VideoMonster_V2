# ADR-017 — Studio QA / Diagnostics / Production Hardening (Master Spec Part 6)

## Status

Accepted

## Context

After Dub Engine 2.0, the product needs full observability: why decisions were
taken, where errors occur, crash resume, release gates, and Studio as a QA
control surface — not only a player UI.

Much of the underlying machinery already existed (`pipeline_integrity`,
`release_governance`, `production_hardening`, Studio API). Part 6 unifies them
under a single Studio QA façade and wires Pipeline → Diagnostics → Metrics →
Runtime Validator → Studio → Review → Export → Release Validation.

## Decision

1. Package: `engines/studio_qa/`
2. Studio 2.0 views: Pipeline / Timeline / Replicas / Review Panel / Decision Graph
3. Diagnostics ZIP (`project.diagnostics.zip`) + crash checkpoint façade
4. Metrics / Health / Error Taxonomy / Final Acceptance
5. Release façade over Architecture Audit, Golden Comparison, Certificate,
   Production Hardening smoke
6. Wired from `semantic_v3.phase2` (`meta.studio_qa`) and `api/studio_api`
   session builder

## Consequences

- Studio session JSON gains `studio_qa` + view mirrors for UI panels
- Part 6 does not duplicate OpenDDF / P16 / P17 — it orchestrates them
- Full Golden Dataset corpus categories (P515) and 24h lab runs (P518) remain
  operational follow-ups; scaffolds and smoke harnesses are in place

## Related

- ADR-016 Dub Engine 2.0
- ADR-015 Decision Policy
- ADR-012 Foundations
- `docs/STUDIO_QA_PART6_REPORT.md`
