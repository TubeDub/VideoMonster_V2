"""Soft Sync — hard anchor start, soft stretch end, shorten/expand loop (TZ §5)."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MAX_ITERATIONS = 3
_UNDERFILL_EXPAND_RATIO = 0.82


def is_soft_sync_enabled() -> bool:
    from engines.core.feature_flags import is_enabled

    return is_enabled("soft_sync", developer_session=True)


def is_word_timing_enabled() -> bool:
    from engines.core.feature_flags import is_enabled

    return is_enabled("word_timing", developer_session=True)


def shorten_text_for_slot(
    text: str,
    *,
    slot_ms: int,
    lang: str = "ru",
    source_hint: str = "",
    skip_if_timing_agent: bool = False,
) -> str:
    """
    Text shortening BEFORE atempo — meaning-safe rephrase only (no tail clip).

    Slot Fit must not mutate text (TZ §12); this helper is for pre-TTS paths only.
    When Timing Agent already adapted text (manifest path), skip duplicate shorten.
    """
    if skip_if_timing_agent:
        return text
    if not text or slot_ms <= 0:
        return text

    try:
        from engines.semantic_adaptation import estimate_tts_duration_ms
        from engines.translation_adapt import adapt_for_duration

        est = estimate_tts_duration_ms(text, lang)
        target = max(200, int(slot_ms) - 40)
        if est <= target:
            return text
        adapted = adapt_for_duration(
            text,
            est,
            target,
            source_hint=source_hint,
            stage="auto",
            tgt_lang=lang,
        )
        if adapted != text:
            est2 = estimate_tts_duration_ms(adapted, lang)
            if est2 <= target or est2 < est:
                return adapted
    except Exception as exc:
        logger.debug("soft_sync: meaning-safe shorten skipped: %s", exc)
    return text


def expand_text_for_slot(
    text: str,
    *,
    slot_ms: int,
    lang: str = "ru",
    source_hint: str = "",
) -> str:
    """Slight expansion when TTS is much shorter than slot (natural pacing)."""
    if not text or slot_ms <= 0:
        return text
    try:
        from engines.semantic_optimizer import optimize_expand_for_slot
        from engines.translation_adapt import llm_rephrase_available

        if llm_rephrase_available():
            res = optimize_expand_for_slot(
                text,
                source_hint=source_hint,
                slot_ms=slot_ms,
                tgt_lang=lang,
                max_rounds=1,
            )
            return res.text
    except Exception:
        pass
    return text


def _word_anchor_offset_ms(word_map: dict[str, Any] | None, slot_start_ms: int) -> int:
    """Offset from slot start to first spoken word (hard anchor)."""
    if not word_map:
        return 0
    words = word_map.get("words") or []
    if not words:
        return 0
    first = words[0]
    w_start = int(first.get("start_ms", first.get("start", 0)))
    seg_start = int(word_map.get("segment_start_ms", slot_start_ms))
    return max(0, w_start - seg_start)


def apply_hard_anchor_soft_end(
    audio_path: str | Path,
    slot_start_ms: int,
    slot_end_ms: int,
    work_dir: Path,
    *,
    word_map: dict[str, Any] | None = None,
    max_stretch_ratio: float = 1.08,
) -> tuple[str, dict[str, Any]]:
    """
    Hard anchor at first word / slot start; pad or gentle fit toward slot end.
    Does not shift segment start in the mix — only internal lead-in silence + tail pad.
    """
    from pydub import AudioSegment

    from engines.timing_fit import fit_segment_audio

    slot_ms = max(0, slot_end_ms - slot_start_ms)
    src = Path(audio_path)
    audio = AudioSegment.from_file(str(src))
    tts_ms = len(audio)
    anchor_ms = _word_anchor_offset_ms(word_map, slot_start_ms)

    meta: dict[str, Any] = {
        "strategy": "hard_anchor",
        "slot_ms": slot_ms,
        "tts_ms": tts_ms,
        "anchor_ms": anchor_ms,
        "stretch_ratio": 1.0,
        "pause_added_ms": 0,
        "overflow_ms": 0,
    }

    if slot_ms <= 0:
        return str(src), meta

    if anchor_ms > 0 and anchor_ms < min(slot_ms, 800):
        audio = AudioSegment.silent(duration=anchor_ms) + audio
        tts_ms = len(audio)
        meta["strategy"] = "hard_anchor+word_offset"
        meta["tts_ms"] = tts_ms

    tmp_in = work_dir / f"{src.stem}_anchored.wav"
    audio.export(tmp_in, format="wav")

    if tts_ms < slot_ms * _UNDERFILL_EXPAND_RATIO and tts_ms > 0:
        ratio = min(max_stretch_ratio, slot_ms / tts_ms)
        if ratio > 1.02:
            fitted, fit_meta = fit_segment_audio(
                tmp_in,
                slot_start_ms,
                slot_end_ms,
                work_dir=work_dir,
                allow_atempo=False,
                _skip_soft_sync=True,
            )
            meta["stretch_ratio"] = fit_meta.get("atempo", 1.0)
            meta["strategy"] = meta["strategy"] + "+soft_pad"
            meta["pause_added_ms"] = fit_meta.get("pause_added_ms", 0)
            meta["overflow_ms"] = fit_meta.get("overflow_ms", 0)
            meta.update({k: fit_meta.get(k) for k in ("fitted_ms", "effective_slot_ms")})
            return fitted, meta

    fitted, fit_meta = fit_segment_audio(
        tmp_in,
        slot_start_ms,
        slot_end_ms,
        work_dir=work_dir,
        allow_atempo=False,
        _skip_soft_sync=True,
    )
    meta.update(
        {
            "strategy": meta["strategy"] + "+fit",
            "atempo": fit_meta.get("atempo", 1.0),
            "pause_added_ms": fit_meta.get("pause_added_ms", 0),
            "overflow_ms": fit_meta.get("overflow_ms", 0),
            "fitted_ms": fit_meta.get("fitted_ms"),
        }
    )
    return fitted, meta


def apply_soft_stretch_end(
    audio_path: str | Path,
    slot_start_ms: int,
    slot_end_ms: int,
    work_dir: Path,
    *,
    max_stretch_ratio: float = 1.08,
    word_map: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Backward-compatible alias for hard anchor + soft end."""
    return apply_hard_anchor_soft_end(
        audio_path,
        slot_start_ms,
        slot_end_ms,
        work_dir,
        word_map=word_map,
        max_stretch_ratio=max_stretch_ratio,
    )


