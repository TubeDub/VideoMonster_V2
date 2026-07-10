"""
Text Preparation — normalize text before TTS (does not change Marian/Naturalizer output meaning).

Pipeline slot: after Final / review approval, before TTS synthesis.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.engines.text_preparation")

_ABBR_RU = {
    "т.д.": "так далее",
    "т.п.": "тому подобное",
    "т.е.": "то есть",
    "т.к.": "так как",
    "др.": "другие",
    "пр.": "прочее",
    "г.": "год",
    "ул.": "улица",
}
_ABBR_UK = {
    "т.д.": "тощо далі",
    "т.п.": "то подібне",
    "т.е.": "тобто",
    "т.к.": "тому що",
    "вул.": "вулиця",
}
_ABBR_EN = {
    "e.g.": "for example",
    "i.e.": "that is",
    "etc.": "etcetera",
    "Dr.": "Doctor",
    "Mr.": "Mister",
    "Mrs.": "Missus",
}


def _abbr_map(lang: str) -> dict[str, str]:
    base = (lang or "en").split("-")[0].lower()
    if base == "uk":
        return {**_ABBR_RU, **_ABBR_UK}
    if base == "ru":
        return _ABBR_RU
    return _ABBR_EN


def _normalize_whitespace(text: str) -> str:
    return " ".join(str(text or "").split()).strip()


def _fix_punctuation(text: str) -> str:
    t = text
    t = re.sub(r"\s+([,.!?;:])", r"\1", t)
    t = re.sub(r"([,.!?;:])([^\s\d])", r"\1 \2", t)
    t = re.sub(r"\(\s+", "(", t)
    t = re.sub(r"\s+\)", ")", t)
    t = re.sub(r"…+", "…", t)
    t = re.sub(r"\.{4,}", "…", t)
    return t.strip()


def _expand_abbreviations(text: str, lang: str) -> str:
    """Expand only safe dotted abbreviations at word boundaries."""
    out = text
    for abbr, full in _abbr_map(lang).items():
        pattern = re.compile(r"(?<!\w)" + re.escape(abbr) + r"(?!\w)", re.IGNORECASE)
        out = pattern.sub(full, out)
    return out


def _normalize_numbers(text: str, lang: str) -> str:
    """Keep digits readable; normalize percent and ranges."""
    t = text
    t = re.sub(r"(\d)\s*%", r"\1%", t)
    t = re.sub(r"(\d)\s*-\s*(\d)", r"\1–\2", t)
    return t


def _fix_declension_glue(text: str, lang: str) -> str:
    """Light fixes for glued words common in MT output (ru/uk)."""
    if lang.split("-")[0] not in ("ru", "uk"):
        return text
    t = text
    t = re.sub(r"([а-яіїєґ])([А-ЯІЇЄҐ])", r"\1 \2", t)
    t = re.sub(r"([a-zA-Z])([а-яіїєґА-ЯІЇЄҐ])", r"\1 \2", t)
    t = re.sub(r"([а-яіїєґА-ЯІЇЄҐ])([a-zA-Z])", r"\1 \2", t)
    return _normalize_whitespace(t)


def _apply_stress_marks(text: str, lang: str, *, supports_stress: bool) -> str:
    """Optional stress hints — only when engine declares support (ElevenLabs/Azure future)."""
    if not supports_stress:
        return text
    return text


def prepare_text_for_tts(
    text: str,
    *,
    lang: str = "ru",
    tts_engine_id: str = "edge-offline",
) -> tuple[str, dict[str, Any]]:
    """Prepare one segment for TTS reading."""
    from engines.tts_engines.registry import get_engine

    original = str(text or "")
    if not original.strip():
        return original, {"changed": False}

    eng = get_engine(tts_engine_id)
    supports_stress = bool(getattr(eng, "supports_stress", False))

    steps: list[str] = []
    t = original
    t = _normalize_whitespace(t)
    steps.append("whitespace")
    t = _expand_abbreviations(t, lang)
    steps.append("abbreviations")
    t = _fix_punctuation(t)
    steps.append("punctuation")
    t = _normalize_numbers(t, lang)
    steps.append("numbers")
    t = _fix_declension_glue(t, lang)
    steps.append("declension_glue")
    t = _apply_stress_marks(t, lang, supports_stress=supports_stress)
    if supports_stress:
        steps.append("stress")

    changed = t != original
    return t, {"changed": changed, "steps": steps, "engine": tts_engine_id}


def prepare_segments_for_tts(
    segments: list[str],
    *,
    lang: str = "ru",
    tts_engine_id: str = "edge-offline",
    app_dir: Path | None = None,
    task_id: str = "",
) -> tuple[list[str], dict[str, Any]]:
    """Batch text preparation before TTS."""
    app_dir = app_dir or Path(__file__).resolve().parent.parent
    t0 = time.perf_counter()
    out: list[str] = []
    changed_count = 0
    for seg in segments:
        prepared, meta = prepare_text_for_tts(seg, lang=lang, tts_engine_id=tts_engine_id)
        out.append(prepared)
        if meta.get("changed"):
            changed_count += 1

    elapsed = time.perf_counter() - t0
    log_meta = {
        "segments": len(segments),
        "changed": changed_count,
        "elapsed_sec": round(elapsed, 3),
        "engine": tts_engine_id,
        "lang": lang,
    }
    _log_preparation(app_dir, out, log_meta, task_id=task_id)
    logger.info(
        "[TextPrep] %d segments, %d changed, %.2fs",
        len(segments),
        changed_count,
        elapsed,
    )
    return out, log_meta


def _log_preparation(
    app_dir: Path,
    segments: list[str],
    meta: dict[str, Any],
    *,
    task_id: str = "",
) -> str:
    log_dir = app_dir / "output" / "dev"
    log_dir.mkdir(parents=True, exist_ok=True)
    jid = uuid.uuid4().hex[:10]
    path = log_dir / f"text_preparation_{jid}.log"
    lines = [
        f"=== TEXT PREPARATION task={task_id} ===",
        f"segments={meta.get('segments')} changed={meta.get('changed')} engine={meta.get('engine')}",
    ]
    for i, t in enumerate(segments[:200]):
        lines.append(f"{i}\t{(t or '')[:400]}")
    text = "\n".join(lines) + "\n"
    path.write_text(text, encoding="utf-8")
    (log_dir / "text_preparation_latest.log").write_text(text, encoding="utf-8")
    return str(path)
