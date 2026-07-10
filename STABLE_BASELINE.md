# TubeDub — эталонная стабильная версия

**Тег:** `stable-2026-06-17`  
**Продукт:** TubeDub V2 (VideoMonster Engine)

## Зафиксированное качество (не ухудшать)

- Полный дубляж выполняется до конца
- Видео не «разваливается» (проверка `video_integrity`)
- Синхронизация и минимизация наложений реплик (`timing_fit`, adaptive dub)
- Естественная интонация TTS и улучшенные склонения (`translation_naturalizer`)

## Перед крупной доработкой

```powershell
.\scripts\backup_stable.ps1
```

Создаёт ZIP-копию проекта в `output/backups/` без `output/`, `uploads`, `.git`.

## Режимы озвучки (один пайплайн)

| ID | Название |
|----|----------|
| `modern` | Современный дубляж |
| `cinema` | Кинотеатр |
| `classic_voiceover` | Закадровый перевод |
| `nineties` | Озвучка в стиле 90-х |
| `language_learning` | Изучение языка |
| `subtitles_only` | Только субтитры |

Пресеты: `engines/dub_style_presets.py`

## Длинные видео

Обязательная проверка после изменений — см. `docs/LONG_VIDEO_TEST_CHECKLIST.md`

## Установщик

- Розница: `TubeDub_Setup.exe` — Настройки владельца → **Создать установщик**
- Тест 7 дней: `TubeDub_Test_7_Days_Setup.exe` → **Создать тестовую версию**
- Требуется: PyInstaller, Inno Setup 6, FFmpeg в `tools/ffmpeg/`
