# STAGE10c — stable_translate.py wiring (verified by grep)

**Дата:** 2026-07-29  
**Файл:** `engines/mt/stable_translate.py`  
**mt_cache:** не трогали (уже ок)

---

## Grep (обязательная проверка)

```text
grep -n "num_beams = 1 if use_stable_mt" engines/mt/stable_translate.py
→ (empty)

grep -n "resolve_marian_beams\|protect_glossary\|apply_post_mt_glossary" engines/mt/stable_translate.py
→ hits: resolve_marian_beams, protect_glossary, apply_post_mt_glossary_fixes (single + batch)
```

**Статус grep: GREEN**

---

## Что изменено в `stable_translate.py`

1. **Удалено** любое `num_beams = 1 if use_stable_mt() else 4` (в файле отсутствует).
2. **`resolve_marian_beams(simple=True)`** — env `MT_NUM_BEAMS` / `VM_MT_NUM_BEAMS` clamp 1–4; иначе `2 if simple else 4`.
3. Single + `translate_batch_marian`: `num_beams = resolve_marian_beams(simple=True)`.
4. **Glossary EN→UK** (inline, не через helper):
   - перед infer: `protect_glossary`
   - после infer / join: `restore_glossary` → `apply_post_mt_glossary_fixes` → `apply_glossary_en_uk`
   - oversized: protect на полном тексте, затем split parts.

---

## Тесты

```text
pytest tests/test_stage10b_mt_wiring.py tests/test_stage10_mt_completeness.py -q
→ 14 passed
```
