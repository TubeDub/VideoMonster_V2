# STAGE13 — Job translate cache bypass fix

**Дата:** 2026-07-29  
**Проблема:** Simple брал `job_cache` (pipeline translate) → #5/#6/#9 обрезаны; Stage 12b на `cache/mt` не срабатывал.

## Фикс

`engines/pipeline_cache.py`:
- `TRANSLATE_COMPLETENESS_VERSION = 3` в fingerprint (старые ключи miss)
- `translate_job_cache_acceptable`: reject если long (words>55 / oversized) **или** `is_incomplete_mt_pair`
- `load_translate_cache`: reject → unlink → `None` → Marian
- `save_translate_cache`: не писать incomplete/long blobs

Очищено: `output/cache/pipeline/translate`, `cache/mt`.

## Тесты

`tests/test_job_translate_cache_gate.py` — passed.
