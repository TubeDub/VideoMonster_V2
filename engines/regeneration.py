"""Regeneration engine — single segment TTS + timing fit (Phase 1)."""

from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

APP_DIR = Path(__file__).parent.parent.resolve()
OUTPUT_DIR = APP_DIR / "output"


def _parse_timing(item: Any) -> tuple[int, int]:
    if isinstance(item, dict):
        return int(item.get("start", item.get("start_ms", 0))), int(
            item.get("end", item.get("end_ms", 0))
        )
    if isinstance(item, (list, tuple)) and len(item) >= 2:
        return int(item[0]), int(item[1])
    return 0, 0


def _overflow_status(overflow_pct: float) -> str:
    if overflow_pct <= 5:
        return "green"
    if overflow_pct <= 15:
        return "yellow"
    return "red"


def regenerate_segment(
    segment: dict[str, Any],
    *,
    timing_map: list[Any] | None = None,
    voice: str,
    lang: str = "ru",
    source_hint: str = "",
    tts_rate: str | None = None,
    tts_pitch: str | None = None,
    use_soft_sync: bool | None = None,
    app_dir: Path | None = None,
) -> dict[str, Any]:
    """
    Regenerate TTS for one segment and fit to timing slot.
    Updates segment dict in-place fields: text, file, tts_ms, overflow_pct, timing_meta.
    """
    app = Path(app_dir or APP_DIR)
    out_dir = app / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    idx = int(segment.get("index", segment.get("id", 0)))
    text = str(segment.get("text") or segment.get("translation") or "").strip()
    if not text:
        return {"ok": False, "error": "empty_text", "segment_index": idx}

    start_ms, end_ms = _parse_timing(segment.get("timing") or {})
    if timing_map and idx < len(timing_map):
        start_ms, end_ms = _parse_timing(timing_map[idx])
    elif segment.get("start_ms") is not None:
        start_ms = int(segment["start_ms"])
        end_ms = int(segment.get("end_ms") or start_ms + 3000)

    emotion = segment.get("emotion") or (segment.get("tts_emotion") or {}).get("emotion")
    word_map = segment.get("source_word_map") or segment.get("word_map")

    if use_soft_sync is None:
        from engines.soft_sync import is_soft_sync_enabled

        use_soft_sync = is_soft_sync_enabled()

    old_file = segment.get("file")
    result: dict[str, Any]

    if use_soft_sync:
        from engines.soft_sync import fit_segment_with_retry

        result = fit_segment_with_retry(
            text,
            voice=voice,
            slot_start_ms=start_ms,
            slot_end_ms=end_ms,
            lang=lang,
            source_hint=source_hint,
            word_map=word_map,
            tts_rate=tts_rate,
            tts_pitch=tts_pitch,
            emotion=str(emotion) if emotion else None,
        )
        if not result.get("ok") and result.get("file"):
            pass
        elif result.get("file"):
            work = Path(result.get("work_dir") or out_dir)
            src = work / result["file"]
            if src.is_file():
                dest_name = f"regen_{idx}_{uuid.uuid4().hex[:8]}.mp3"
                dest = out_dir / dest_name
                shutil.copy2(src, dest)
                result["file"] = dest_name
            fitted = work / result.get("fitted_file", "")
            if fitted.is_file():
                fitted_name = f"regen_{idx}_{uuid.uuid4().hex[:8]}_fit.wav"
                shutil.copy2(fitted, out_dir / fitted_name)
                result["fitted_file"] = fitted_name
    else:
        from engines.tts import generate_audio
        from engines.timing_fit import fit_segment_audio
        import tempfile

        files = generate_audio(
            text=text,
            voice=voice,
            segments=[text],
            rate=tts_rate,
            pitch=tts_pitch,
            emotion=str(emotion) if emotion else None,
        )
        if not files:
            return {"ok": False, "error": "tts_failed", "segment_index": idx}
        tts_name = files[0]
        tts_path = out_dir / Path(tts_name).name
        work = Path(tempfile.mkdtemp(prefix="regen_"))
        fitted_path, fit_meta = fit_segment_audio(
            tts_path,
            start_ms,
            end_ms,
            work_dir=work,
            allow_atempo=bool(segment.get("allow_atempo", False)),
        )
        from pydub import AudioSegment

        tts_ms = len(AudioSegment.from_file(str(tts_path)))
        fitted_ms = len(AudioSegment.from_file(str(fitted_path)))
        slot_ms = max(0, end_ms - start_ms)
        overflow_ms = max(0, fitted_ms - slot_ms)
        result = {
            "ok": overflow_ms <= 40,
            "text": text,
            "file": tts_name,
            "fitted_file": Path(fitted_path).name,
            "tts_ms": tts_ms,
            "fitted_ms": fitted_ms,
            "slot_ms": slot_ms,
            "overflow_ms": overflow_ms,
            "overflow_pct": round(100.0 * overflow_ms / max(slot_ms, 1), 1),
            "meta": fit_meta,
        }
        shutil.copy2(fitted_path, out_dir / result["fitted_file"])

    if old_file and result.get("file") and old_file != result["file"]:
        try:
            (out_dir / Path(old_file).name).unlink(missing_ok=True)
        except OSError:
            pass

    overflow_pct = float(result.get("overflow_pct") or 0)
    segment["text"] = result.get("text") or text
    segment["file"] = result.get("file")
    segment["fitted_file"] = result.get("fitted_file")
    segment["tts_ms"] = result.get("tts_ms")
    segment["fitted_ms"] = result.get("fitted_ms")
    segment["overflow_ms"] = result.get("overflow_ms", 0)
    segment["overflow_pct"] = overflow_pct
    segment["container_status"] = _overflow_status(overflow_pct)
    segment["timing_meta"] = result.get("meta") or {}
    segment["start_ms"] = start_ms
    segment["end_ms"] = end_ms

    if emotion:
        from engines.emotion_tagger import tts_params_for_emotion

        segment["tts_emotion"] = {
            "emotion": emotion,
            "tts": tts_params_for_emotion({"emotion": emotion}),
        }
    if segment.get("tts_emotion"):
        segment["intonation"] = (segment.get("tts_emotion") or {}).get("intonation") or segment.get("intonation") or {}

    logger.info(
        "regeneration: idx=%d overflow=%.1f%% file=%s",
        idx,
        overflow_pct,
        segment.get("file"),
    )
    return {"ok": True, "segment": segment, **result}


def auto_fix_segment(
    segment: dict[str, Any],
    *,
    timing_map: list[Any] | None = None,
    voice: str,
    lang: str = "ru",
    source_hint: str = "",
    **kwargs: Any,
) -> dict[str, Any]:
    """Auto-fix: regenerate with soft_sync loop enabled."""
    return regenerate_segment(
        segment,
        timing_map=timing_map,
        voice=voice,
        lang=lang,
        source_hint=source_hint,
        use_soft_sync=True,
        **kwargs,
    )
