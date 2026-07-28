# STAGE4 — TTS text sync + duration fit (Simple)

**Дата:** 2026-07-27  
**Ролик:** George Jr. / Lucas (`uploads/stage2_happy_path_clip.mp4`)  
**Task:** `a0d12680b34041f99a6f436ac212acdf`  
**Выход:** `video_67ec01a93a_OUTPUT_a0d12680.mp4`  
**Время пайплайна:** ~450 s  

## Вердикт

| Проверка | Результат |
|----------|-----------|
| Review text == озвучка (1:1) | **да** (`review_equals_tts`, `fit_preserved`) |
| Нет hard-cut / `meaning_truncated` | **да** (0) |
| atempo | **max 1.04** (лимит ≤1.08) |
| Синхрон со слотом исходника | **заметно лучше** (TTS ≈ slot, без разгона) |
| MP4 | **собирается** |

Acceptance: `output/simple_pipeline_acceptance.json`

## Корневая причина (Задача A)

После `text_slot_fit` короткий текст записывался в сегменты, но **Translation Review populate** и `align_info_for_translation_review` снова поднимали длинный `semantic_text` / audit Final. Edge-TTS говорил длинный буфер → `tts_ms` ≈ pre-fit, Review визуально мог совпадать с этим длинным текстом, а rows `text_slot_fit` оставались «фантомом».

## Что сделано

### A — Один текст = одна озвучка
- `engines/tts_text_authority.py` — авторитет `final_tts_text`, hash, assert
- Снимок `info["fitted_tts_texts"]` сразу после fit
- Review populate / align / freeze **восстанавливают** снимок, а не stale semantic
- Pre-TTS re-lock перед `build_tts_groups`
- Happy Path: skip post-Review re-fit и `audio_trim_text_sync`

### B — Перефраз под `slot_ms`, не обрезка
- `engines/text_slot_fit.py`: shorten при `predicted > slot * 1.08`
- Запрет висячих хвостов (`незважаючи на те.`, `…вирішив.`, `й застосувати.`)
- Только целые предложения / paraphrase; hard char-cut отказ

### C — Логи
На сегмент (timing rows + fit rows): `slot_ms`, `predicted_ms_before/after`, `tts_ms`, `atempo`, `final_tts_text`, `text_fit_applied`, `meaning_truncated`, `tts_text_hash`, `spoken_text_source`

## До / после (тот же клип)

| Seg | Было (pre-fix) | Стало (Stage4) |
|-----|-----------------|----------------|
| 1 | `tts_ms≈19032`, atempo 1.08, текст ~250 симв. (полный MT) | `tts_ms=8736`, atempo **1.0**, 117 симв. = fit |
| 2 | `tts_ms≈28128`, atempo 1.08 | `tts_ms=15336`, atempo **1.0** |
| 6 | `tts_ms≈10176`, обрезанный/длинный хвост | `tts_ms=4224`, atempo **1.0**, цельная фраза |

Примеры fit (смысл сохранён, без mid-thought cut):

1. **Seg1** slot 11764 ms: полный абзац про Fiat →  
   «Це за винятком автомобілів. І в той момент його батько купив йому маленький італійський автомобіль під назвою «фіат».»  
   (убран висячий «незважаючи на те.»)
2. **Seg2** slot 18760 ms: ужат до двух целых предложений про фокус/ужин (без хвоста «й застосувати.»).
3. **Seg6** slot 6102 ms: «Що певним чином він витрачав свій потенціал.» вместо обрыва «…вирішив.»

## Файлы

- `engines/tts_text_authority.py` (new)
- `engines/text_slot_fit.py`
- `engines/tts_review_align.py`
- `engines/tts_text_path.py`
- `engines/pipeline_integrity/tts_segment_fields.py`
- `api/auto_dub_api.py`
- `scripts/simple_pipeline_acceptance.py`
- `tests/test_stage4_tts_text_sync.py`

## Не делалось (по ТЗ)

Lip-sync, ADA/SSO/Studio, Pro-путь, новые большие модули.

## Следующий шаг

Ускорение пайплайна + 3–5 разных тестовых роликов — после того как A/B подтверждены на слух в UI Review.
