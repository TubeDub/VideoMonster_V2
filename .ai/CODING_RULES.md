# Coding Rules

## Architecture

1. **Core is immutable** — extend via plugins (`plugins/`) and SDK (`sdk/`)
2. **Protected modules** must not be modified when adding features:
   - `core/event_bus.py`, `core/orchestrator.py`, `core/llm_dispatcher.py`
   - `core/pipeline_engine.py`, `core/performance_optimizer.py`, `core/monitoring_center.py`
3. **Wrapper integration** — hook in `event_pipeline.py`, never inside restricted engines
4. **Single chokepoint** for LLM: `engines/translation_adapt.py` → `LLMDispatcher`

## Code Quality

- No fixed performance constants — use Performance Optimizer
- Thread-safe singletons with `threading.RLock()`
- Best-effort probes — never crash on missing `psutil`/`torch`
- User corrections in AI Memory are canonical (locked)

## Testing

- Every new `core/` module needs tests in `tests/`
- Run full suite before merging core changes
- Dispatcher tests isolate semantic cache (`VM_SEMANTIC_CACHE=0`)

## Documentation

- Update `.ai/CHANGELOG.md` for every significant change
- Run `assistant.document()` after architectural changes
- Stage docs live in `docs/` — synced to `.ai/` by DocumentationSync
