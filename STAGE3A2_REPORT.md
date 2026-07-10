# Этап 3A.2 — TTS Failure Diagnostics

**Статус:** Реализовано | **Приоритет:** Critical

---

## 1. Принцип прозрачности

Запрещены обобщённые сообщения без контекста. Каждый сбой TTS сопровождается полным диагностическим отчётом.

---

## 2. Структура диагностики (TZ §2)

Модуль `engines/dubbing_engine/tts_failure_diag.py`:

| Поле | Источник |
|------|----------|
| segment_id, current/total | `_tts_context_for_segment()` |
| original_text | source_segments_snapshot |
| tts_text | текст после plain_text / SSML strip (PronunciationNormalizer path) |
| voice, language | конфигурация пайплайна |
| tts_file_path | ожидаемый путь до генерации |
| error_code | TimeoutError→TTS_TIMEOUT, Edge→EDGE_TTS, … |
| traceback, duration_ms | `engines/tts.py` `_generate_single()` |

Лог: `[TTS FAILURE]` + JSON в `output/sessions/<UUID>/tts_failure_<id>_<ts>.json`

---

## 3. UI Contract (TZ §3)

Формат сообщения:

```text
TTS ошибка [5/20] segment_id=abc123: EDGE_TTS — Connection reset
```

Передаётся через:

- `task["errors"]` (не терминально при partial failure)
- `task["info"]["last_tts_error"]`
- `progress_detail.last_tts_error` → отображение в `dub.js` progress-live
- API `/api/auto_dub/status/<id>` → `last_tts_error`, `tts_failures`

---

## 4. No-Swallow / Session State

| Требование | Реализация |
|------------|------------|
| No try/except без логирования | `_generate_single` логирует все параметры перед raise |
| Context-rich TTS calls | `context=` / `tts_context` в sequential и parallel |
| Snapshot при сбое | `_snapshot_project_on_tts_failure()` → studio session JSON |
| Сбой не останавливает проект | `continue` вместо `_fail()`; pipeline → studio_ready с failed сегментами |

Failed сегменты: `tts_status=failed`, `container_status=red`, `tts_error={...}`

Integrity guard пропускает `tts_status=failed` при validate.

---

## 5. Изменённые файлы

| Файл | Изменение |
|------|-----------|
| `engines/dubbing_engine/tts_failure_diag.py` | **NEW** |
| `engines/tts.py` | context, TTSGenerationError, per-group parallel catch |
| `api/auto_dub_api.py` | handlers, continue-on-failure, status API |
| `engines/pipeline_integrity/guards.py` | skip failed segments |
| `engines/pipeline_integrity/stage_contracts.py` | tts_status/tts_error whitelist |
| `static/js/dub.js` | show last_tts_error in progress |
| `tests/test_tts_failure_diag.py` | **NEW** — 8 tests |

---

## 6. DoD

| Критерий | Статус |
|----------|--------|
| Логирование всех параметров (§2) | ✅ |
| UI содержит segment_id + N/M (§3) | ✅ |
| Автосохранение проекта при сбое (§4) | ✅ |
| Сбой одного сегмента не блокирует проект (§4) | ✅ |
| 237+ автотестов | ✅ (после pytest) |

---

## 7. Тесты

```bash
python -m pytest tests/test_tts_failure_diag.py tests/ -q
```
