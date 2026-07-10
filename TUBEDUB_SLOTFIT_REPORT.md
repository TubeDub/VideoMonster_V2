# TUBEDUB Slot Fit / Studio Report

## Изменённые файлы

- `api/auto_dub_api.py`
- `api/studio_api.py`
- `engines/locale_utils.py`
- `engines/plugins/__init__.py`
- `engines/plugins/vst_host.py`
- `static/js/i18n.js`
- `static/js/studio_timeline.js`
- `static/i18n/ru.json`
- `static/i18n/uk.json`
- `static/i18n/en.json`
- `templates/base.html`
- `tests/test_slot_fit_pipeline.py`
- `tests/test_i18n_keys.py`

## Почему таймлайн был «весь красный»

Studio считала переполнение по `tts_ms` (сырое TTS), даже когда фактическая fitted-длительность уже помещалась в слот.  
Плюс slot-fit в auto pipeline мог не запускаться из-за флагов, поэтому сжатие происходило не всегда до открытия Studio.

## Что исправлено по auto-compress в pipeline

- Slot Fit запускается внутри `auto_dub` как стандартный шаг до Studio/Timing.
- Retry-цикл компрессии поднят до 4 попыток.
- Переполнение отмечается как проблема только после исчерпания 4 попыток.
- В Studio overflow считается по fitted-длительности (если она есть), а не только по raw TTS.

## Локализация (i18n)

- Клиентский дефолт языка: `ru` для ru-локали, `uk` для uk-локали, иначе `en`.
- Серверный дефолт через `Accept-Language`/env тоже переведён на `en` как fallback.
- Добавлены ключи для новых действий сегмента (`split/copy/delete/merge`) в `ru/uk/en`.

## Таймлайн + waveform

- Треки редактора: **Original Voice, Dub Voice, Music, SFX, Markers**.
- API waveform поддерживает `dub_voice` и существующие треки.
- Добавлены API-операции сегментов: split / copy / delete / merge.
- Zoom по колесу на таймлайне включён без модификаторов.

## Plugins / VST

- Built-in loudness/compressor остаются реальными ffmpeg-эффектами на export.
- Добавлен `engines/plugins/vst_host.py` как абстрактный контракт VST2/VST3.
- Честно: полноценный runtime-host VST в этом шаге не реализован (только интерфейсный hook).

## Тесты

- `python -m pytest tests/test_slot_fit_pipeline.py tests/test_i18n_keys.py`
- Результат: **7 passed**

## Быстрый ручной тест (2–3 минуты)

1. Запустить `/dub` на видео 2–3 минуты.
2. Дождаться `studio_ready`, открыть `/studio?task_id=<id>`.
3. Проверить, что red в таймлайне — исключение, а не дефолт.
4. На длинном сегменте проверить, что Auto Fix снижает `overflow_pct`.
5. Проверить split/copy/delete/merge и zoom колесом.
6. Проверить waveform для `original` и `dub_voice`.
7. Выполнить экспорт MP4 и убедиться, что цепочка плагинов применяется.
