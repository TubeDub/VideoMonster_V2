# STAGE12 — Language Lock + Force Full MT (Simple)

**Дата:** 2026-07-29  
**Скоуп:** Simple / Happy Path. Не трогали: Qwen, pads strip, voice clone.

---

## Вердикт

**Закрыто.** Длинные сегменты идут через Marian+split (не короткий cache); TTS озвучивает только uk-текст uk-UA-голосом; incomplete cache жёстче (0.55 + smash/ejected/survived/Star Wars).

| Пункт | Статус |
|---|---|
| P0-A TTS target-lang + remt/skip | ✅ `tts_lang_lock` |
| P0-A voice uk-UA-* only, unique=1 | ✅ `simple_voice_lock` + mux assert |
| P0-B incomplete 0.55 + entity keys | ✅ `mt_cache` |
| P0-C skip cache long (default ON) | ✅ `VM_MT_SKIP_CACHE_LONG=1` |
| P1 Star Wars / Lucas entity | ✅ |
| P1 pre-mux integrity logs | ✅ |
| pytest | ✅ |

---

## Симптомы → фикс

| Симптом | Фикс |
|---|---|
| #5/#6 truncated via cache+glossary | long → skip cache → Marian+split; incomplete 0.55 |
| Аудио чешский / «Vítejme» | cyrillic ratio &lt; 0.6 → reject → remt → else skip; forbid cs-CZ |
| #9 без Star Wars | entity incomplete + long skip-cache |
| Voice locale drift | `uk-UA-*` only; mux fails if unique≠1 |

---

## Env

| Env | Default | Meaning |
|-----|---------|---------|
| `VM_MT_SKIP_CACHE_LONG` | **1** | oversized / words&gt;55 → no cache lookup |
| `VM_MT_NO_CACHE` | off | force all miss |

---

## Файлы

| Файл | Изменение |
|------|-----------|
| `engines/mt_cache.py` | ratio 0.55; smash/ejected/survived/…; skip_cache_for_long |
| `engines/mt_batch.py` | long skip-cache before lookup |
| `engines/tts_lang_lock.py` | **новый** — ratio, remt, enforce, pre-mux |
| `engines/simple_voice_lock.py` | forbid cs-CZ/pl-PL; force uk-UA |
| `engines/tts.py` | refuse non-uk text before Edge |
| `api/auto_dub_api.py` | lang lock pre-TTS; integrity pre-mux |
| `tests/test_stage12_lang_lock.py` | **новый** |

---

## Acceptance (ручной)

1. Удалить `cache/mt`, Simple George Jr. en→uk.  
2. Review: #5/#6/#9 → `marian_batch` (не сплошной cache+glossary).  
3. #5: вижив / аварію / викинуло.  
4. #9: Лукас + Зоряні.  
5. Аудио: только uk-UA Edge, без чешского.  

---

## Тесты

```text
pytest tests/test_stage12_lang_lock.py tests/test_stage11_mt_cache_bypass.py \
  tests/test_stage10*.py tests/test_stage7_mt_speedup.py tests/test_stage9_simple_voice_lock.py -q
→ 30 passed
```
