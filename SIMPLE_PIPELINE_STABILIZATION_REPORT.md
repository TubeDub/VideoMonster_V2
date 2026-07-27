# SIMPLE_PIPELINE_STABILIZATION_REPORT

**Проект:** VideoMonster_V2 / TubeDub  
**Дата:** 2026-07-27  
**Цель:** Simple как у лучших простых open-source дубляторов (pyVideoTrans / VideoLingo style).

## Вердикт

Simple-режим зафиксирован как **один короткий путь** с явной политикой и entrypoint.  
Приёмочный прогон на ~110 с клипе (George Jr. / Lucas материал):

| Проверка | Результат |
|----------|-----------|
| MP4 | ✅ `video_d712432992_OUTPUT_5016c840.mp4` |
| `simple_pipeline` / `happy_path` | ✅ |
| segments | **21 → 11** |
| bleed | **0** |
| max atempo | **1.08** (не выше) |
| text-fit | ✅ applied, changed 5/10 |
| ADA/SSO/MF/TAT/TPS/adaptive seg | ✅ skipped |
| Время | **~16 мин** (957 с) — меньше «полчаса» |

Task: `5016c840ac134549a4efd7896d3feb73`  
JSON: `output/simple_pipeline_acceptance.json`

## Эталонный путь Simple

```
Видео → FFmpeg audio → STT (faster-whisper)
     → glue сегменты ≥4.5–5.0 с (пауза <0.9 с)
     → перевод 1:1 (+ source-aware batch split)
     → text-fit под slot (без обязательного LLM)
     → Edge-TTS, atempo только 0.95–1.08
     → FFmpeg mux → MP4 (auto-mix, без остановки на Studio)
```

## Что выключено в Simple

| Модуль | Статус |
|--------|--------|
| ADA / SSO / Meaning Fit | OFF |
| Timing-Aware LLM | OFF |
| TPS orchestrator | OFF |
| Adaptive Segmentation (pre-MT) | OFF |
| post-TTS resegment | OFF |
| blind `align_segments_to_timing_map` | OFF (source-aware parity) |
| atempo > 1.08 | OFF |
| длинный silence-pad | OFF (pause 80–200 мс) |
| enterprise / cloud / lip-sync | не в пути |

## Изменённые файлы

| Файл | Изменение |
|------|-----------|
| `engines/simple_dub_pipeline.py` | **новый** `apply_simple_pipeline_policy`, `run_simple_dub_pipeline`, `should_auto_mix_mp4` |
| `engines/happy_path.py` | явные флаги resegment/align/text-fit/atempo в `stamp_happy_path_meta` |
| `api/auto_dub_api.py` | Simple → `run_simple_dub_pipeline`; policy при старте; **auto-mix MP4** для Simple |
| `tests/test_simple_dub_pipeline.py` | unit на policy |
| `scripts/simple_pipeline_acceptance.py` | приёмочный прогон |

Ранее (Stage 2–3, уже в пути): сегментация Happy Path, translation parity, `fit_text_to_slot`, atempo cap.

## Примеры «было → стало» (текст / fit)

1. **except for cars + Fiat** (после glue в один слот ≥5 с — ожидаемо для Simple):  
   - Original: `That is except for cars. And at that point… Fiat…`  
   - Translation: начинается с **«Це за винятком автомобілів…»** (свой смысл, не чужой абзац).  
   - text-fit: shorten `18592 → 17333` ms.

2. **Обед / работа / ссора** — severe fit: `28592 → 16444` ms (`severe_keep_leading`).

3. **Больница** — drop fillers: `13629 → 13185` ms.

4. **Потенциал** — severe fit: `10148 → 3259` ms.

5. **USC** — severe fit: `14000 → 8296` ms.

Сравнение с проблемным `fa66` (до parity): там podium получал чужой абзац, сосед — «я знаю людей в USC».  
Сейчас: `bleed_count=0`, parity applied.

## Логи / критерии

В task info видно:
- `segments_before` / `segments_after`
- `translation_parity.bleed_count=0`
- `text_slot_fit.applied`
- `timing_fit_segments` → max atempo ≤ 1.08
- `simple_auto_mix_done=true`

## Как воспроизвести

```bash
python -u scripts/simple_pipeline_acceptance.py
# или UI: user_mode=basic → Дубляж
```

Unit:

```bash
pytest tests/test_simple_dub_pipeline.py tests/test_happy_path_stage1.py -q
```

## Остаточный долг (не блокирует Simple)

- Wall-clock ~16 мин на 2-мин клип всё ещё тянет Edge-TTS + тяжёлый import; ускорение кэшем TTS — отдельно.  
- STT glue специально **склеивает** «except for cars» с соседним предложением в слот 5–8 с (как pyVideoTrans) — отдельный короткий сегмент не требуется, если смысл в переводе свой.  
- Качество сырого MT (опечатки вроде «Убитий Чоловік») — не задача этого ТЗ.

## Успех по ТЗ

Обычный человек в Simple: загрузил видео → Дубляж → получил **MP4** → речь без разгона выше 1.08 → смысл сегментов без bleed.  
**Этап закрыт** для эталонного Simple-пути; Pro/Studio/cloud — потом.
