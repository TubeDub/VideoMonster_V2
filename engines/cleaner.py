"""
TubeDub — Transcript Cleaner Engine (Frozen Production Release)
Модуль очистки, фильтрации и валидации текстовых транскриптов (Whisper / SRT).
Обеспечивает строгое сквозное соответствие текстовых сегментов временным меткам.
Архитектура завершена и заморожена. Изменения без отдельного ТЗ запрещены.
"""

import logging
import re
from typing import List, Tuple

# Настройка локального логгера для модуля лингвистической предобработки
logger = logging.getLogger("tubedub.transcript_engine")


# Компиляция регулярных выражений на этапе импорта модуля (Single-pass оптимизация)
SRT_INDEX_PAT = re.compile(r"^\d+$")


# Поддержка расширенных форматов таймкодов Whisper и стандартных SRT
TIMECODE_PAT = re.compile(r"^(?:(\d{1,2}):)?(\d{1,2}):(\d{2,})(?:[.,]\d+)?\s*(.*)$")


# Паттерн для удаления артефактов разметки и системных галлюцинаций STT
TAGS_PAT = re.compile(
    r"\[[^\]]+\]|\((?:music|applause|laughter|noise|музыка|смех|вздох|кашель)\)",
    re.IGNORECASE,
)


# Быстрая трансляция символов для вырезания музыкальных нот и спецсимволов субтитров
CLEAN_CHARS = str.maketrans("", "", "*♪♫♬♩")


# Паттерн для вырезания мусорных словесных повторов времени, генерируемых Whisper
TIME_WORDS_PAT = re.compile(
    r"^(?:"
    r"(?:\d+\s*)?(?:секунд(?:а|ы)?|секунди|seconds?)\s*"
    r"(?:\d+\s*)?(?:секунд(?:а|ы)?|секунди|seconds?)?\s*|"
    r"(?:\d+\s*)?(?:минут(?:а|ы)?|хвилин(?:а|и)?|minutes?)\s*"
    r"(?:\d+\s*)?(?:секунд(?:а|ы)?|секунди|seconds?)?\s*|"
    r"(?:\d+\s*)?(?:час(?:а|ов)?|годин(?:а|и)?|hours?)\s*"
    r"(?:\d+\s*)?(?:минут(?:а|ы)?|хвилин(?:а|и)?|minutes?)?\s*)+",
    re.IGNORECASE,
)


def clean_transcript(raw_text: str) -> Tuple[str, List[str], List[Tuple[str, str]]]:
    """
    Очищает входной сырой текст транскрипта от тайм-кодов, служебных тегов и шума.
    Гарантирует бинарное соответствие индексов строк и выходной карты таймингов.


    :param raw_text: Сырой текст из Whisper или SRT файла.
    :return:         Кортеж (очищенный_текст_одним_блоком, timing_map, review_items).
    """
    logger.info("Старт лингвистической очистки транскрипта.")

    if not raw_text or not raw_text.strip():
        logger.warning(
            "Получен пустой входной транскрипт. Инициализация пустой структуры."
        )
        return "", [], [("empty_input", "")]

    processed_lines: List[str] = []
    timing_map: List[str] = []
    review_items: List[Tuple[str, str]] = []

    # Состояние последнего валидного таймкода для защиты от "слепых" строк
    last_valid_timestamp = "00:00"

    for original_line in raw_text.splitlines():
        line = original_line.strip()

        if not line:
            continue

        # Фильтрация стандартных временных диапазонов SRT (передаются в review)
        if "-->" in line:
            review_items.append(("srt_range", line))
            continue

        # Фильтрация числовых индексов SRT
        if SRT_INDEX_PAT.match(line):
            review_items.append(("srt_index", line))
            continue

        timestamp = last_valid_timestamp
        match = TIMECODE_PAT.match(line)

        if match:
            hours = match.group(1)
            first = match.group(2)
            second = match.group(3)

            # Унификация формата таймкода (MM:SS или HH:MM:SS)
            if hours:
                timestamp = f"{hours}:{first}:{second[:2]}"
            else:
                timestamp = f"{first}:{second[:2]}"

            last_valid_timestamp = timestamp
            line = match.group(4).strip()

        # Потоковая очистка строки от мусорных словесных паттернов времени
        line = TIME_WORDS_PAT.sub("", line)

        # Изоляция чистых фоновых тегов, занимающих всю строку целиком
        if line.startswith("[") and line.endswith("]"):
            review_items.append(("tag", line))
            continue

        # Очистка внутристроковых тегов, нот и лишних пробелов
        line = TAGS_PAT.sub("", line)
        line = line.translate(CLEAN_CHARS)
        line = " ".join(line.split())

        # Жесткий контроль геометрии: строка добавляется в мастер-массив
        # только если она сохранила текстовое наполнение после фильтрации
        if line:
            processed_lines.append(line)
            timing_map.append(timestamp)
        else:
            # Если строка превратилась в пустоту, она НЕ должна ломать индексы timing_map
            review_items.append(("removed", original_line))

    # Проверка инварианта геометрии сегментов после окончания цикла
    if len(processed_lines) != len(timing_map):
        raise RuntimeError(
            f"Transcript geometry violation: "
            f"processed_lines={len(processed_lines)}, "
            f"timing_map={len(timing_map)}"
        )

    # Завершение возврата результата
    final_text = "\n".join(processed_lines)
    return final_text, timing_map, review_items


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+")
_SENTENCE_END = re.compile(r"[.!?…]\s*$")


