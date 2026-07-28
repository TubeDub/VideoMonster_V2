# STAGE7b — Simple Translation Path Lock (без Qwen)

**Дата:** 2026-07-28  
**Цель:** в Simple/Happy Path перевод = только `cache → Marian batch`, без Qwen / translation agent / streaming orchestrator.

---

## Вердикт

**Закрыто.** Gate `use_locked_simple_mt` + `run_locked_simple_mt` прибивают путь; UI скрывает Qwen; при ошибке Simple **не** откатывается в agent/LLM.

| Проверка | Ожидание | Реализация |
|---|---|---|
| UI Simple | нет активного Qwen | `hidden_buckets` + `phase_status.llm_adaptation=skipped`; `dub.js` прячет ряд |
| Логи | `marian_batch` / `mt_cache`, agent=false | `translate_method`, `translation_agent_path=false`, `llm_adaptation_used=false` |
| Путь | только batch | нет fallback на `_prepare`/UniversalPipeline с LLM |
| Pro/Studio | не ломать | agent path только если `not use_locked_simple_mt` |

---

## Что изменено

### 1. Жёсткий gate
- `engines/simple_mt_path.py` — `use_locked_simple_mt`, `run_locked_simple_mt`, UI timing без Qwen
- `api/auto_dub_api.py` шаг «Перевод»:
  - Simple → **только** `run_locked_simple_mt`
  - **запрещены:** Director/AI-Core translation agent, Qwen adaptation
  - ошибка Simple → pipeline fail (`SIMPLE_MT_FAILED`), **без** agent fallback
- `_prepare_translated_segments` при Simple **редиректит** в locked MT (если кто-то всё же вызвал)

### 2. UI
- Backend шлёт `translation_timing` с `hidden_buckets: [llm_adaptation, post_processing]`
- Labels: «Marian MT» / «Кэш перевода»
- `static/js/dub.js` — скрывает Qwen/post в Simple
- Status API отдаёт `simple_mt_locked`, `llm_adaptation_used`, `translate_method`

### 3. Логи (task.info)
- `translate_method = "marian_batch" | "mt_cache"`
- `mt_wall_sec`, `mt_engine`, `mt_cache_hits/misses`
- `translation_agent_path = false`
- `llm_adaptation_used = false`
- `simple_mt_locked = true`
- `tps_skip_orchestrator = true`

### 4. Policy
- `engines/simple_dub_pipeline.py` штампует lock-флаги на старте Simple

---

## Файлы

| Файл | Роль |
|---|---|
| `engines/simple_mt_path.py` | **new** — lock API |
| `api/auto_dub_api.py` | gate + status fields + no Qwen fallback |
| `engines/simple_dub_pipeline.py` | policy stamps |
| `static/js/dub.js` | hide Qwen substep |
| `static/css/dub.css` | skipped/hidden styles |
| `tests/test_stage7b_simple_mt_path_lock.py` | unit |

---

## Как проверить вручную

1. UI → Simple → Дубляж (George Jr. клип)
2. На этапе «Перевод» видно **Marian MT** или **Кэш перевода**
3. **Нет** активного «Qwen / LLM Adaptation»
4. В статусе/логах: `translate_method=marian_batch|mt_cache`, `translation_agent_path=false`, `llm_adaptation_used=false`
5. mt_wall: десятки секунд cold / ~0 warm

Unit: `pytest tests/test_stage7b_simple_mt_path_lock.py` — OK.
