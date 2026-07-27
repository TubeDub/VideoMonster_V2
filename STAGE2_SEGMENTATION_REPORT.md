# STAGE2_SEGMENTATION_REPORT

**Проект:** VideoMonster_V2 / TubeDub  
**Дата:** 2026-07-27  
**Режим:** Simple / Happy Path  

## Цель этапа

Убрать микро-сегменты Whisper **до** TTS: склейка ≥5.0 с, batch-перевод, без advanced-укорочителей, без обрезки речи.

## Что изменено (файлы)

| Файл | Изменение |
|------|-----------|
| `engines/segment_merger.py` | Happy Path glue: `min≥5000ms` (floor 4500), `gap<900ms`, лог `segments_before/after` |
| `api/auto_dub_api.py` | Обязательная Happy Path склейка; Adaptive Seg **пропущен** на Happy Path; `segments_before/after` в `task.info`; выравнивание `timing_map` перед TTS |
| `engines/translation_naturalizer.py` | `merge_segments_for_translation_happy_path` (cross-sentence batch); `_gap_ms` / `build_tts_groups` не падают при коротком `timing_map` |
| `engines/translation_pipeline.py` | Реальный вызов Happy Path batch + штамп `mt_batch_*` в task info |
| `tests/test_happy_path_stage2_segmentation.py` | Константы, склейка, gap 850ms, speakers, batch, advanced OFF, timing caps, IndexError-guard |
| `scripts/stage2_segmentation_quality_check.py` | Реальный прогон ~110 с клипа в Simple |

## Как работает склейка (Задача A)

После Whisper, **до** перевода:

1. `merge_stt_segments_happy_path()` склеивает соседние куски пока:
   - длительность блока < **5.0 с** (не ниже 4.5 с),
   - пауза < **0.9 с**,
   - спикер тот же (если diarization есть),
   - span ≤ 14 с.
2. В лог и `task.info`: `segments_before` → `segments_after`.
3. Adaptive Segmentation на Happy Path **не** пересобирает слоты (иначе снова появляются микро-дыры).

## Batch-перевод (Задача B)

- `happy_path_batch_translate()` → крупные группы (`cross_sentence=True`, batch до 14).
- После MT текст раскладывается через `split_by_timing_map` / batch splitter.
- В тестовом прогоне MT шёл через `translation_agent` (кэш/агент), но на уже склеенных блоках (~5–12 с), не на 1–2 с кусках.

## Advanced OFF (Задача C)

На Simple подтверждено:

- `adaptation_path=happy_path`
- `meaning_fit_skipped=happy_path`
- `timing_aware_skipped=happy_path`
- ADA / SSO не запускаются

Остаётся: naturalizer + мягкий soft-compress (если не review-freeze).

## Timing (Задача D)

- `atempo` hard-cap **1.20**, `no_speech_trim=True`
- Лог на сегмент: `slot_ms / tts_ms / atempo / overflow_ms / speech_trimmed`

## Результат реального теста (Задача E)

**Клип:** `uploads/stage2_happy_path_clip.mp4` (~110 с из `video_076d1b49ad.mp4`)  
**Task:** `0340f51fb6944934b147dc14ba51f14d`  
**Выход:** `output/video_a9c8d51913_OUTPUT_0340f51f.mp4` (~14.0 MB) — **done**

| Метрика | Значение |
|---------|----------|
| `segments_before` → `after` | **21 → 11** (−48%) |
| Synthetic probe | 24 → 6, median 5400 ms |
| `adaptation_path` | `happy_path` |
| Adaptive Seg | skipped (`happy_path`) |
| max `atempo` | **1.20** (не выше) |
| `speech_trimmed_count` | **0** |
| overflow сегментов | 5 (без hard-trim; atempo/warn) |
| Meaning Fit / TAT | skipped |

Сырой JSON: `output/stage2_segmentation_result.json`

### Синтетический smoke

```
segments_before=24 → segments_after=6, min_dur_ms=5400, ok_fewer=true
```

## Исправление по ходу теста

Первый прогон упал с `IndexError` в `merge_segments_for_tts` / `_gap_ms`, когда `timing_map` был короче списка сегментов после склейки/нормализации. Исправлено:

- безопасный `_gap_ms`
- `ensure_timing_map_for_segments` перед TTS groups
- повторный прогон — **успех до MP4**

## Остались ли проблемы

1. **Overflow без trim** — на части слотов TTS всё ещё длиннее слота; atempo упирается в 1.20. Это ожидаемо по ТЗ (лучше overflow, чем резать слова). Дальше можно чуть сильнее soft-compress / batch-naturalizer.
2. **`mt_batch_mode` null** в этом прогоне — MT шёл через translation_agent, не Marian grouping; сами сегменты уже крупные после склейки.
3. Часть UK строк всё ещё содержит EN хвосты (качество агента/MT) — не блокер сегментации, тема Этапа 4 (polish).

## Вердикт

**Этап 2 по сегментации закрыт:** склейка обязательна и видна в логах, Simple идёт по Happy Path, речь не режется, atempo ≤ 1.20, дубляж доходит до MP4.
