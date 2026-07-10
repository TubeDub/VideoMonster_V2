# Platform Memory

## Translation Consistency

- AI Memory stores characters, glossary, style, voices per project
- Semantic Cache prevents redundant LLM calls
- Cross-episode memory via `global_memory.db` + `series_id`

## Performance Memory

- `performance.db` — hardware profile, benchmark, run history
- `analytics.db` — project run metrics
- `development_history.db` — architectural changes

## Knowledge Base

- `data/knowledge/knowledge_base.db` — best practices, lessons
- Populated by self-diagnostics and developer input
- All AI tools query via `get_knowledge_base()`

## Semantic Fingerprints

Each segment gets a hash — near-identical meaning skips LLM.

*Auto-updated by AI Memory `learn()` after each film.*
