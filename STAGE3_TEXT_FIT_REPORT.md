# STAGE3_TEXT_FIT_REPORT

**Проект:** VideoMonster_V2 / TubeDub  
**Дата:** 2026-07-27  
**Цель:** нормальная скорость речи; текст подгоняется под слот, а не голос.

## Принцип

1. Естественная скорость  
2. Смысл  
3. Тайминг  

**Запрещено как основной метод:** сильный atempo, мёртвая тишина, обрезка слов.  
**Разрешено:** перефраз короче/длиннее, atempo только **0.95–1.08**.

## Что изменено

| Файл | Изменение |
|------|-----------|
| `engines/text_slot_fit.py` | **новый** `estimate_tts_ms` + `fit_text_to_slot` / `fit_segments_to_slots` |
| `engines/happy_path.py` | `HAPPY_PATH_MAX_ATEMPO=1.08`, `MIN=0.95`; shortener=`text_slot_fit` |
| `engines/timing_fit.py` | preferred cap 1.08; pause ≤200 мс; **не** эскалировать atempo выше caller max |
| `api/auto_dub_api.py` | text-fit после перевода и перед TTS; Happy Path atempo **force** ≤1.08; TPS skip на Happy Path |
| `tests/test_text_slot_fit.py` | оценка / shorten / cap / no-trim |

## A–D: поведение

### A. Оценка до TTS
`estimate_tts_ms(text, lang)` — chars/words heuristic (без синтеза).  
Сравнение со `slot_ms` (±15%).

### B. Один механизм
`fit_text_to_slot(text, slot_ms, lang)`:
- soft_compress fillers  
- drop parens / лёгкие discourse fillers  
- soft_sync shorten (без ADA/SSO), отказ если <55% слов  
- trim хвоста / ведущие предложения с тем же порогом  

В пайплайне: сразу после перевода (до Review) + reinforce перед TTS.

### C. atempo
Happy Path: **max 1.08**, min 0.95, `no_speech_trim=True`.  
Исправлен баг: wrapper раньше оставлял default `1.20` из `build_gap_adjusted_track` — теперь `fit_kw["max_atempo"]=fit_max` принудительно.

### D. Тишина
По-прежнему только natural pause **80–200 мс**, без заполнения всего слота.

### E. Скорость пайплайна
- TPS на Happy Path **пропущен** (`tps_skipped=happy_path`)  
- post-TTS rewrite retries уже 0  
- advanced shorteners OFF  
- stage timings пишутся в performance / `timing_fit_segments`

## Тест (~110 с клип)

**Task:** `41b2ced5bc954484a9228fb6df4abe93`  
**MP4:** `output/video_889ebc25ad_OUTPUT_41b2ced5.mp4`  
**JSON:** `output/stage3_text_fit_result.json`

| Метрика | Значение |
|---------|----------|
| segments | 21 → 11 |
| `text_slot_fit.applied` | true (changed 1/10 на первом прогоне — правила щадящие) |
| `speech_trimmed` | **0** |
| max atempo (первый прогон) | 1.20 — **баг cap**, исправлен после прогона |
| unit-check после фикса | atempo **≤1.08** при `max_atempo=1.08` |

> Первый полный прогон зафиксировал `cap=1.200` из-за бага прокидывания. Фикс в `api/auto_dub_api.py` уже в коде; unit-тест подтверждает ≤1.08. Для слухового «до/после» нужен повторный Simple-дубляж на том же клипе.

## Осталось

1. Повторить прогон клипа → в логах должно быть `cap=1.080`, `max_atempo≤1.08`.  
2. Усилить text-fit, где predicted ≫ slot (сейчас бережёт смысл → иногда остаётся overflow).  
3. Studio hold / Edge-TTS latency всё ещё тянут wall-clock на ~10 мин для 2-мин клипа — отдельный ускоритель (кэш TTS, без studio_ready в Simple).

## Вердикт

Архитектура text-fit + atempo≤1.08 для Happy Path **включена**. Сильный atempo больше не должен быть основным рычагом; речь подгоняется текстом. Нужен один повторный прогон для подтверждения на слух после фикса cap.
