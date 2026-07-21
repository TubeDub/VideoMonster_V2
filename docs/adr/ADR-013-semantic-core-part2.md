# ADR-013 — Master Spec Part 2 Semantic Core (v6.0)

## Status

Accepted

## Context

Part 1 Foundations established Meaning First / Sentence First. Part 2 defines the
Semantic Core so Whisper is only an ASR + word-timestamp source, never the owner
of dub structure.

## Decision

1. Package path: `engines/semantic_v3/` modules:
   - `word_model.py` (P101), `word_graph.py` (P102)
   - `sentence_builder.py` + `boundary_optimizer.py` (P103–P104)
   - `sentence_confidence.py` (P106)
   - `semantic_graph.py` (P107), `entity_graph.py` (P108)
   - `dialogue_engine.py` (P109), `conversation_memory.py` (P110)
   - `scene_context.py` (P111)
   - `emotion_engine.py` (P112), `style_engine.py` (P113)
   - `semantic_validator.py` (P115), `lock_preparation.py` (P116)
   - Orchestrator: `semantic_core.py` (`run_semantic_core`)
2. Phase 2 pipeline (`phase2.py`) runs Semantic Core **before** Translation.
3. P117: only `SemanticSentence` is a valid meaning unit.
4. Conversation Memory is project-scoped and cleared via `clear_project_memory`.

## Consequences

- Translation Core (Part 3) consumes Semantic Core output only.
- Golden Dataset expansion (P119) remains a follow-up corpus task; unit suite
  covers P118 engines.

## Related

- ADR-010 / ADR-011 Semantic V3
- ADR-012 Foundations Part 1
- `docs/SEMANTIC_CORE_PART2_REPORT.md`
