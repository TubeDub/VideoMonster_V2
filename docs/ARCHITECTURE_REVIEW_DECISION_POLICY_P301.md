# P301 Architecture Review — Decision Policy Engine

**Date:** 2026-07-12  
**Spec:** Master Spec Part 4 v6.0  

## Existing decision-like modules

| Module | Role | Owns data? | Strategy? |
|--------|------|------------|-----------|
| `PipelineIntegrityCoordinator` | Integrity guards / fail-fast | No (validates) | No |
| `engines/semantic_v3/adaptive_planning.py` | First-fit ladder P39–P40 | Writes `adaptive_plan` / `recovery_plan` | Partial — **first found**, costs hardcoded |
| `engines/ai_adaptation_engine.py` | Pre/post TTS text adaptation | Mutates text | Yes — **violates Part 4 post-lock rule** if used after lock |
| `engines/ai_core/quality_agent/decision_engine.py` | ACCEPT/RETRY/FALLBACK | Segment QA | Not dub timing strategy |
| `engines/ai_core/director_agent` | Creative brief | brief only | Not audio planning |
| Translation Core Decision Layer | Chooses translation variant | Translation text | Pre-lock only |

## Duplication / gaps

1. **First-found vs multi-strategy:** `adaptive_planning` walks `DECISION_ORDER` and stops — violates P307.
2. **Costs in code:** ladder order is a Python tuple — violates P305/P306.
3. **No Decision Graph / Explainability / Rollback / Cache** as first-class artifacts.
4. **Scene-level planning** absent (per-sentence only).
5. Expanding `PipelineIntegrityCoordinator` would mix integrity with strategy (SRP violation).
6. Expanding TE `ai_adaptation_engine` would keep text mutation in the decision path (P302/P318).

## Decision

**Create a dedicated package** `engines/decision_policy/` as the **sole strategist** for post–Semantic Lock dub planning.

- Does **not** translate, synthesize, mutate text, or mutate WAV.
- Emits `DecisionRecord` / `DecisionGraph` for Dub Engine / Scheduler to **execute**.
- Reuses Semantic V3 sentence objects as **read inputs**.
- Keeps `adaptive_planning.plan_all` as a thin compatibility shim that can consume Decision Policy output.
- `PipelineIntegrityCoordinator` remains integrity-only (Safety Validator may call into it).

## New agent justification (required by P301)

| Question | Answer |
|----------|--------|
| Can existing module be extended? | Partially — adaptive_planning lacks multi-strategy/cost config/explainability |
| Would extension break SRP? | Yes if stuffed into Integrity Coordinator or TE adaptation |
| Suitable service? | None owns post-lock strategy selection |
| Suitable Coordinator? | Integrity Coordinator is wrong owner |

→ **New Decision Policy Engine is justified.**
