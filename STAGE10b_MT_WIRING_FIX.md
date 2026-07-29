# STAGE10b — MT Completeness wiring fix

**Дата:** 2026-07-29  
**Цель:** дожать то, что STAGE10 заявлял, но могло быть неявно / слабо зафиксировано: beams=2, glossary в Marian path, short-cache reject.  
**Не трогали:** oversized split, Stage9 voice/pads, Qwen в Simple.

---

## Вердикт

**Закрыто.** В `stable_translate` (single + batch) явно: `resolve_marian_beams()` (default 2), glossary protect→infer→restore→`apply_post_mt_glossary_fixes`, cache не хранит обрезки (`words_src>55` или oversized + ratio &lt; 0.35).

| Пункт | Статус |
|---|---|
| Нет `num_beams = 1 if use_stable_mt() else 4` | ✅ AST/string guard |
| `resolve_marian_beams` default 2; env clamp 1–4 | ✅ |
| EN→UK glossary в single + batch | ✅ `_finish_en_uk_glossary` |
| Cache short-reject + lookup delete | ✅ `words>55` OR oversized |
| Тесты 10b | ✅ `tests/test_stage10b_mt_wiring.py` |

---

## Изменения

### 1. Beams
`engines/mt/stable_translate.py` — single и `translate_batch_marian` вызывают только `resolve_marian_beams(simple=True)`:
- default **2**
- `MT_NUM_BEAMS` / `VM_MT_NUM_BEAMS` → clamp 1–4

### 2. Glossary
Хелпер `_finish_en_uk_glossary`:
1. `restore_glossary` (если были placeholders)
2. `apply_post_mt_glossary_fixes` (Fiat→Фіат и др.)
3. `apply_glossary_en_uk` (остаточные EN-термины)

Protect по-прежнему **до** infer (в т.ч. oversized parts).

### 3. Cache
`is_incomplete_mt_pair`: long src = `is_oversized_mt_unit(src)` **или** `words_src > 55`; если `words_tgt < 0.35 * words_src` → store skip; lookup → miss + unlink.

---

## Тесты

```text
pytest tests/test_stage10b_mt_wiring.py tests/test_stage10_mt_completeness.py -q
```

Покрыто: Fiat→Фіат; beams env; short cache rejected (+ delete на lookup).
