# STAGE16 — Marian UK quality repairs (George Jr. Simple)

**Дата:** 2026-07-31  
**После Stage 15:** truncation OK; сырой Marian всё ещё ломал #2/#5/#8/#9.

## Фикс

`engines/mt/glossary_en_uk.py` — `apply_uk_marian_repairs` / `_POST_UK_FIXES`:
- #2 Фіат + італійський; «не розумів одержимості…»
- #5 мчала / врізалася (не «бігла/розбився»)
- #8 надіслав заяву / подав заявку / його не візьмуть
- #9 USC (не СШ/Знімання США), кінооператор, франшиза / Зоряні війни

Также в `naturalize_uk` + cache key `v4_uk_quality_repairs`.

## Тесты

```text
pytest tests/test_stage16_uk_mt_quality.py -q
```
