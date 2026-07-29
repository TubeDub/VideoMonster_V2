# STAGE9b — Pad filler removed from `text_slot_fit`

**Дата:** 2026-07-29  
**Цель:** вырезать изобретение хвостов «ось як це було тоді» / «Саме так:» из expand.

---

## Вердикт

**Закрыто.** `_rule_expand_once` больше **не** генерирует pad-хвосты.  
`strip_slot_pad_fillers` чистит уже попавший мусор после expand, перед Review freeze и перед TTS.

| Проверка | Результат |
|---|---|
| Нет «Саме так:» / «Именно так:» / «That is:» в expand | ✅ |
| Нет «— ось як це було тоді.» / «— вот как это было тогда.» | ✅ |
| `strip_slot_pad_fillers` после expand | ✅ |
| strip перед Review freeze | ✅ `freeze_spoken_to_review_final` |
| strip перед TTS | ✅ parallel + sequential TTS |
| `test_no_slot_pad_filler` | ✅ (+ static AST guard на литералы) |

---

## Что удалено из `_rule_expand_once`

Больше нет веток:
- echo: `Саме так: …` / `Именно так: …` / `That is: …`
- pacing: `— ось як це було тоді.` / `— вот как это было тогда.`

Остаются только meaning-safe intensifiers (`Тож`→`Тож тоді`, `був`→`справді був`, …).  
Underfill без безопасного expand → **slot shrink**, не pad.

---

## `strip_slot_pad_fillers`

Вырезает UK/RU/EN pad-фразы из готового текста (кэш / старый fit).

Вызовы:
1. `expand_text_to_slot` — вход, каждый rule-pass, выход (и LLM early-return)
2. `fit_text_to_slot` — на входе
3. Review populate / `freeze_spoken_to_review_final`
4. TTS input (parallel + sequential)

---

## Тест

`tests/test_no_slot_pad_filler.py`:
- strip dirty → clean
- expand uk/ru/en + `_rule_expand_once` → нет banned
- AST: в исходнике `_rule_expand_once` нет pad-литералов (тест **упадёт**, если вернуть генерацию)

```bash
python -m pytest tests/test_no_slot_pad_filler.py -q
```

---

## Файлы

| Файл | Изменение |
|---|---|
| `engines/text_slot_fit.py` | no pad invent; strip on all expand exits |
| `engines/tts_review_align.py` | strip on freeze |
| `api/auto_dub_api.py` | strip Review + TTS |
| `tests/test_no_slot_pad_filler.py` | hardened fail-on-pad |
| `STAGE9b_PAD_FILLER_REMOVED.md` | этот отчёт |