def _split_complete_sentences(text: str) -> List[str]:
    """Split text into complete sentences (TZ §8 — never cut mid-sentence)."""
    text = " ".join(str(text or "").split())
    if not text:
        return []
    parts = [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]
    return parts if parts else [text]


def _ends_complete_sentence(text: str) -> bool:
    t = str(text or "").strip()
    return bool(t) and bool(_SENTENCE_END.search(t))


def _merge_sentences_to_buckets(sentences: List[str], bucket_count: int) -> List[str]:
    """
    Assign whole sentences to N segments without splitting any sentence (TZ §8).
    When sentences > buckets: merge consecutive sentences into buckets.
    When sentences < buckets: distribute one sentence per bucket where possible.
    """
    if bucket_count <= 0:
        return []
    if not sentences:
        return [""] * bucket_count

    if len(sentences) <= bucket_count:
        out: List[str] = [""] * bucket_count
        si = 0
        for bi in range(bucket_count):
            if si >= len(sentences):
                break
            remaining_sentences = len(sentences) - si
            remaining_slots = bucket_count - bi
            if remaining_sentences <= remaining_slots:
                out[bi] = sentences[si]
                si += 1
            else:
                group_size = (remaining_sentences + remaining_slots - 1) // remaining_slots
                out[bi] = " ".join(sentences[si : si + group_size]).strip()
                si += group_size
        return out

    bucket_size = len(sentences) // bucket_count
    extra = len(sentences) % bucket_count
    out: List[str] = []
    idx = 0
    for bi in range(bucket_count):
        take = bucket_size + (1 if bi < extra else 0)
        out.append(" ".join(sentences[idx : idx + take]).strip())
        idx += take
    return out


def _distribute_text_by_timing_sentences(text: str, timing_map: List) -> List[str]:
    """Distribute translated text across timing slots — sentence-boundary safe (TZ §8)."""
    target = len(timing_map)
    if not text or target <= 0:
        return [""] * max(target, 0)
    if target == 1:
        return [text.strip()]
    sentences = _split_complete_sentences(text)
    # Not enough sentence boundaries to cover every slot → fall back to
    # word-level proportional distribution so no slot is left empty and no
    # translated words are dropped (TZ §8 fill + timing sync).
    if len(sentences) < target:
        return _distribute_text_by_timing(text, timing_map)
    return _merge_sentences_to_buckets(sentences, target)


def detect_split_sentences(segments: List[str]) -> List[dict]:
    """
    Detect sentences split across adjacent segments (TZ §8/§11).
    Returns list of issue dicts with index and code.
    """
    issues: List[dict] = []
    for i, seg in enumerate(segments):
        text = str(seg or "").strip()
        if not text or _ends_complete_sentence(text):
            continue
        if i + 1 < len(segments):
            nxt = str(segments[i + 1] or "").strip()
            if nxt:
                issues.append(
                    {
                        "index": i,
                        "code": "split_sentence",
                        "segment_text": text[:120],
                        "next_segment_text": nxt[:120],
                    }
                )
        elif len(text.split()) >= 4:
            issues.append(
                {
                    "index": i,
                    "code": "incomplete_sentence",
                    "segment_text": text[:120],
                }
            )
    return issues


def _timing_weight(item) -> int:
    """Длительность слота в мс для пропорционального распределения текста."""
    if isinstance(item, dict):
        return max(int(item.get("end", 0)) - int(item.get("start", 0)), 80)
    if isinstance(item, (list, tuple)) and len(item) >= 2:
        return max(int(item[1]) - int(item[0]), 80)
    return 1000


