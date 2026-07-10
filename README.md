# TubeDub — быстрый старт

**TubeDub** — программа для **авто-дубляжа видео**: распознаёт речь, переводит и озвучивает. Одна кнопка.

*Powered by VideoMonster Engine*

## Для обычного пользователя

1. Установите **Python 3.10+** (python.org, галочка «Add to PATH»)
2. Установите **FFmpeg** и добавьте в PATH (ffmpeg.org)
3. Дважды щёлкните **`install_and_run.bat`**
4. В программе: **Выбрать видео** → язык перевода → **🤖 Дубляж**
5. Скачайте MP4 или «Сохранить в папку»

Нужен **интернет** (озвучка Edge-TTS и перевод через deep-translator, если Argos офлайн недоступен).

## Режимы

| Режим | Как включить |
|-------|----------------|
| **Простой** | По умолчанию — одна кнопка, минимум настроек |
| **Профессиональный** | Кнопка «⚙️ Про» — модель Whisper, режим звука |

## Лицензия

- Первый запуск: **7 дней теста** (полный доступ)
- После теста: **Basic** (базовые функции) или ключ **Premium** от владельца
- Ключ вводится в **Настройки → Лицензия** (формат `VM-XXXX-XXXX-XXXX`)

## Сборка EXE (владелец)

```bat
build_windows.bat
```

Результат: `dist\VideoMonster\VideoMonster.exe` (внутреннее имя сборки)

Тестовая версия для Telegram: **TubeDub_Test_7_Days.exe** (панель владельца в Настройках).

## Проверка и ZIP (разработчик)

```bat
run_smoke_test.bat
```

или (кроссплатформенно):

```bash
make test          # pytest
make lint          # ruff
make zip           # ZIP без cache/models
```

PowerShell: `scripts\dev.ps1 test`

Лог: `output\master_check_results.txt`  
ZIP: `output\VideoMonster_V2_ready.zip`

## Разработка

- [CONTRIBUTING.md](CONTRIBUTING.md) — установка, тесты, структура
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — слои и пайплайн
- `.env.example` — переменные окружения
- CI: `.github/workflows/ci.yml`

## Документация

- [REPORT.md](REPORT.md) — что исправлено, ограничения
- [LICENSE_SYSTEM.md](LICENSE_SYSTEM.md) — ключи и сервер лицензий
- [TRANSLATION_QUALITY.md](TRANSLATION_QUALITY.md) — качество перевода и дубляжа
