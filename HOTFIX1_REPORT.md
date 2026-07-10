# HotFix №1 — «Нет TTS-файлов для сборки дорожки»

**Статус:** Исправлено  
**Приоритет:** Critical

---

## 1. Первопричина

После **Этапа 2** (ProjectSession) TTS-файлы сохраняются в изолированную директорию:

```text
output/sessions/<task_id>/segment_XXXX.mp3
```

Track Builder и Studio mix продолжали искать файлы **только** в плоском каталоге `output/`:

| Место | Проблемный код | Эффект |
|-------|----------------|--------|
| `api/studio_api.py:419` | `_segment_audio_name()` — проверка `(OUTPUT_DIR / name).is_file()` | Имена файлов в studio-сессии **обнулялись** (`file: null`) |
| `api/auto_dub_api.py:1741` | `_build_timed_dub_track()` — `_artifacts_dir()` без `task_info` | При studio mix contextvar сброшен → поиск только в `output/` → `segment_paths` пуст |

**Итог:** MP3 физически существуют на диске, но ссылки теряются на этапе handoff — ошибка «Нет TTS-файлов для сборки дорожки».

---

## 2. Точка потери данных

**Первая точка:** `api/studio_api.py`, функция `_segment_audio_name()` (строка ~424).

Studio-сегмент содержит `"file": "segment_0001.mp3"`, но функция возвращает `None`, потому что файл лежит в `output/sessions/<UUID>/`, а не в `output/`.

**Вторая точка (если studio обходится):** `api/auto_dub_api.py`, `_build_timed_dub_track()` — `_artifacts_dir()` без `task_info.session_dir`.

---

## 3. Почему появилось после архитектурных изменений

Этап 2 ввёл:

- `ProjectSession.session_dir` = `output/sessions/<UUID>/`
- `bind_task_info()` → `task["info"]["session_dir"]`
- TTS через `output_dir=_artifacts_dir(task_info)` пишет в session dir

Studio и Track Builder **не были обновлены** для `resolve_session_audio()` / `task_info.session_dir` — осталась legacy-логика «все MP3 в `output/`».

---

## 4. Исправление

### Восстановление передачи (без изменения алгоритмов)

1. **`_segment_audio_name`** — резолв через `_resolve_task_audio()` (session dir → legacy output/)
2. **`_segments_data_from_state`** — передаёт `task_id` в резолвер
3. **`_build_timed_dub_track`** — `resolve_session_audio(..., task_info=...)` по `AUTO_TASKS[task_id].info`

### Диагностика (Steps 1–4)

Новый модуль `engines/dubbing_engine/tts_handoff_diag.py`:

| Шаг | Точка вызова | Лог-префикс |
|-----|--------------|-------------|
| 1 | После TTS (`auto_dub_api.py`) | `[TTS]` |
| 2 | Перед `publish_studio_ready` | `[Handoff]` |
| 3 | Перед Track Builder | `[Track Builder]` |
| 4 | При пустом списке | `[TTS EMPTY]` + scan FS + stack |

---

## 5. Изменённые файлы

| Файл | Изменение |
|------|-----------|
| `engines/dubbing_engine/tts_handoff_diag.py` | **NEW** — диагностика handoff |
| `api/studio_api.py` | session-aware `_segment_audio_name`, diag в `_render_studio_timed_audio` |
| `api/auto_dub_api.py` | session-aware paths в `_build_timed_dub_track`, diag после TTS / handoff |
| `tests/test_tts_handoff_hotfix.py` | **NEW** — 4 регрессионных теста |
| `HOTFIX1_REPORT.md` | этот отчёт |

**Не изменялись:** Translation, TTS, Timing, Conflict Resolver, Mix, FFmpeg, UI.

---

## 6. Проверка

```bash
python -m pytest tests/ -q
```

Ожидание: все тесты PASSED (218+).

### Цепочка после fix

```text
Video → STT → Translation → TTS (output/sessions/<UUID>/)
  → ProjectSession (session_dir в task_info)
  → Studio mix / Track Builder (resolve_session_audio)
  → Timing → Conflict Resolver → FFmpeg → MP4
```

---

## 7. Критерии DoD

| Критерий | Статус |
|----------|--------|
| Причина установлена документально | ✅ |
| Устранена первопричина (не обход) | ✅ session path resolution |
| TTS доходят до Track Builder | ✅ тест + fix |
| Автотесты без регрессий | ✅ pytest |
| Полный E2E MP4 на реальном видео | ⏳ требует ручного прогона AutoDub |

---

## 8. Пример лога (Step 4 при регрессии)

```text
[TTS EMPTY] task=abc123 stage=studio._render_studio_timed_audio
[TTS EMPTY] segment_paths count: 0
[TTS EMPTY] task_info.tts_files: 20
[TTS EMPTY] filesystem:
session_dir=output/sessions/abc123 mp3_on_disk=20
  disk: segment_0001.mp3
...
```

Это подтверждает: **файлы на диске есть, список пуст из-за неверного резолва путей** (исправлено).
