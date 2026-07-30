# STAGE14b — Post-MT glossary only (Simple en→uk)

**Дата:** 2026-07-30  
**Симптом:** Final/TTS с `__GLOS__000_`, `_GLOS_001`, `__GLOS_XY`; #5 обрезан; #9 без Star Wars/Lucas.

## Причина

Protect→Marian→restore: Marian портит ASCII-токены → restore не матчит → мусор в Final. Имена не доходят как EN → post-fixes не срабатывают.

## Фикс

### A. Protect off
- `protect_glossary()` = no-op `(text, [])`
- `restore_glossary()` = no-op при пустой map
- `stable_translate` (single + batch): RAW source → Marian → `finalize_mt_text` only

### B. Post-MT only
`finalize_mt_text`:
1. `strip_glossary_placeholders` (`__GLOS…`, `_GLOS…`, `}Token`, legacy)
2. `apply_post_mt_glossary_fixes` (EN→UK word-boundary + UK mangling)
3. strip снова; log если dirty

### C. Gates
- TTS: `finalize_mt_text` перед озвучкой
- `mt_cache` / job cache: reject тексты с `__GLOS_` / garbage / near-empty после strip

Не трогали: Stage 13 job cache gate, tts_lang_lock, Qwen, voice lock.

## Acceptance (cold: clear `cache/mt` + `output/cache/pipeline/translate`)

1. Final/TTS: ноль `__GLOS_` / `_GLOS_`
2. #1 Джордж Молодший
3. #2 Фіат
4. #5 длиннее; вижив / розбив / аварі
5. #9 Векслер, Лукас, Зоряні війни
6. Engine длинных: `marian_batch`

## Тесты

```text
pytest tests/test_stage14b_post_glossary_only.py tests/test_stage14_glossary_restore.py tests/test_stage10_mt_completeness.py tests/test_stage10b_mt_wiring.py -q
```
