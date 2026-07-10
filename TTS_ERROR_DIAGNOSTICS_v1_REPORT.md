# TTS Error Diagnostics v1.0 — отчёт

**Статус:** Реализовано

---

## Запрет generic-сообщений

`vmFriendlyError()` **не заменяет** сообщения с маркерами:
- `VoiceGenerationError`
- `segment_id=`
- `TTS ошибка` / `TTS error`

Запрещённое сообщение «Произошла ошибка. Попробуйте ещё раз.» больше не показывается для TTS-сбоев.

---

## Диагностический блок (пример)

```
VoiceGenerationError
segment_id: abc123def456
segment_number: 5/20
engine: Edge-TTS
text:
"Добрий вечір."
reason:
TTS engine returned empty audio.
stage:
TTS Generation
pipeline_state:
PARTIAL
timestamp:
2026-06-25T12:00:00+00:00
```

Полный stack trace — **только в лог** (`tubedub.tts_failure`).

---

## Dub Studio — подсветка

- Сегменты с `tts_status=failed` — красная рамка + glow
- Клик → модальное окно `_showTtsFailureModal` с diagnostic block
- `segment_id` передаётся в studio session

---

## «Сообщить об ошибке» → ZIP

`build_error_report()` включает:

| Файл | Содержимое |
|------|------------|
| `report.json` | версия TubeDub, platform, task_id |
| `pipeline_state.json` | snapshot AUTO_TASKS |
| `tts_failures.json` | все TTS failure reports |
| `voice_engine.json` | voice + engine_id |
| `stacktrace.txt` | diagnostic blocks + tracebacks |
| `ProjectSession/` | studio_session.json + tts_failure_*.json |
| `logs/` | tubedub.log, dev logs |

---

## API status

`GET /api/auto_dub/status/<id>`:
- `last_tts_error` — однострочное UI-сообщение
- `last_tts_diagnostic` — полный блок
- `tts_failures[]` — массив отчётов

---

## Тесты

`tests/test_tts_failure_diag.py` — 12 tests  
Полный прогон: **247 passed**
