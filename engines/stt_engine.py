"""
TubeDub / VideoMonster V2 — STT Engine
Быстрое распознавание речи через Faster-Whisper с кэшированием моделей.




Приоритет:
    1. faster-whisper
    2. openai-whisper (резерв)




Совместим с существующим auto_dub_api.py.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_MODEL_CACHE: dict = {}


# ─────────────────────────────────────────────
# Публичный API
# ─────────────────────────────────────────────


def transcribe(
    audio_path: str,
    language: str | None = None,
    model_size: str = "tiny",
    *,
    word_timestamps: bool | None = None,
) -> tuple[str, str, list[dict[str, int]], str]:
    """
    Распознаёт речь.

    Возвращает:
        clean_text,
        srt_content,
        timing_map,   # each entry may include "words": [{text, start_ms, end_ms}, ...]
        detected_lang
    """
    if word_timestamps is None:
        from engines.word_timing_map.config import whisper_word_timestamps_enabled

        word_timestamps = whisper_word_timestamps_enabled()

    try:
        return _transcribe_faster_whisper(
            audio_path,
            language,
            model_size,
            word_timestamps=word_timestamps,
        )
    except ImportError:
        pass

    try:
        return _transcribe_openai_whisper(
            audio_path,
            language,
            model_size,
            word_timestamps=word_timestamps,
        )
    except ImportError:
        pass

    raise ImportError(
        "Установите движок распознавания:\n"
        "pip install faster-whisper\n"
        "или\n"
        "pip install openai-whisper"
    )


def check_available() -> tuple[bool, str]:
    """
    Проверяет доступность STT.
    """

    try:
        import faster_whisper  # noqa

        return True, "faster-whisper"
    except ImportError:
        pass

    try:
        import whisper  # noqa

        return True, "openai-whisper"
    except ImportError:
        pass

    return False, ""


# ─────────────────────────────────────────────
# Faster Whisper
# ─────────────────────────────────────────────


def _get_faster_model(model_size: str):
    """
    Кэширует модели.
    Загружает только один раз — в папку проекта models/huggingface.
    """
    from faster_whisper import WhisperModel  # noqa: F401 — availability

    cache_key = model_size
    if model_size not in _MODEL_CACHE:
        from pathlib import Path

        from engines.model_manager.downloader import load_whisper

        app_dir = Path(__file__).resolve().parent.parent
        _MODEL_CACHE[cache_key] = load_whisper(app_dir, model_size)

    return _MODEL_CACHE[cache_key]


def _transcribe_faster_whisper(
    audio_path: str,
    language: str | None,
    model_size: str,
    *,
    word_timestamps: bool = False,
) -> tuple[str, str, list[dict[str, int]], str]:

    model = _get_faster_model(model_size)

    beam_size = 1 if model_size == "tiny" else 5

    segments_gen, info = model.transcribe(
        audio_path,
        language=language or None,
        beam_size=beam_size,
        vad_filter=True,
        word_timestamps=word_timestamps,
    )

    text_lines: list[str] = []
    srt_blocks: list[str] = []
    timing_map: list[dict[str, int]] = []

    from engines.word_timing_map.extract import words_from_faster_whisper_segment

    for i, seg in enumerate(segments_gen, 1):

        text = seg.text.strip()

        if not text:
            continue

        start_ms = int(round(seg.start * 1000))
        end_ms = int(round(seg.end * 1000))

        start_srt = _sec_to_srt(seg.start)
        end_srt = _sec_to_srt(seg.end)

        entry: dict = {"start": start_ms, "end": end_ms}
        if word_timestamps:
            words = words_from_faster_whisper_segment(seg)
            if words:
                entry["words"] = [w.to_dict() for w in words]

        text_lines.append(text)
        timing_map.append(entry)

        srt_blocks.append(f"{i}\n" f"{start_srt} --> {end_srt}\n" f"{text}\n")

    return (
        "\n".join(text_lines),
        "\n".join(srt_blocks),
        timing_map,
        getattr(info, "language", language or "unknown"),
    )


# ─────────────────────────────────────────────
# OpenAI Whisper (резерв)
# ─────────────────────────────────────────────


def _transcribe_openai_whisper(
    audio_path: str,
    language: str | None,
    model_size: str,
    *,
    word_timestamps: bool = False,
) -> tuple[str, str, list[dict[str, int]], str]:

    import whisper

    logger.info("[STT] Using OpenAI Whisper: %s", model_size)

    model = whisper.load_model(model_size)

    transcribe_kw: dict = {
        "language": language or None,
        "verbose": False,
    }
    if word_timestamps:
        transcribe_kw["word_timestamps"] = True

    result = model.transcribe(audio_path, **transcribe_kw)

    text_lines: list[str] = []
    srt_blocks: list[str] = []
    timing_map: list[dict[str, int]] = []

    from engines.word_timing_map.extract import words_from_openai_whisper_segment

    for i, seg in enumerate(result.get("segments", []), 1):

        text = seg["text"].strip()

        if not text:
            continue

        start_ms = int(round(seg["start"] * 1000))
        end_ms = int(round(seg["end"] * 1000))

        start_srt = _sec_to_srt(seg["start"])
        end_srt = _sec_to_srt(seg["end"])

        entry: dict = {"start": start_ms, "end": end_ms}
        if word_timestamps:
            words = words_from_openai_whisper_segment(seg)
            if words:
                entry["words"] = [w.to_dict() for w in words]

        text_lines.append(text)
        timing_map.append(entry)

        srt_blocks.append(f"{i}\n" f"{start_srt} --> {end_srt}\n" f"{text}\n")

    return (
        "\n".join(text_lines),
        "\n".join(srt_blocks),
        timing_map,
        result.get("language", language or "unknown"),
    )


# ─────────────────────────────────────────────
# Утилиты времени
# ─────────────────────────────────────────────


def _sec_to_timing(sec: float) -> str:
    """
    float → М:СС или Ч:ММ:СС
    """

    sec = max(0.0, sec)

    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)

    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"

    return f"{m}:{s:02d}"


def _sec_to_srt(sec: float) -> str:
    """
    float → ЧЧ:ММ:СС,ммм
    """

    sec = max(0.0, sec)

    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)

    ms = int(round((sec - int(sec)) * 1000))

    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
