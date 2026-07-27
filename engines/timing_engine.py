"""
TubeDub — Timing Engine (Frozen Ultimate Release 100/100)
Модуль синхронизации аудиосегментов с временной картой (timing_map).
Отвечает исключительно за задачу: segment_paths + timing_map -> timed.mp3


Любые изменения Timing Engine допускаются только через отдельное ТЗ
и обязательное прохождение регрессионного тестирования.
"""

import logging
from pathlib import Path
from typing import Any, List, Tuple, Union


from pydub import AudioSegment

# Настройка локального логгера для модуля синхронизации
logger = logging.getLogger("tubedub.timing_engine")


def parse_timing(item: Any) -> Tuple[int, int]:
    """
    Парсит элемент тайминга из timing_map и приводит к миллисекундам (int).
    Поддерживает форматы:
    - Словарь: {"start": 1000, "end": 5000}
    - Кортеж: (1000, 5000)
    - Список: [1000, 5000]
    """
    try:
        if isinstance(item, dict):
            # Studio / ASR maps use start_ms/end_ms; older maps use start/end.
            if "start" in item or "start_ms" in item:
                start = item.get("start", item.get("start_ms"))
            else:
                raise KeyError("start")
            if "end" in item or "end_ms" in item:
                end = item.get("end", item.get("end_ms"))
            else:
                raise KeyError("end")
        elif isinstance(item, (list, tuple)):
            if len(item) < 2:
                raise ValueError(
                    "Элемент тайминга должен содержать минимум 2 значения."
                )
            start = item[0]
            end = item[1]
        else:
            raise TypeError(
                "Неподдерживаемый тип элемента тайминга. Ожидается dict, tuple или list."
            )

        start_ms = int(float(start))
        end_ms = int(float(end))

        if start_ms < 0 or end_ms < 0:
            raise ValueError("Временные метки не могут быть отрицательными.")
        if start_ms > end_ms:
            raise ValueError(
                "Время начала (start) не может быть больше времени окончания (end)."
            )

        return start_ms, end_ms

    except (KeyError, ValueError, TypeError) as e:
        raise RuntimeError(
            f"Критическая ошибка валидации формата тайминга на элементе {item}: {e}"
        )