def _measure_audio_ms(path: str | Path) -> int:
    try:
        from pydub import AudioSegment

        return len(AudioSegment.from_file(str(path)))
    except Exception:
        return 0


def fit_segment_with_retry(
    text: str,
    *,
    voice: str,
    slot_start_ms: int,
    slot_end_ms: int,
    lang: str = "ru",
    source_hint: str = "",
    word_map: dict[str, Any] | None = None,
    tts_rate: str | None = None,
    tts_pitch: str | None = None,
    emotion: str | None = None,
    max_iterations: int = _MAX_ITERATIONS,
    work_dir: Path | None = None,
) -> dict[str, Any]:
    """
    Loop: shorten/expand → TTS → measure → retry until fits slot or max iterations.
    Returns dict with text, file, tts_ms, fitted_path, meta, overflow_pct.
    """
    from engines.tts import generate_audio

    slot_ms = max(0, slot_end_ms - slot_start_ms)
    work = work_dir or Path(tempfile.mkdtemp(prefix="soft_sync_"))
    work.mkdir(parents=True, exist_ok=True)

    cur_text = (text or "").strip()
    if not cur_text:
        return {"ok": False, "error": "empty_text", "text": "", "overflow_pct": 100.0}

    target_ms = max(200, slot_ms - 40)
    last_file: str | None = None
    iterations: list[dict[str, Any]] = []

    for attempt in range(max(1, max_iterations)):
        gen_kwargs: dict[str, Any] = {
            "text": cur_text,
            "voice": voice,
            "segments": [cur_text],
            "rate": tts_rate,
            "pitch": tts_pitch,
            "emotion": emotion,
        }
        files = generate_audio(**gen_kwargs)
        last_file = None
        if files:
            from engines.tts import OUTPUT_DIR

            src = OUTPUT_DIR / files[0]
            if src.is_file():
                dest = work / files[0]
                dest.write_bytes(src.read_bytes())
                last_file = str(dest)

        tts_ms = _measure_audio_ms(last_file) if last_file else 0

        iter_meta = {"attempt": attempt + 1, "text": cur_text, "tts_ms": tts_ms}
        iterations.append(iter_meta)

        if tts_ms <= 0:
            break

        overflow_ms = max(0, tts_ms - target_ms)
        underfill = tts_ms < target_ms * _UNDERFILL_EXPAND_RATIO

        if overflow_ms <= 40 and not underfill:
            break

        if attempt >= max_iterations - 1:
            break

        if overflow_ms > 40:
            cur_text = shorten_text_for_slot(cur_text, slot_ms=slot_ms, lang=lang)
            iter_meta["action"] = "shorten"
        elif underfill:
            cur_text = expand_text_for_slot(
                cur_text, slot_ms=slot_ms, lang=lang, source_hint=source_hint
            )
            iter_meta["action"] = "expand"

    if not last_file:
        return {
            "ok": False,
            "error": "tts_failed",
            "text": cur_text,
            "overflow_pct": 100.0,
            "iterations": iterations,
        }

    fitted_path, fit_meta = apply_hard_anchor_soft_end(
        last_file,
        slot_start_ms,
        slot_end_ms,
        work,
        word_map=word_map if is_word_timing_enabled() else None,
    )
    fitted_ms = _measure_audio_ms(fitted_path)
    overflow_ms = max(0, fitted_ms - slot_ms)
    overflow_pct = round(100.0 * overflow_ms / max(slot_ms, 1), 1)

    return {
        "ok": overflow_ms <= 40,
        "text": cur_text,
        "file": Path(last_file).name,
        "fitted_file": Path(fitted_path).name,
        "tts_ms": _measure_audio_ms(last_file),
        "fitted_ms": fitted_ms,
        "slot_ms": slot_ms,
        "overflow_ms": overflow_ms,
        "overflow_pct": overflow_pct,
        "meta": fit_meta,
        "iterations": iterations,
        "work_dir": str(work),
    }
