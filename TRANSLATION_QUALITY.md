# Translation Quality — TubeDub (VideoMonster Engine)

## Что изменилось (регрессия)

### Ранний VideoMonster (EFER backup, `api/auto_dub_api.py`)

```python
translated_text = translate_text(source_text, source_lang, target_lang)
segments = split_by_timing_map(translated_text, timing_map)
```

- Один **batch-перевод** всего транскрипта Whisper
- Контекст предложений сохраняется
- Разбиение на реплики только **после** перевода через `split_by_timing_map`

### Текущая регрессия (до фикса)

```python
translated_segments = translate_segments(source_segments, ...)  # каждый сегмент отдельно
```

- Whisper даёт короткие фрагменты: «The goat was walking», «The goat was chewing»
- Перевод по одному → «Коза ходила…», «Коза жевала…» — роботизированные повторы
- Дополнительно: префикс `[context: …]` в промежуточной версии naturalizer загрязнял перевод

## Что восстановили в TubeDub

1. **Batch-перевод (по умолчанию)** — `translate_segments_natural()` склеивает микро-сегменты, переводит блок, делит через `split_by_timing_map`
2. **Натурализация** — `naturalize_ru()`, `dedupe_consecutive_similar()` убирают повтор подлежащего и смысловые дубли; короткие фрагменты (<12 символ / <1.2 с) склеиваются перед переводом
3. **TTS** — `merge_segments_for_tts()` объединяет окна <2 с; Edge-TTS `rate=-5%` (env `VM_TTS_RATE`)
4. **Full dub** — `dub_engine._cmd_replace()` явно `-map 1:a:0 -map -0:a`, только дорожка дубляжа
5. **Кэш/redub** — `translated_segments` только при `skip_translate=true` (явный redub из Студии с готовым переводом)
6. **Двойной запуск** — guard `starting`/`running` в `dub.js`, очистка `redub` при новом видео
7. **_OUTPUT_ guard** — API `/api/auto_dub/start` и `/api/dub/upload_video` возвращают 400 с понятной ошибкой

## Сегментация (дополнение №2, 16.06.2026)

**Симптом:** часть реплик идеальна, часть роботизирована; `dub_timing_fit_log.txt` показывает `atempo=2.0` на коротких слотах Whisper.

**Причина:** микро-сегменты STT (4–6 с) + русский длиннее английского → TTS не помещается → ускорение до 2× и обрезка.

**Исправления:**
1. `engines/segment_merger.py` — склейка STT до перевода (min 4.5 с)
2. `split_by_timing_map` — пропорциональное распределение текста по длительности слота
3. `timing_fit.py` — atempo max 1.30, заимствование паузы до 2.5 с, без обрезки речи
4. TTS merge min 4.5 с (`build_tts_groups`)
5. MP4: `-c:v copy` + `-t` длительности оригинала; mix с `apad`

**Режим звука по умолчанию:** `language_learning` 38% оригинала (субъективно естественнее в полевом тесте).

## Правило естественного перевода (FINAL TZ)

**Приоритет:** сохранить смысл → звучать как носитель целевого языка.

- Допускается перефразирование, смена порядка слов, лёгкое сокращение
- Запрещено менять смысл
- Универсально для всех языков через `naturalize_text()` + `polish_lines(tgt_lang=...)`
- RU: `naturalize_ru()` — склонения, разговорные формы, анти-повтор подлежащего
- UK: `naturalize_uk()` — падежи, украинские конструкции, удаление русизмов
- LLM (если `OPENAI_API_KEY` / `VM_LLM_API_KEY`): промпт с правилами FINAL TZ, `VM_TRANSLATE_NATURAL=1`

## Опциональный LLM polish

Если задан `VM_LLM_API_KEY` или `OPENAI_API_KEY`, после batch-перевода включается лёгкая пост-обработка реплик (`VM_TRANSLATE_MODEL`, по умолчанию `gpt-4o-mini`).

## Двойной голос (типичные причины)

| Причина | Симптом | Фикс |
|--------|---------|------|
| `language_learning` / amix | оригинал 35% + дуб | UI default `full_dub` |
| Повторный дубляж `_OUTPUT_` файла | STT слышит прошлый дуб | блок на API + предупреждение в UI |
| Старый `translated_segments` без `skip_translate` | пересказ / неверный текст | игнор на сервере |
| Перекрытие TTS в timing_engine | наложение реплик | merge TTS + один файл на группу |

## Тест

```powershell
cd c:\Users\serhii\Desktop\VideoMonster_V2
python scripts\test_naturalizer_unit.py
python scripts\test_translation_quality.py
```

Результаты: `output/tubedub_quality_report.txt`, `output/quality_test.txt`
