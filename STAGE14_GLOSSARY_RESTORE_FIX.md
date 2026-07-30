# STAGE14 — Glossary protect/restore fix (Simple en→uk)

**Дата:** 2026-07-30  
**Симптом:** Final с `ведьг0]`, `}USC`, `G000`, `Г2` после Marian; Star Wars/Lucas ломались.

## Причина

Unicode-плейсхолдеры `⟦G0⟧` Marian ломал → restore не срабатывал → мусор в Final/TTS.

## Фикс

`engines/mt/glossary_en_uk.py`:
- Protect: только `__GLOSS_001__` (ASCII)
- Restore: высокий индекс → низкий; только полные токены с digit-boundary (не `GLOSS_0` ⊂ `GLOSS_002`)
- Mangled/legacy варианты + strip leftovers; missing → EN/UK канон
- `strip_glossary_artifacts` / `contains_glossary_garbage`
- `finalize_mt_text` всегда чистит placeholders

`engines/mt/stable_translate.py`: после restore → strip → finalize (single + batch)

`engines/pipeline_integrity/tts_segment_fields.py`: cleanup перед TTS

Не трогали: skip_cache_long, job cache gate, tts_lang_lock, Qwen.

## Acceptance

1. #1 Джордж-молодший без ведьг0] / }  
2. #2 Фіат  
3. #5 лікарня + аварія/викинув/вижив (Stage 12b/13)  
4. #9 Векслер, USC, Лукас, Зоряні  
5. Final/TTS без `__GLOSS_` / `}X` / `G000`  

## Тесты

```text
pytest tests/test_stage14_glossary_restore.py tests/test_stage10_mt_completeness.py tests/test_stage10b_mt_wiring.py -q
```

Результат: 19 passed.
