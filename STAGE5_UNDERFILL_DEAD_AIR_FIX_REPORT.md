# STAGE5 — Underfill / dead-air fix (Simple)

**Дата:** 2026-07-28  
**Ролик:** George Jr. / Lucas (`uploads/stage2_happy_path_clip.mp4`)  
**Task:** `1f3c3b2404a9410493fe26c556b5ce78`  
**Режим:** Simple / Happy Path only  

## Вердикт

| Проверка | Ожидание | Результат |
|----------|----------|-----------|
| Длинные тишины (fill &lt; 0.80) | нет | **нет** (`underfill_count=0`) |
| fill_ratio большинства | ≥ 0.80 | **10/10** (`fill_ok_ratio=1.0`) |
| max atempo | ≤ 1.08 | **1.06** |
| Review == озвучка | да | **да** |
| bleed | 0 | **0** |
| MP4 | собирается | **да** (~1130 s) |

Acceptance: `output/simple_pipeline_acceptance.json`

## Причина dead air (до фикса)

1. `text_slot_fit` укорачивал текст слишком агрессивно (overshoot ниже 0.80×slot).  
2. `_light_expand()` был **no-op**.  
3. `timing_fit` не сжимал слот → на таймлайне оставалась пустота внутри старого окна речи.

## Что сделано

### 1. Реальный expand (`engines/text_slot_fit.py`)
- `UNDERFILL_EXPAND_RATIO = 0.80`
- `expand_text_to_slot()` — LLM (если есть) + rule-based pacing/restate **без новых фактов**
- После shorten: если overshoot → expand обратно в полосу
- `keep_leading_sentences` не уходит в dead air, если оригинал ещё влезает в 1.08×slot

### 2. Детект underfill
На сегмент: `slot_ms`, `tts_ms`/`speech_ms`, `fill_ratio`, `underfill_ms`, `underfill_significant`  
В конце задачи: `underfill_count`, `max_underfill_ms`, `underfill_summary`

### 3. Slot shrink (`engines/timing_fit.py`)
- При `fill_ratio < 0.80`: `end ≈ start + speech_ms + 80–200 ms`
- Не наезжает на следующий сегмент
- Не добивает длинной тишиной до конца старого слота
- Не замедляет голос ниже 0.95

### 4. Сохранено (P1)
- `final_tts_text` authority / Review == TTS  
- bleed=0, atempo 0.95–1.08, auto-mix MP4, no meaning hard-cut  

## Таблица fill (после Stage 5)

| idx | slot_ms | tts_ms | fill_ratio | underfill_ms | atempo |
|-----|---------|--------|------------|--------------|--------|
| 0 | 9026 | 12336 | 1.084 | 0 | 1.00 |
| 1 | 11764 | 10608 | **0.902** | 1156 | 1.00 |
| 2 | 18760 | 15336 | **0.817** | 3424 | 1.00 |
| 3 | 9318 | 8184 | 0.878 | 1134 | 1.00 |
| 4 | 7958 | 7944 | 0.998 | 14 | 1.00 |
| 5 | 12360 | 12912 | 1.045 | 0 | 1.00 |
| 6 | 6265 | 8544 | 1.029 | 0 | 1.06 |
| 7 | 5084 | 6384 | 1.058 | 0 | 1.04 |
| 8 | 9169 | 7464 | **0.814** | 1705 | 1.00 |
| 9 | 7284 | 6888 | 0.946 | 396 | 1.00 |

## До / после (ключевые сегменты, Stage 4 → Stage 5)

| idx | Stage4 tts/slot (fill) | Stage5 tts/slot (fill) |
|-----|------------------------|------------------------|
| 1 | 8736 / 11764 (**0.74**) | 10608 / 11764 (**0.90**) |
| 2 | 15336 / 18760 (0.82) | 15336 / 18760 (**0.82**) |
| 6 | 4224 / 6102 (**0.69**) | 8544 / 6265 (**1.03**) |
| 8 | 7464 / 9169 (0.81) | 7464 / 9169 (**0.81**) |

## Файлы

- `engines/text_slot_fit.py` — expand + anti-overshoot  
- `engines/timing_fit.py` — underfill metrics + slot shrink  
- `api/auto_dub_api.py` — `underfill_summary` в task info  
- `scripts/simple_pipeline_acceptance.py` — fill checks  
- `tests/test_stage5_underfill_dead_air.py`  

## Не делалось

Pro / Studio / ADA / SSO / lip-sync / cloud / voice clone.  
P2 ускорение — отдельно (прогон ~19 мин из‑за более длинного TTS; цель &lt;15 мин на 2‑мин клип — следующий шаг).

## Готово для пользователя

В Simple: загрузка → Дубляж → MP4 без дыр, без сильного разгона, текст Review = голос.
