# Pipeline Error Diagnostics v1.0 — отчёт

**Статус:** Реализовано (Critical Bug Fix)

---

## Scope

Расширение TTS-диагностики на **весь пайплайн дубляжа**: STT, Translation, TTS, Timing, FFmpeg Render.

---

## Модуль `pipeline_failure_diag.py`

| Компонент | Назначение |
|-----------|------------|
| `PipelineFailureReport` | stage, error_type, error_code, segment, voice, reason, traceback |
| `fail_pipeline()` | Fail-fast: STOPPED, лог, JSON, ProjectSession snapshot |
| `RuntimeDiagnosticsRecorder` | Запись после каждого этапа (§7 TZ) |
| `format_detail_block()` | Технический блок для «Подробнее» |
| `user_summary()` | Краткий UI: Этап / Ошибка / Сегмент / Причина |

**Этапы:** Audio Extraction, STT, Translation, TTS, Timing Engine, FFmpeg Render.

---

## Fail Fast (§6)

- TTS-сбой → **остановка пайплайна** (`pipeline_state: STOPPED`), без `continue`
- `_fail()` → structured `fail_pipeline()` на всех путях ошибок
- Исключение не скрывается; stack trace в лог

---

## UI (§4)

- Панель **«Ошибка дубляжа»**: Этап, Ошибка, Сегмент, Причина
- Кнопка **«Подробнее»** → технический блок
- `vmFriendlyError()` **не возвращает** «Произошла ошибка. Попробуйте ещё раз.»

---

## ZIP «Сообщить об ошибке» (§5)

| Файл | Содержимое |
|------|------------|
| `report.json` | TubeDub, Python, FFmpeg, platform |
| `pipeline_state.json` | snapshot задачи |
| `ProjectSession.json` | studio session |
| `engine_info.json` | Voice Engine + installed models |
| `config.json` | параметры задачи |
| `runtime_diagnostics.json` | per-stage metrics |
| `stacktrace.txt` | diagnostic blocks + tracebacks |
| `logs/` | tubedub.log, dev logs |

---

## Runtime Diagnostics (§7)

После этапов 1–6 сохраняется: номер, duration_ms, segments, errors, memory_mb, voice_engine, integrity_guard.

---

## Тесты

- `tests/test_pipeline_failure_diag.py` — 8 tests
- `tests/test_tts_failure_diag.py` — обновлены под fail-fast

**Полный прогон:** см. pytest
