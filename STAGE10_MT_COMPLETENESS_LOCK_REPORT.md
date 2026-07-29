# STAGE10 — MT Completeness Lock (Simple)

**Дата:** 2026-07-29  
**Цель:** убрать обрезку Marian после ускорения MT, сохранить скорость Simple.  
**Скоуп:** Simple / Happy Path only. Pro/Studio / voice clone / Qwen — не трогали. Pads (Stage9b) — не возвращали.

---

## Вердикт

**Закрыто.** Перед Marian обязательный oversized-split; `num_beams=2`; короткий truncated MT не кэшируется; glossary protect для George Jr. demo.

| Проверка | Ожидание | Статус |
|---|---|---|
| Split перед `truncation=True` | parts → MT → join 1:1 STT | ✅ |
| `mt_batch` + guard | expand → batch units → rejoin | ✅ |
| Лог `[MT Guard] oversized seg#N → K parts` | warning | ✅ |
| Cache | полный join; short oversized → skip | ✅ ключ `v2_osplit` |
| Beams Simple | 2 (`MT_NUM_BEAMS` override 1–4) | ✅ |
| Glossary | Fiat→Фіат, USC, Star Wars, Jr. | ✅ |
| Pads / Qwen | не возвращать | ✅ |

---

## Корневая причина

1. `truncation=True, max_length=512` без split → обрезка длинных сегментов (#5/#6/#9).  
2. `oversized_guard` был, но не вшит в `mt_batch` miss-path.  
3. `num_beams=1` в stable path резал качество.  
4. Cache сохранял уже обрезанный перевод.

---

## Что изменено

### A. Split перед Marian
- `engines/mt/stable_translate.py` — single + `translate_batch_marian`: guard → units → generate → rejoin.  
- `engines/mt_batch.py` — перед Marian: `guard_segments_before_mt` → batch units → `_rejoin_by_parent` → слоты 1:1.  
- Пороги: `MT_MAX_CHARS_PER_UNIT=480`, `MT_MAX_SENTENCES_PER_UNIT=2`, `MT_MAX_WORDS_PER_UNIT=55`.

### B. Качество без сильной потери скорости
- `resolve_marian_beams()` → default **2** для Simple; env `MT_NUM_BEAMS` / `VM_MT_NUM_BEAMS` (clamp 1–4).  
- `max_length` generate = 512; tokenizer truncate только после split.

### C. Glossary (P1)
- `engines/mt/glossary_en_uk.py` — protect/restore + post-fixes (`Файта`→`Фіат`).  
- Пары из `project_glossary` / `default_en_uk.json` (fallback dict).  
- Вшито в Marian single/batch path (EN→UK).

### Cache
- `is_incomplete_mt_pair`: oversized EN→UK и `words_tgt < 0.35 * words_src` → **не store**; lookup → miss + delete.

---

## Файлы

| Файл | Изменение |
|------|-----------|
| `engines/mt/stable_translate.py` | split + beams=2 + glossary |
| `engines/mt_batch.py` | guard expand/rejoin |
| `engines/mt_cache.py` | short-reject |
| `engines/mt/glossary_en_uk.py` | **новый** |
| `tests/test_stage10_mt_completeness.py` | **новый** |

---

## Тесты

```text
pytest tests/test_stage10_mt_completeness.py tests/test_stage7_mt_speedup.py -q
```

---

## Рекомендация перед демо

Очистить старый MT cache (`cache/mt`), чтобы не тянуть truncated v2 записи, если они уже лежали с полным join но обрезанным текстом до short-reject. Затем cold Simple на George Jr. clip.
