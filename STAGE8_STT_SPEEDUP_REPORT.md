# STAGE8 — STT Speedup (Simple / Happy Path)

**Дата:** 2026-07-28  
**Клип:** George Jr. / Lucas (`uploads/stage2_happy_path_clip.mp4`, ~110 с)  
**Режим:** Simple (`user_mode=basic`), UI шлёт `model_size=medium` → сервер caps до **small**

---

## Вердикт

**Закрыто.** Распознавание в Simple: **~608 с → ~83 с** (CPU int8, small, beam=1); warm/cache **~0.01 с**. Glue/parity/bleed/fill и Marian lock сохранены. Pro medium/large не ломаем.

| Проверка | Ожидание | Результат |
|---|---|---|
| MP4 | да | ✅ |
| fill ≥ 0.80, Review==TTS | да | ✅ fill_ok_ratio=1.0, review_equals_tts |
| bleed | 0 | ✅ |
| stt_wall_sec | ≤ 90–120 с CPU | ✅ **82.8 с** cold |
| stt_model | small | ✅ (cap с medium) |
| beam_size | 1 | ✅ |
| повтор файла | cache hit | ✅ **0.009 с**, `stt_cache_hit=true` |
| Simple MT | без Qwen | ✅ `simple_mt_locked`, `translate_method=marian_batch\|mt_cache` |
| post-TTS re-STT | запрещён | ✅ `voice_verification_skipped=simple_stt_lock` |

---

## Таблица прогонов

| Прогон | stt_wall_sec | model | device | compute | beam | total pipeline sec |
|--------|--------------|-------|--------|---------|------|--------------------|
| baseline (UI medium, pre-Stage8) | **608.4** | medium* | cpu | int8* | 5* | **765.6** |
| Stage8 cold | **82.8** | small | cpu | int8 | 1 | **208.5** (timer 176.1) |
| Stage8 warm/cache | **0.009** | small | cpu | int8 | 1 | **99.3** (timer 65.7) |

\* Baseline task `71964f3e…` (`pipeline_timing_*.json`: whisper=608.387). До Stage8 UI default = medium + beam≈5; device/compute типичны для этой машины (`probe_whisper_device` → cpu/int8). CUDA на стенде нет.

**Ускорение STT:** ≈ **7.3×** cold vs baseline; warm ≈ мгновенный disk hit.

---

## Что изменено

### 1. Simple STT policy
- `engines/simple_stt_policy.py` — default **small**, beam **1**, VAD on, word_timestamps off  
- CUDA → float16 / CPU → int8 (`probe_whisper_device`)  
- Cap medium/large → small **только** для Simple (`is_simple_mode` / happy_path), **не** через `skip_advanced` (Pro не регрессирует)

### 2. STT engine + метрики
- `engines/stt_engine.py` — `beam_size`/`vad_filter`, `best_of=1`, `get_last_stt_meta()`
- `api/auto_dub_api.py` — Simple lock до Whisper; прогресс «Распознавание речи (model / device)»
- Метрики → `task.info`, `pipeline_timing` meta, `output/stt_speedup_<task_id>.json`
- `stt_segments_raw` / `stt_segments_after_glue` после Happy Path glue

### 3. Кэш
- `engines/pipeline_cache.py` — ключ: fingerprint(audio) + model + lang + beam + device + compute  
- Hit → сегменты без Whisper

### 4. Запрет второго STT в Simple
- `voice_verification_asr_allowed=False`, `post_tts_restt_allowed=False`
- `_run_voice_verification_for_task` пропускается при Simple lock

### 5. UI / API
- Wizard default **small** (было medium)
- `/api/auto_dub/start` caps Simple model до small даже если UI прислал medium

### 6. Acceptance / tests
- `scripts/simple_pipeline_acceptance.py` — шлёт medium (как UI), собирает STT metrics + warm cache probe
- `tests/test_stage8_simple_stt_policy.py`

---

## Артефакты прогона

| Файл | Содержание |
|---|---|
| `output/simple_pipeline_acceptance.json` | cold acceptance |
| `output/stt_speedup_9ae3700980ca4e0383c900b68582936f.json` | cold STT stats |
| `output/pipeline_timing_9ae3700980ca4e0383c900b68582936f.json` | cold stages |
| `output/stage8_stt_warm.json` | warm full pipeline |
| `output/pipeline_timing_d9df9a364fe64729a6ce8b9a8b8e1a23.json` | warm stages |
| `output/pipeline_timing_71964f3e335d432a8cc5f6121d467b22.json` | baseline |

Cold STT detail: raw **25** → after glue **9**; `simple_stt_locked=true`.

---

## Файлы

| Файл | Роль |
|---|---|
| `engines/simple_stt_policy.py` | **new** — Simple STT defaults + lock |
| `engines/stt_engine.py` | beam/best_of/meta |
| `engines/pipeline_cache.py` | STT cache key knobs |
| `engines/simple_dub_pipeline.py` | policy stamp |
| `api/auto_dub_api.py` | lock + metrics + VV skip + start cap |
| `templates/dub.html` / `static/js/dub.js` | UI default small |
| `scripts/simple_pipeline_acceptance.py` | STT checks |
| `tests/test_stage8_simple_stt_policy.py` | unit |

---

## Как проверить

1. Simple → Дубляж того же клипа (можно оставить medium в UI — сервер сделает small).  
2. Фаза «Распознавание речи (small / cpu)» — порядка **1–2 мин**, не 10.  
3. Повтор того же upload-файла → `stt_cache_hit=true`, STT ~0 с.  
4. `pytest tests/test_stage8_simple_stt_policy.py` — OK.  
5. Pro + medium — без Simple lock (модель не caps).

Unit: `python -m pytest tests/test_stage8_simple_stt_policy.py` — OK.