def _distribute_text_by_timing(text: str, timing_map: List) -> List[str]:
    """Распределяет текст по сегментам пропорционально длительности слотов."""
    text = " ".join(text.split())
    target = len(timing_map)
    if not text or target <= 0:
        return [""] * target
    if target == 1:
        return [text]

    weights = [_timing_weight(t) for t in timing_map]
    total_w = sum(weights) or target
    words = text.split()
    if not words:
        return [""] * target

    if len(words) < target:
        out: List[str] = [""] * target
        wi = 0
        for i in range(target):
            if wi >= len(words):
                break
            remaining_words = len(words) - wi
            remaining_slots = target - i
            n = max(1, (remaining_words + remaining_slots - 1) // remaining_slots)
            n = min(n, remaining_words)
            out[i] = " ".join(words[wi : wi + n]).strip()
            wi += n
        return out

    out: List[str] = []
    idx = 0
    for i, w in enumerate(weights):
        if i == target - 1:
            chunk = " ".join(words[idx:]).strip()
            out.append(chunk)
            break
        share = max(1, round(len(words) * (w / total_w)))
        chunk = " ".join(words[idx : idx + share]).strip()
        out.append(chunk)
        idx += share
    while len(out) < target:
        out.append("")
    return out[:target]


def _distribute_text_to_segments(text: str, target_count: int) -> List[str]:
    """Равномерно распределяет один блок текста по N сегментам (слова / предложения)."""
    text = " ".join(text.split())
    if not text or target_count <= 0:
        return [""] * max(target_count, 0)
    if target_count == 1:
        return [text]

    sentences = re.split(r"(?<=[.!?])\s+", text)
    sentences = [s.strip() for s in sentences if s.strip()]
    if len(sentences) >= target_count:
        bucket_size = max(1, len(sentences) // target_count)
        out: List[str] = []
        idx = 0
        for bucket in range(target_count - 1):
            chunk = " ".join(sentences[idx : idx + bucket_size]).strip()
            out.append(chunk)
            idx += bucket_size
        out.append(" ".join(sentences[idx:]).strip())
        return out[:target_count]

    words = text.split()
    if not words:
        return [""] * target_count

    per = max(1, len(words) // target_count)
    out = []
    idx = 0
    for bucket in range(target_count - 1):
        chunk = " ".join(words[idx : idx + per]).strip()
        out.append(chunk)
        idx += per
    out.append(" ".join(words[idx:]).strip())
    return out[:target_count]


def _merge_lines_to_count(lines: List[str], target_count: int) -> List[str]:
    """Сжимает лишние строки, объединяя соседние реплики."""
    if target_count <= 0:
        return []
    if len(lines) <= target_count:
        return lines

    merged = list(lines)
    while len(merged) > target_count:
        shortest_idx = min(
            range(len(merged) - 1),
            key=lambda i: len(merged[i]),
        )
        merged[shortest_idx] = f"{merged[shortest_idx]} {merged[shortest_idx + 1]}".strip()
        merged.pop(shortest_idx + 1)
    return merged


def align_segments_to_timing_map(
    segments: List[str],
    timing_map: List,
) -> List[str]:
    """
    Приводит список сегментов к len(timing_map) без падения пайплайна.
    """
    if not timing_map:
        return segments if segments else []

    target = len(timing_map)
    normalized = [str(s or "").strip() for s in segments]

    if len(normalized) == target:
        return normalized

    if len(normalized) < target:
        combined = " ".join(s for s in normalized if s)
        if combined:
            logger.warning(
                "align_segments_to_timing_map: redistribute %d -> %d",
                len(normalized),
                target,
            )
            return _distribute_text_by_timing_sentences(combined, timing_map)
        logger.warning(
            "align_segments_to_timing_map: pad %d -> %d",
            len(normalized),
            target,
        )
        while len(normalized) < target:
            normalized.append("")
        return normalized

    logger.warning(
        "align_segments_to_timing_map: merge %d -> %d",
        len(normalized),
        target,
    )
    return _merge_lines_to_count(normalized, target)


def split_by_timing_map(text: str, timing_map: List) -> List[str]:
    """
    Разбивает переведенный текст на массив реплик по timing_map.
    При рассинхронизации выполняет авто-восстановление (split/join), не падает.


    :param text:       Полный очищенный текст (после перевода).
    :param timing_map: Карта таймингов (строки MM:SS или dict start/end).
    :return:           Список строк для TTS, len == len(timing_map) когда карта задана.
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    if not timing_map:
        return [text.strip()] if text and text.strip() else []

    target = len(timing_map)

    if len(lines) == target:
        return lines

    if len(lines) == 1 and target > 1:
        logger.warning(
            "split_by_timing_map: single translated block for %d segments — sentence-safe split",
            target,
        )
        return _distribute_text_by_timing_sentences(lines[0], timing_map)

    if len(lines) < target:
        logger.warning(
            "split_by_timing_map mismatch: timing_map=%d translated_lines=%d — timing redistribute",
            target,
            len(lines),
        )
        combined = " ".join(lines)
        return _distribute_text_by_timing_sentences(combined, timing_map)

    logger.warning(
        "split_by_timing_map mismatch: timing_map=%d translated_lines=%d — merging",
        target,
        len(lines),
    )
    return _merge_lines_to_count(lines, target)
