# STAGE14c — Incomplete MT remt (#5 crash/survived)

**Дата:** 2026-07-30  
**Симптом (cold `11.json`):** #5 raw 13/65 слов — только «лежав у лікарні», без smash/ejected/survived. Glossary OK.

## Причины

1. `split_oversized_unit` паковал 2 предложения (65 слов) в **один** unit: max_sentences=2, max_words не проверялся → Marian обрезал.
2. Gate `is_incomplete_mt_pair` не принимал `розбила` (`розбив` ≠ stem) → ложный incomplete.
3. Live batch писал truncated в `out[]` без remt (cache-only reject).

## Фикс

- `oversized_guard`: pack учитывает `max_words`; safety word-chunk
- `mt_batch`: после finalize → `is_incomplete_mt_pair` → sentence/word RAW remt
- `mt_cache`: entity `розби\w*|аварі\w*|smash(?:ed|ing)?`

## Тесты

```text
pytest tests/test_stage14c_incomplete_remt.py tests/test_stage12b_truncated_mt.py -q
```

## Acceptance (повторный cold)

1. #5 длиннее; есть вижив / розбив* / викину*
2. Engine: `marian_batch` или `marian_batch+remt`
3. Glossary: ноль `__GLOS_`
