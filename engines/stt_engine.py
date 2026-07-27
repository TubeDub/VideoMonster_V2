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

    # Prefer small for CJK — but only if prepared (no hard-fail mid-dub).
    lang0 = str(language or "").split("-")[0].lower()
    if lang0 in ("zh", "ja", "ko", "yue") and model_size in ("tiny", "base", ""):
        bumped = _best_prepared_cjk_model(model_size or "tiny")
        if bumped != (model_size or "tiny"):
            logger.info(
                "[STT] CJK lang=%s using Whisper %s (requested %s)",
                lang0 or "?",
                bumped,
                model_size or "tiny",
            )
            model_size = bumped

    try:
        return _transcribe_faster_whisper(
            audio_path,
            language,
            model_size,
            word_timestamps=word_timestamps,
        )
    except ImportError:
        pass
    except Exception as exc:
        # CJK bump / missing small must not kill the job — fall back to tiny
        from engines.model_manager.runtime import ModelNotPreparedError

        if isinstance(exc, ModelNotPreparedError) and model_size != "tiny":
            logger.warning(
                "[STT] Whisper %s not prepared (%s) — falling back to tiny",
                model_size,
                exc,
            )
            try:
                return _transcribe_faster_whisper(
                    audio_path,
                    language,
                    "tiny",
                    word_timestamps=word_timestamps,
                )
            except Exception as retry_exc:
                logger.error("[STT] tiny fallback failed: %s", retry_exc)
                raise
        # mkl_malloc / OOM — retry with tiny after clearing cache
        if isinstance(exc, RuntimeError):
            msg = str(exc).lower()
            if any(
                t in msg
                for t in ("mkl_malloc", "failed to allocate", "out of memory", "oom")
            ):
                logger.error("[STT] Whisper OOM on %s: %s — retry tiny", model_size, exc)
                try:
                    from engines.model_manager.downloader import clear_whisper_cache

                    clear_whisper_cache()
                    _MODEL_CACHE.clear()
                except Exception:
                    _MODEL_CACHE.clear()
                if model_size != "tiny":
                    try:
                        return _transcribe_faster_whisper(
                            audio_path,
                            language,
                            "tiny",
                            word_timestamps=word_timestamps,
                        )
                    except Exception as retry_exc:
                        logger.error("[STT] tiny retry failed: %s", retry_exc)
                raise
        raise

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


def _best_prepared_cjk_model(requested: str = "tiny") -> str:
    """Pick the best Whisper size available without mid-run downloads."""
    from pathlib import Path

    from engines.model_manager.downloader import verify_whisper

    app_dir = Path(__file__).resolve().parent.parent
    for size in ("small", "base", requested or "tiny", "tiny"):
        try:
            if size and verify_whisper(app_dir, size):
                return size
        except Exception:
            continue
    return "tiny"


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

    detected = str(getattr(info, "language", language or "unknown") or "unknown")
    det0 = detected.split("-")[0].lower()
    # Auto-detect CJK with tiny/base → re-run on best prepared larger model.
    # Never let a failed upgrade abort the already-good tiny/base pass
    # (verify_whisper can be optimistic; load may still raise).
    if (
        not language
        and det0 in ("zh", "ja", "ko", "yue")
        and model_size in ("tiny", "base")
    ):
        better = _best_prepared_cjk_model(model_size)
        if better != model_size:
            logger.info(
                "[STT] Re-transcribe with %s after CJK detect=%s (was %s)",
                better,
                det0,
                model_size,
            )
            try:
                return _transcribe_faster_whisper(
                    audio_path,
                    det0,
                    better,
                    word_timestamps=word_timestamps,
                )
            except Exception as upgrade_exc:
                logger.warning(
                    "[STT] CJK upgrade %s→%s failed (%s) — keeping %s result",
                    model_size,
                    better,
                    upgrade_exc,
                    model_size,
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
        detected,
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
