# AI Memory + Semantic Cache — Stage 6 (TZ #6)

## Goal

Build an intelligent project memory that ensures **translation consistency**,
**reuse of prior work**, and **gradual knowledge accumulation** without
degrading quality.

**Principle:** Never repeat heavy work when a correct result already exists.

## Modules

| Module | Role |
|--------|------|
| `core/ai_memory.py` | Central memory API — characters, glossary, style, voices, film context |
| `core/semantic_cache.py` | Pre-LLM cache — exact fingerprint + fuzzy semantic search |
| `core/llm_dispatcher.py` | Integration chokepoint — cache lookup/store + memory context injection |
| `core/pipeline_engine.py` | Post-film `memory.learn()` from segments, audits, corrections |

## Architecture

```
LLM request (translation_adapt → LLMDispatcher.execute_chat)
  │
  ├─ SemanticCache.lookup()     → hit? return immediately (no LLM)
  ├─ AIMemory.build_context_prompt() → inject into system prompt
  ├─ adapter.generate()         → existing LLM transport
  └─ SemanticCache.store()      → save successful result

Pipeline completion (PipelineEngine.run)
  └─ AIMemory.learn(job_data)   → glossary, cache, user corrections, voices
```

## AI Memory API (§14)

All memory access goes through `get_memory(project_id)`:

| Method | Purpose |
|--------|---------|
| `find(key, category=...)` | Lookup entry by key |
| `save(MemoryEntry)` | Store entry; locked entries cannot be overwritten |
| `update(key, value, ...)` | Update or create; `user_correction=True` locks as canonical |
| `learn(job_data)` | Auto-learn from completed film (§10) |
| `search(query, category=...)` | Text search across dictionaries |
| `get_character(name)` | Character translation + metadata |
| `get_glossary()` | Terminology list |
| `get_style()` | Style matrix (tone, formality, humour, etc.) |
| `get_voice(name)` | Voice profile (timbre, pitch, model) |
| `lookup_translation(...)` | Delegate to semantic cache before LLM |
| `store_translation(...)` | Store translation in semantic cache |
| `build_context_prompt()` | Assemble memory context for LLM system prompt |
| `apply_glossary(text)` | Replace known terms in text |
| `check_consistency(...)` | Detect contradictory translations (§13) |

## Dictionaries (§3)

Automatically maintained per project:

- **Character Dictionary** — `Luke Skywalker` → `Люк Скайуокер`
- **Location Dictionary**
- **Brand Dictionary**
- **Glossary**
- **Style Matrix** — official / informal tone, humour, sarcasm, etc.
- **Voice Profiles** — timbre, pitch, emotion, voice model

User corrections (`locked=1`) are **canonical** — cannot be overwritten (§11).

## Storage (§7–§8)

| Store | Path | Scope |
|-------|------|-------|
| Project DB | `data/memory/project_{id}.db` | Single film/episode |
| Project JSON | `data/memory/project_{id}_memory.json` | Snapshot export |
| Global DB | `data/memory/global_memory.db` | Cross-episode series memory |
| Semantic cache | `data/memory/semantic_cache.db` | Translation fingerprints |

Override directory: `VM_MEMORY_DIR=/path/to/memory`

Cross-project memory: save with `global_memory=True` and shared `series_id`.

## Semantic Cache (§2, §9, §12)

Before every LLM call:

1. **Exact hit** — SHA-256 fingerprint of normalised text + langs + context + task_type
2. **Fuzzy hit** — Jaccard token overlap ≥ threshold (default 0.85)

On hit → return cached result, **LLM call forbidden**.

Each segment gets a semantic fingerprint; near-identical meaning reuses prior work.

## Integration points

### LLM Dispatcher (`execute_chat`)

- Cache lookup **before** model selection
- AI Memory context injected into system prompt when `task_id` is set
- Cache store **after** successful response
- Meta fields: `cache_hit`, `cache_source`, `cache_similarity`

### Pipeline Engine (`run`)

At job completion, calls `mem.learn()` with:
- `segments`, `source_segments`
- `translation_audits`
- `user_corrections`
- `voice_profiles`

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `VM_AI_MEMORY` | `1` | Enable AI Memory Engine |
| `VM_SEMANTIC_CACHE` | `1` | Enable semantic cache |
| `VM_MEMORY_DIR` | `data/memory` | Storage directory |

## HTTP

```
GET /api/pipeline/memory/status?project_id=<id>
```

Returns character/glossary/style counts, cache stats, DB paths.

## Quality guarantees (§13)

- Locked entries prevent contradictory re-translations
- `check_consistency()` flags divergent name/term usage
- Cache never bypasses user-locked glossary entries
- Memory context enriches LLM prompts without changing translation algorithms

## What is NOT changed (TZ constraint)

- Event Bus, AI Orchestrator, Pipeline Engine core logic
- LLM Dispatcher routing / failover
- Translation, TTS, Cleaner, Timing algorithms
- User interface

Only the memory layer and semantic cache are added.

## Tests

```bash
python -m pytest tests/test_ai_memory.py -q
```

Coverage: fingerprint stability, exact/fuzzy cache, character/glossary/style/voice
storage, locked entries, cross-project global memory, `learn()`, context prompt,
consistency checks.
