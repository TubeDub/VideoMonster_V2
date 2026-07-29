# STAGE9 — Simple Audio Quality Lock

**Дата:** 2026-07-29  
**Режим:** Simple / Happy Path only  
**Симптомы:** тишина / Ostap↔Polina / «ось як це було тоді» / обрезанный MT

---

## Вердикт

| Проверка | Ожидание | Статус в коде |
|---|---|---|
| Один голос | `unique_voices_used=1` | ✅ `simple_voice_lock` — multi-speaker plan **пропущен** |
| Dead air | fill ≥ 0.80 или `slot_shrunk` | ✅ `UNDERFILL_SIGNIFICANT_THRESH=0.80` + shrink |
| Pad-мусор | нет в Final/TTS | ✅ генерация удалена + `strip_slot_pad_fillers` |
| MT long | split + cache v2 | ✅ oversized Marian + `v2_osplit` |
| Empty TTS | skip, не PIPELINE_CRITICAL | ✅ empty → skip+log |
| PIPELINE_CRITICAL UI | тип exception в сообщении | ✅ `[IndexError]: …` |

Unit: `tests/test_stage9_simple_voice_lock.py`, `tests/test_no_slot_pad_filler.py` — OK.

---

## A. Один голос (P0)

**Причина:** `_apply_voice_platform_assignments` / `plan_project_voices` ставили разные Edge id по speaker/seg.

**Сделано:**
- `engines/simple_voice_lock.py` — `lock_simple_pipeline_voice`
- Simple: **не** вызываем multi-voice plan
- Перед Stage6 TTS: все `voice` / `assigned_voice` = `pipeline_voice`
- Логи/status: `simple_voice_locked`, `tts_voice`, `unique_voices_used`
- Assert: если unique > 1 → force pin

---

## B. Dead air + pad (P0)

| Пункт | Реализация |
|---|---|
| B1 Slot shrink | `timing_fit.shrink_underfilled_slot_end` при fill < 0.80 |
| B2 Порог | `UNDERFILL_SIGNIFICANT_THRESH = 0.80`; QA `POST_TTS_UNDERFLOW_RATIO = 0.80` |
| B3 Empty TTS | пустой текст → `skip_tts` + log, пайплайн идёт дальше |
| B4 Pads | `_rule_expand_once` **не** добавляет «ось як…» / «Саме так:»; `strip_slot_pad_fillers` в expand, Review populate, freeze, TTS |
| B5 Expand | только mild intensifiers; иначе shrink |

> Ранее на GitHub/main ещё могла быть старая генерация pads — сейчас в локальном дереве генерация вырезана.

---

## C. MT без обрезки (P1)

- Marian: oversized → sentence/word split → translate parts → join  
- Cache key включает `v2_osplit` (старый truncated cache не бьёт)  
- Pads после MT стрипаются на Review/TTS

---

## D. PIPELINE_CRITICAL

Сообщение: `Критическая ошибка пайплайна [ExcType]: …`  
Empty / voice-mix в Simple не валят весь job молча.

---

## Файлы

| Файл | Роль |
|---|---|
| `engines/simple_voice_lock.py` | **new** — single voice |
| `engines/simple_dub_pipeline.py` | policy stamps |
| `api/auto_dub_api.py` | skip voice platform; re-pin; empty skip; status fields |
| `engines/text_slot_fit.py` | no pad invent + strip |
| `engines/tts_review_align.py` | strip on freeze |
| `engines/pipeline_language_gate.py` | strip on heal |
| `engines/timing_fit.py` | underfill 0.80 + shrink |
| `engines/segment_timing_qa.py` | underflow 0.80 |
| `engines/mt/stable_translate.py` | oversized split |
| `engines/mt_cache.py` | v2 key |
| `tests/test_stage9_simple_voice_lock.py` | unit |
| `tests/test_no_slot_pad_filler.py` | unit |

---

## Как проверить

1. Очистить при необходимости: `cache/mt`, `cache/tts`  
2. Simple → George Jr. клип, голос Ostap (или любой **один** из UI)  
3. На слух: один тембр на весь ролик  
4. Review: нет «ось як це було тоді» / «Саме так»  
5. Status: `simple_voice_locked=true`, `unique_voices_used=1`  
6. `python -m pytest tests/test_stage9_simple_voice_lock.py tests/test_no_slot_pad_filler.py -q`

Acceptance: `python scripts/simple_pipeline_acceptance.py` → checks `voice_unique_ok`, `no_slot_pad_filler`.