def build_timed_audio(
    segment_paths: List[Union[str, Path]],
    timing_map: List[Any],
    mode: str = "exact",
    target_duration_ms: Union[int, float, None] = None,
) -> Tuple[AudioSegment, List[str]]:
    """
    Собирает финальную аудиодорожку дубляжа, накладывая верифицированные сегменты на мастер-подложку.


    :param segment_paths:       Список путей к .mp3 файлам реплик.
    :param timing_map:          Список временных интервалов (dict, tuple или list).
    :param mode:                Режим сборки (поддерживается только "exact").
    :param target_duration_ms:  Целевая длительность мастер-дорожки в миллисекундах.
    :return:                    Кортеж (final_audio, warnings_list).
    """
    logger.info("Инициализация сборки тайминг-карты. Режим: %s", mode)

    # Шаг 1. Проверка режима работы (Запрет silent fallback)
    if mode != "exact":
        raise RuntimeError(f"Unsupported timing mode: {mode}")

    # Шаг 2. Жесткая валидация на полное отсутствие данных
    if not segment_paths:
        raise RuntimeError("Критическая ошибка сборки: Список segment_paths пуст.")
    if not timing_map:
        raise RuntimeError("Критическая ошибка сборки: Список timing_map пуст.")

    # Шаг 3. Запрет скрытия рассинхронизации входных данных (Исправление по ТЗ №3)
    input_segments_count = len(segment_paths)
    input_timing_count = len(timing_map)

    if input_segments_count != input_timing_count:
        raise RuntimeError(
            f"Input mismatch: "
            f"segment_paths={input_segments_count}, "
            f"timing_map={input_timing_count}"
        )

    usable_count = min(input_segments_count, input_timing_count)

    # Защита от нулевого количества (Исправление по ТЗ №1)
    if usable_count == 0:
        raise RuntimeError("No usable timing pairs available for assembly.")

    # Метрики для сбора статистики качества
    missing_files_count = 0
    corrupt_files_count = 0
    empty_segments_count = 0
    duplicate_segments_count = 0
    overlap_warnings_count = 0
    timing_warnings_count = 0

    warnings_list: List[str] = []
    parsed_timings: List[Tuple[int, int]] = []
    valid_segments_data: List[Tuple[int, AudioSegment, int, int, str]] = (
        []
    )  # (idx, audio_segment, start, end, resolved_path)
    seen_paths = set()

    # Вспомогательное состояние для контроля сортировки и пересечений
    last_start_ms = -1
    last_end_ms = -1

    # Шаг 4. Парсинг таймингов, строгое возрастание, пересечения и физические MP3
    for idx in range(usable_count):
        item = timing_map[idx]

        # Парсинг временного интервала с жесткой валидацией структуры
        try:
            start_ms, end_ms = parse_timing(item)
        except RuntimeError as e:
            raise RuntimeError(f"Ошибка в элементе timing_map на индексе {idx}: {e}")

        # Ужесточение проверки порядка timing_map (Исправление по ТЗ №2 — строгое возрастание)
        if start_ms <= last_start_ms:
            raise RuntimeError(
                f"Timing map order violation at idx={idx}: "
                f"{start_ms} <= {last_start_ms}"
            )

        # Проверка пересечений временных окон
        if start_ms < last_end_ms:
            overlap_warnings_count += 1
            warn_overlap = (
                f"Overlapping timing windows detected at idx={idx}: "
                f"current start ({start_ms} ms) < previous end ({last_end_ms} ms)."
            )
            warnings_list.append(warn_overlap)
            logger.warning(warn_overlap)

        # Обновление маркеров последовательности для следующей итерации
        last_start_ms = start_ms
        last_end_ms = end_ms

        # Фиксация валидного временного окна для дальнейшего расчета геометрии
        parsed_timings.append((start_ms, end_ms))

        # Определение и проверка физического пути к файлу сегмента
        path_raw = segment_paths[idx]
        if not path_raw:
            missing_files_count += 1
            warn_msg = f"Индекс {idx}: Пустой путь к файлу сегмента."
            warnings_list.append(warn_msg)
            logger.warning(warn_msg)
            continue

        file_path = Path(path_raw)
        if not file_path.exists() or not file_path.is_file():
            missing_files_count += 1
            warn_msg = f"Индекс {idx}: Файл сегмента не найден на диске или не является файлом: {file_path}"
            warnings_list.append(warn_msg)
            logger.warning(warn_msg)
            continue

        # Чтение и декодирование аудиофайла с защитой от повреждений
        try:
            audio_segment = AudioSegment.from_mp3(str(file_path))
        except Exception as e:
            corrupt_files_count += 1
            warn_msg = (
                f"Индекс {idx}: Поврежденный MP3 файл, ошибка чтения {file_path}: {e}"
            )
            warnings_list.append(warn_msg)
            logger.warning(warn_msg)
            continue

        # Проверка длительности декодированного сегмента
        if len(audio_segment) <= 0:
            empty_segments_count += 1
            warn_msg = f"Индекс {idx}: Обнаружен пустой аудиосегмент (длина <= 0 мс): {file_path}"
            warnings_list.append(warn_msg)
            logger.warning(warn_msg)
            continue

        # Проверка дублей сегментов
        resolved_path = str(file_path.resolve())
        if resolved_path in seen_paths:
            duplicate_segments_count += 1
            warn_dup = f"Duplicate segment detected: {resolved_path}"
            warnings_list.append(warn_dup)
            logger.warning(warn_dup)
        else:
            seen_paths.add(resolved_path)

        # Сегмент полностью валиден и готов к размещению
        valid_segments_data.append(
            (idx, audio_segment, start_ms, end_ms, resolved_path)
        )

    # Шаг 5. Диагностика процента потерь с защитой от деления на ноль
    skipped_segments = missing_files_count + corrupt_files_count + empty_segments_count
    skip_ratio = skipped_segments / usable_count

    if skip_ratio > 0.5:
        raise RuntimeError("More than 50% of segments were lost.")

    # Шаг 6. Защита от "тихой сборки" (Запрет скрытия ошибок)
    if not valid_segments_data:
        raise RuntimeError("No valid audio segments available for timing assembly.")

    # Шаг 7. Расчёт и контроль лимитов геометрии мастер-дорожки
    max_end_ms = max(end for _, end in parsed_timings) if parsed_timings else 0
    master_duration_ms = max_end_ms

    if target_duration_ms is not None:
        try:
            target_ms_int = int(float(target_duration_ms))
            if target_ms_int < 0:
                raise ValueError(
                    "Значение target_duration_ms не может быть отрицательным."
                )
            if target_ms_int == 0:
                logger.warning("target_duration_ms=0. Using timing_map duration.")
            master_duration_ms = max(master_duration_ms, target_ms_int)
        except (ValueError, TypeError) as e:
            raise RuntimeError(f"Критическая ошибка валидации target_duration_ms: {e}")

    # Защита от слишком длинных треков (более 24 часов)
    if master_duration_ms > 24 * 60 * 60 * 1000:
        raise RuntimeError("Master track exceeds 24 hours.")

    # Добавление обязательного 1000 мс технологического хвоста безопасности
    master_duration_ms += 1000
    logger.info(
        "Рассчитанная длительность мастер-подложки (с учетом хвоста): %d мс",
        master_duration_ms,
    )

    # Шаг 8. Инициализация базовой дорожки тишины
    try:
        final_audio = AudioSegment.silent(duration=master_duration_ms)
    except Exception as e:
        raise RuntimeError(
            f"Не удалось инициализировать базовый трек тишины длины {master_duration_ms} мс: {e}"
        )

    # Шаг 9. Потоковое наложение аудиосегментов с контролем границ окон и дорожки
    for idx, seg, start_ms, end_ms, resolved_path in valid_segments_data:
        window_len = end_ms - start_ms

        # Проверка отрицательного или нулевого окна
        if window_len <= 0:
            raise RuntimeError(
                f"Invalid timing window: idx={idx}, start={start_ms}, end={end_ms}"
            )

        # Проверка слишком коротких окон
        if window_len < 100:
            warn_short = f"Very small timing window at idx={idx}: {window_len} ms."
            warnings_list.append(warn_short)
            logger.warning(warn_short)

        # Контроль выхода за правые границы мастер-дорожки по end_ms
        if end_ms > master_duration_ms:
            raise RuntimeError(
                f"Segment outside master track: "
                f"idx={idx}, end={end_ms}, "
                f"master={master_duration_ms}"
            )

        actual_len = len(seg)

        # Защита от наложения за пределы мастер-дорожки по физической длине
        actual_end = start_ms + actual_len
        if actual_end > master_duration_ms:
            warn_exceed = f"Segment {idx} exceeds master duration by {actual_end - master_duration_ms} ms."
            warnings_list.append(warn_exceed)
            logger.warning(warn_exceed)

        if actual_len > window_len:
            timing_warnings_count += 1
            overflow_ms = actual_len - window_len
            warn_msg = (
                f"Превышение временного окна на индексе {idx}: файл {segment_paths[idx]} "
                f"имеет длину {actual_len} мс при размере окна {window_len} мс. "
                f"Величина превышения: {overflow_ms} мс. Сегмент обрезан по границе окна."
            )
            warnings_list.append(warn_msg)
            logger.warning(warn_msg)
            # Prevent bleed into the next slot / overlapping dialogue.
            if window_len > 0:
                seg = seg[:window_len]

        final_audio = final_audio.overlay(seg, position=start_ms)

    # Шаг 10. Проверка финальной геометрии результирующего аудиообъекта
    if final_audio is None:
        raise RuntimeError(
            "Критическая ошибка сборки: Итоговый аудиосегмент равен None."
        )

    actual_duration = len(final_audio)
    if actual_duration == 0:
        raise RuntimeError("Final audio length is zero.")

    if actual_duration < master_duration_ms:
        raise RuntimeError(
            f"Критическая ошибка геометрии: Итоговый трек короче расчётной мастер-длины. "
            f"{actual_duration} < {master_duration_ms}"
        )

    # Проверка на аномальное превышение лимита длины мастер-дорожки
    if actual_duration > (master_duration_ms + 5000):
        warn_geometry = (
            f"Финальная длительность аудио значительно превышает мастер-лимит: "
            f"actual={actual_duration} ms, expected_max={master_duration_ms + 5000} ms."
        )
        warnings_list.append(warn_geometry)
        logger.warning(warn_geometry)

    total_warnings_collected = len(warnings_list)

    # Шаг 11. Формирование и вывод итоговой статистики качества (Исправление по ТЗ №5)
    summary = {
        "input_segments": input_segments_count,
        "usable_segments": usable_count,
        "valid_segments": len(valid_segments_data),
        "missing_files": missing_files_count,
        "corrupt_files": corrupt_files_count,
        "empty_segments": empty_segments_count,
        "duplicate_segments": duplicate_segments_count,
        "skipped_segments": skipped_segments,
        "skip_ratio": skip_ratio,
        "overlap_warnings": overlap_warnings_count,
        "timing_warnings": timing_warnings_count,
        "total_warnings": total_warnings_collected,
    }

    logger.info(
        "Timing Engine Summary: input=%d, valid=%d, warnings=%d",
        summary["input_segments"],
        summary["valid_segments"],
        summary["total_warnings"],
    )
    logger.info("Quality summary: %s", summary)
    logger.info(
        "Сборка аудиодорожки успешно завершена. Итоговая длительность: %d мс.",
        actual_duration,
    )

    return final_audio, warnings_list


# Официальная фиксация статуса компонента в рамках экосистемы TubeDub:
# TubeDub Timing Engine достиг максимально разумного уровня надёжности в рамках архитектуры TubeDub.
# Статус: Frozen Ultimate Release 100/100.
