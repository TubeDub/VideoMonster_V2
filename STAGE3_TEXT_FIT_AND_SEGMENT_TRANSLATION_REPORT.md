# STAGE3_TEXT_FIT_AND_SEGMENT_TRANSLATION_REPORT

**Проект:** VideoMonster_V2 / TubeDub  
**Дата:** 2026-07-27  
**Диагностическая задача:** `fa66b0299dbc499bb99ededcd6e4cb21` (George Jr. / Lucas)  
**Цель:** 1 оригинал → 1 перевод → текст под `slot_ms`; речь около нормальной скорости.

## Вердикт

Исправлены **translation bleed** (раскладка 1:1 + repair length-imbalance) и усилен **text-fit до TTS** без обязательного LLM. Happy Path atempo остаётся **0.95–1.08**; post-TTS adaptive resegment на Happy Path **выключен** (он раздувал bleed).

На симуляции поверх studio `fa66b029`:
- сегмент «except for cars» — короткий свой текст;
- podium / middle-aged man — blob с USC-цитаты переразложен по смыслу EN;
- studio max atempo на старом прогоне: **1.08**.

## Изменённые файлы

| Файл | Что сделано |
|------|-------------|
| `engines/translation_segment_parity.py` | **новый** `split_translation_by_sources`, `enforce_one_to_one_translations`, `repair_length_imbalance_pairs`, `detect_translation_bleed`, stamp-аудит |
| `engines/translation_pipeline.py` | после batch-MT всегда **source-aware** split (timing — только fallback) |
| `engines/translation_naturalizer.py` | Happy Path MT-группы: `cross_sentence=False` |
| `engines/adaptive_segmentation/post_tts.py` | split UK по source lengths; не оставлять полный blob слева |
| `engines/closed_loop_timing.py` | resegment только если advanced adaptation ON |
| `engines/segment_timing_qa.py` | то же для post-TTS QA |
| `engines/text_slot_fit.py` | порог overflow **1.10**; severe shorten + char-budget без mid-word cut |
| `api/auto_dub_api.py` | parity после перевода; text-fit логи; Happy Path без blind `align_segments_to_timing_map` |
| `tests/test_translation_segment_parity.py` | unit-тесты bleed / imbalance / severe fit |

## Задача 1 — translation bleed

### Как проявлялось (fa66 studio)

| # | Original | TTS (было) |
|---|----------|------------|
| 11 | короткий «…photos of the winning driver» | длинный абзац про подіум + чоловік + Haskell |
| 12 | продолжение про middle-aged man / photography | чужая фраза «я знаю людей в USC» |

Причины:
1. Batch / resegment оставлял один UK blob на первом слоте.
2. Blind `align_segments_to_timing_map` мог перераспределять смысл по длительности.
3. Post-TTS adaptive resegment на Happy Path усугублял рассинхрон.

### Как устранили

1. После MT: `split_batch_translation` / `split_translation_by_sources` **всегда** по EN границам.
2. `enforce_one_to_one_translations` + `debleed` + **length-imbalance repair**.
3. Happy Path: **не** делать timing-redistribute перевода; pad/trim без смены смысла.
4. Happy Path: **не** запускать post-TTS resegment.
5. Лог на сегмент: `original`, `translated_for_this_segment`, `tts_text`, `translation_bleed`.

### Симуляция «было → стало» (fa66)

| Сегмент | Было | Стало |
|---------|------|-------|
| «That is, except for cars.» | `Тобто, окрім автомобілів.` | без изменений (уже ок) |
| Fiat / father | свой длинный UK | свой (без bleed) |
| podium (11) | весь абзац + чужой хвост | `…сфотографувати водія-переможця` |
| man/photography (12) | `«Джордж, я знаю людей в USC».` | `але коли він ішов туди, до нього підійшов чоловік…` |
| Lucas / Star Wars | свой короткий финал | без изменений |

## Задача 2 — text-fit

`fit_text_to_slot(text, slot_ms, lang="uk")`:
1. `estimate_tts_ms` (chars/syllables, без синтеза)
2. если `predicted > slot * 1.10` → shorten (fillers → soft_sync → leading sentences → severe char budget)
3. если `predicted < slot * 0.75` → **не** растягивать голос
4. без обязательного LLM; ADA/SSO/Meaning Fit не в Simple

Вызов: после перевода (до Review) и reinforce перед TTS.

Пример overflow 11: pred **34222 → ~5259** после parity+fit (слот ~4112; лёгкий overflow предпочтительнее обрезки слов / atempo>1.08).

## Задача 3 — atempo / padding

- Happy Path: `min_atempo=0.95`, `max_atempo=1.08` (force в `build_gap_adjusted_track`)
- natural pause **80–200 мс**, без добивания тишиной до конца слота
- `no_speech_trim=True`
- studio `fa66`: max atempo **1.08**, min **1.0**

## Задача 4 — логи

В `segments_data` / task info:
- `translation_parity` / `translation_bleed`
- `text_slot_fit`: `slot_ms`, `predicted_ms_before/after`, `text_fit_applied`, `original_len` / `fitted_len`
- warning если atempo вне 0.95–1.08 (через существующий timing_fit cap)

## Тесты

```text
pytest tests/test_translation_segment_parity.py tests/test_text_slot_fit.py -q
→ 11 passed
```

Симуляция: `output/stage3_parity_sim.json` (обновляемый снимок).

## Что проверить руками на новом Simple-прогоне

1. «That is, except for cars.» → короткий UK про машины  
2. Fiat → свой текст  
3. intersection / accident → свой смысл  
4. George Lucas / Star Wars → финал без чужих абзацев  
5. atempo в логах ≈ 1.0 (не выше 1.08)  
6. нет длинных тишин «до конца слота»

Полный повторный дубляж клипа нужен для слухового «до/после»; логика parity+fit подтверждена unit-тестами и симуляцией на studio JSON `fa66b029`.
