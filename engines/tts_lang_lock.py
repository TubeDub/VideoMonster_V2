# -*- coding: utf-8 -*-
"""Stage 12/18 — TTS language lock for Simple (target=uk → only Ukrainian text/voice)."""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.tts_lang_lock")

_CYR = re.compile(r"[\u0400-\u04FF]")
_LAT = re.compile(r"[A-Za-z]")
# Distinctive Russian letters that never appear in Ukrainian orthography.
_RU_LETTERS = re.compile(r"[ыэёъЫЭЁЪ]")
_UK_LETTERS = re.compile(r"[іїєґІЇЄҐ]")
# Zip d3b6fe76: Marian en→uk leaked full Russian lines; these tokens are not UK.
_RU_ONLY_WORDS = re.compile(
    r"\b("
    r"хорошо|кажется|умн(?:ый|ым|ая|ое|ые)|мужик|отлично|справляешься|"
    r"честно|говоря|жаль|слышать|слышу|здесь|потому|даже|будто|"
    r"вырос|потеряла|потерял|молодой|молод|"
    r"что|этот|эта|это|они|его|её|чтобы|ещё|еще|"
    r"который|которая|которые|сейчас|очень|тоже|также|если|"
    r"когда|нет|он|она|"
    r"мой|быстро|ладно|накормить|себя|долго|уже|целый|делаешь|"
    r"получить|еду|смотри|верно|как|"
    r"мне|даже|этом|потому|можешь|тебя|есть|говорить|молод"
    r")\b",
    re.I,
)
_EN_SOURCE_KEYS = (
    "original",
    "original_text",
    "whisper_text",
    "source_text",
    "src_text",
    "english_text",
    "stt_text",
)

# Always-banned cross-locale voices when targeting uk (Stage 18: +ru-RU).
_FORBIDDEN_VOICE_PREFIXES_FOR_UK = (
    "cs-CZ",
    "pl-PL",
    "sk-SK",
    "hu-HU",
    "ro-RO",
    "bg-BG",
    "ru-RU",
    "en-US",
    "en-GB",
    "de-DE",
    "fr-FR",
)
# Legacy alias used by older imports/tests.
_FORBIDDEN_VOICE_PREFIXES = _FORBIDDEN_VOICE_PREFIXES_FOR_UK

# Stage 17/18: en→uk Edge TTS requires ≥55% cyrillic letters.
DEFAULT_UK_CYRILLIC_MIN = 0.55


def fold_uk_ru_marks(text: str) -> str:
    """Strip stress acutes so «потому́ что» matches lemmas; keep й/ё/ї."""
    nfd = unicodedata.normalize("NFD", str(text or ""))
    stripped = "".join(c for c in nfd if c not in ("\u0300", "\u0301"))
    return unicodedata.normalize("NFC", stripped)


def segment_en_source(seg: dict[str, Any] | None, fallback: str = "") -> str:
    """English source for remt. Zip 955dd5ec reissue dropped these keys."""
    if isinstance(seg, dict):
        for key in _EN_SOURCE_KEYS:
            val = str(seg.get(key) or "").strip()
            if val:
                return val
    return str(fallback or "").strip()


def cyrillic_letter_ratio(text: str) -> float:
    letters = [c for c in str(text or "") if c.isalpha()]
    if not letters:
        return 0.0
    cyr = sum(1 for c in letters if _CYR.match(c))
    return cyr / len(letters)


def latin_letter_ratio(text: str) -> float:
    """Share of Latin letters among alphabetic chars (Stage 24)."""
    letters = [c for c in str(text or "") if c.isalpha()]
    if not letters:
        return 0.0
    lat = sum(1 for c in letters if _LAT.match(c))
    return lat / len(letters)


def is_latin_heavy(text: str, *, threshold: float = 0.30) -> tuple[bool, float]:
    """True when Latin letters exceed threshold (default 30%)."""
    ratio = latin_letter_ratio(text)
    return ratio > float(threshold), ratio


_TTS_UK_AVAILABLE_CACHE: dict[str, bool] = {}


def _tts_uk_backend_available() -> bool:
    """Cheap cached probe: is the ``tts_uk`` package installed and importable?

    Stage 25 §1.2 — used to gate: when tts_uk is present we NEVER fall back to
    piper/edge as the default backend for target=uk.
    """
    if "ok" in _TTS_UK_AVAILABLE_CACHE:
        return _TTS_UK_AVAILABLE_CACHE["ok"]
    try:
        import importlib.util

        ok = importlib.util.find_spec("tts_uk") is not None
    except Exception:
        ok = False
    _TTS_UK_AVAILABLE_CACHE["ok"] = bool(ok)
    return _TTS_UK_AVAILABLE_CACHE["ok"]


def force_uk_tts_identity(
    *,
    target_lang: str | None,
    engine_id: str | None = None,
    voice: str | None = None,
) -> dict[str, Any]:
    """Stage 25 §1: for target=uk force language=uk and Ukrainian voice+backend.

    Contract for ``target_lang`` starting with ``uk``:

      - engine_id ∈ {``tts_uk``} → keep tts_uk, pick mykyta/tetiana/lada.
      - engine_id ∈ {``edge``, ``edge-offline``} → keep Edge; force
        ``uk-UA-OstapNeural`` (male) / ``uk-UA-PolinaNeural`` (female).
        Short tts_uk ids (mykyta/lada/tetiana) are NEVER sent to Edge.
      - engine_id ∈ {``piper``, unknown, empty} → prefer ``tts_uk`` when the
        ``tts_uk`` package is installed (§1.1 ЖЁСТКО, ignore UI/cache/fallback
        preference for piper); otherwise fall back to ``edge-offline``
        (uk-UA-* only, never cs/sk/pl/ru/en/de/fr).
      - Explicitly forbidden voices (cs/sk/pl/ru/de/fr/en) are rewritten to
        the safe UK default for the chosen backend.

    Non-uk targets are returned unchanged (only normalized).
    """
    from engines.tts_backends import (
        DEFAULT_EDGE_VOICE,
        DEFAULT_TTS_UK_VOICE,
        TTS_UK_VOICES,
        normalize_backend_name,
        resolve_voice_for_backend,
    )

    tgt = str(target_lang or "").split("-")[0].strip().lower()
    eid_raw = str(engine_id or "").strip().lower()
    eid = normalize_backend_name(eid_raw or "tts_uk")
    v = str(voice or "").strip()
    if tgt != "uk":
        return {
            "language": tgt or "uk",
            "engine_id": eid,
            "voice": v,
            "forced": False,
        }

    vl = v.lower()

    def _pick_tts_uk_voice() -> str:
        if vl in TTS_UK_VOICES:
            return vl
        if "tetiana" in vl or "polina" in vl:
            return "tetiana"
        if "lada" in vl:
            return "lada"
        return DEFAULT_TTS_UK_VOICE

    def _pick_edge_voice() -> str:
        # tts_uk short ids → Edge Neural (Ostap for male, Polina for female).
        if vl in TTS_UK_VOICES:
            return (
                "uk-UA-PolinaNeural"
                if TTS_UK_VOICES[vl] == "female"
                else DEFAULT_EDGE_VOICE
            )
        if "polina" in vl or "tetiana" in vl or "lada" in vl:
            return "uk-UA-PolinaNeural"
        if v.startswith("uk-UA-"):
            res = resolve_voice_for_backend(v, "edge-offline")
            ok2, _ = assert_voice_matches_target(res, "uk", raise_error=False)
            if ok2 and res.startswith("uk-UA-"):
                return res
        return DEFAULT_EDGE_VOICE

    # Case A: caller explicitly asks for tts_uk → keep tts_uk + Mykyta family.
    if eid == "tts_uk":
        new_v = _pick_tts_uk_voice()
        return {
            "language": "uk",
            "engine_id": "tts_uk",
            "voice": new_v,
            "forced": True,
            "tts_uk_available": _tts_uk_backend_available(),
        }

    # Case B: caller explicitly asks for Edge → keep Edge with valid uk-UA voice.
    if eid in ("edge-offline", "edge"):
        edge_v = _pick_edge_voice()
        ok_final, _ = assert_voice_matches_target(edge_v, "uk", raise_error=False)
        if not ok_final or not str(edge_v).startswith("uk-UA-"):
            edge_v = DEFAULT_EDGE_VOICE
        return {
            "language": "uk",
            "engine_id": "edge-offline",
            "voice": edge_v,
            "forced": True,
            "tts_uk_available": _tts_uk_backend_available(),
        }

    # Case C: piper / unknown / anything else → §1.1 override to tts_uk when
    # available; otherwise degrade to Edge (never piper stays default for UK).
    if _tts_uk_backend_available():
        logger.warning(
            "[TTS] uk_backend_override %s→tts_uk (mykyta family) — piper/other "
            "must never be default for target=uk when tts_uk is installed",
            eid_raw or eid,
        )
        new_v = _pick_tts_uk_voice()
        return {
            "language": "uk",
            "engine_id": "tts_uk",
            "voice": new_v,
            "forced": True,
            "tts_uk_available": True,
            "override_from": eid_raw or eid,
        }

    edge_v = _pick_edge_voice()
    if not str(edge_v).startswith("uk-UA-"):
        edge_v = DEFAULT_EDGE_VOICE
    logger.warning(
        "[TTS] uk_backend_fallback %s→edge-offline (%s) — tts_uk not installed",
        eid_raw or eid,
        edge_v,
    )
    return {
        "language": "uk",
        "engine_id": "edge-offline",
        "voice": edge_v,
        "forced": True,
        "tts_uk_available": False,
        "override_from": eid_raw or eid,
    }


def resolve_uk_tts(
    target_lang: str | None,
    requested_backend: str | None = None,
    requested_voice: str | None = None,
) -> tuple[str, str]:
    """Stage 25 §1.1 — single entry point resolver: (backend, voice) for UK.

    Wraps :func:`force_uk_tts_identity` so callers can consume a plain tuple.
    For non-UK targets returns the requested backend/voice normalized as-is.
    """
    ident = force_uk_tts_identity(
        target_lang=target_lang,
        engine_id=requested_backend,
        voice=requested_voice,
    )
    return str(ident.get("engine_id") or ""), str(ident.get("voice") or "")


_RU_STRONG = re.compile(
    r"\b("
    r"хорошо|кажется|умн(?:ый|ым|ая|ое)|мужик|честно\s+говоря|"
    r"слышать|слышу|отлично|справляешься"
    r")\b",
    re.I,
)


def uk_text_has_russian_leak(text: str) -> bool:
    """True when target=uk text is Russian (or mixed RU) rather than Ukrainian.

    Stage 33 / diag d3b6fe76: Cyrillic ratio was 1.0 on
    «Да. Джонатан, ты кажется умным.» so Stage 29's cyrillic gate voiced it
    with uk-UA-OstapNeural; STUDIO then hard-failed language_mismatch.

    Stage 34 / diag 955dd5ec: combining accents («потому́») and leftover
    lemmas (мой/быстро/накормить) must still count as leak.
    """
    clean = " ".join(fold_uk_ru_marks(text).split()).strip()
    if not clean:
        return False
    if _RU_LETTERS.search(clean):
        return True
    if _RU_STRONG.search(clean):
        return True
    ru_hits = _RU_ONLY_WORDS.findall(clean)
    if not ru_hits:
        try:
            from engines.language_intelligence.rules import detect_russian_words

            ru_hits = list(detect_russian_words(clean, "uk") or [])
        except Exception:
            ru_hits = []
    if not ru_hits:
        return False
    if not _UK_LETTERS.search(clean):
        return True
    return len(ru_hits) >= 2


def rewrite_russian_leak_for_uk(text: str) -> str:
    """Best-effort RU→UK lexical rewrite when Marian remt is unavailable."""
    out = " ".join(fold_uk_ru_marks(text).split()).strip()
    if not out:
        return out
    try:
        from engines.language_intelligence.rules import UK_RUISM_RULES

        for pat, repl, _cat in UK_RUISM_RULES:
            out = re.sub(pat, repl, out)
    except Exception:
        pass
    extras = (
        (r"\bХорошо\b", "Добре"),
        (r"\bхорошо\b", "добре"),
        (r"\bЭй\b", "Гей"),
        (r"\bэй\b", "гей"),
        (r"\bты\b", "ти"),
        (r"\bТы\b", "Ти"),
        (r"\bмой\b", "мій"),
        (r"\bМой\b", "Мій"),
        (r"\bбыстро\b", "швидко"),
        (r"\bБыстро\b", "Швидко"),
        (r"\bкажется\b", "здається"),
        (r"\bумным\b", "розумним"),
        (r"\bумный\b", "розумний"),
        (r"\bЧестно говоря\b", "Чесно кажучи"),
        (r"\bчестно говоря\b", "чесно кажучи"),
        (r"\bмного\b", "багато"),
        (r"\bжаль\b", "шкода"),
        (r"\bслышать\b", "чути"),
        (r"\bотлично\b", "чудово"),
        (r"\bсправляешься\b", "справляєшся"),
        (r"\bмужик\b", "чувак"),
        (r"\bДа ладно\b", "Та ну"),
        (r"\bда ладно\b", "та ну"),
        (r"\bнакормить\b", "нагодувати"),
        (r"\bсебя\b", "себе"),
        (r"\bкак долго\b", "як довго"),
        (r"\bКак долго\b", "Як довго"),
        (r"\bуже\b", "вже"),
        (r"\bцелый\b", "цілий"),
        (r"\bделаешь\b", "робиш"),
        (r"\bполучить\b", "отримати"),
        (r"\bеду\b", "їжу"),
        (r"\bсмотри\b", "дивись"),
        (r"\bверно\b", "правда"),
        (r"\bДа\.(?=\s|$)", "Так."),
        (r"\bИ мне\b", "І мені"),
        (r"\bМне\b", "Мені"),
        (r"\bпотому что\b", "тому що"),
        (r"\bоб этом\b", "про це"),
        (r"\bчто у тебя есть\b", "що в тебе є"),
        (r"\bТак как ты\b", "То як ти"),
        (r"\bТак как ти\b", "То як ти"),
        (r"\bТак как\b", "То як"),
        (r"\bкак ты\b", "як ти"),
        (r"\bдаже\b", "навіть"),
        (r"\bкак будто\b", "ніби"),
        (r"\bвырос\b", "виріс"),
        (r"\bпотеряла\b", "втратила"),
        (r"\bздесь\b", "тут"),
        (r"\bговорить\b", "говорити"),
        (r"\bГоворить\b", "Говорити"),
        (r"\bгод\b", "рік"),
        (r"\bГод\b", "Рік"),
        (r"\bчто\b", "що"),
        (r"\bЧто\b", "Що"),
        (r"\bтебя\b", "тебе"),
        (r"\bесть\b", "є"),
        (r"\bМне\b", "Мені"),
        (r"\bмне\b", "мені"),
        (r"\bДа,\b", "Так,"),
        (r"\b и \b", " і "),
        (r"\bможешь\b", "можеш"),
        (r"\bМожешь\b", "Можеш"),
        (r"\bэтом\b", "цьому"),
        (r"\bлюблю\b", "люблю"),
        (r"\bмолодой\b", "молодий"),
        (r"\bмолод\b", "молодий"),
    )
    for pat, repl in extras:
        out = re.sub(pat, repl, out)
    return out.strip()


def is_uk_tts_text_ok(
    text: str, *, min_ratio: float = DEFAULT_UK_CYRILLIC_MIN
) -> bool:
    clean = " ".join(str(text or "").split()).strip()
    if not clean:
        return False
    if uk_text_has_russian_leak(clean):
        return False
    return cyrillic_letter_ratio(clean) >= float(min_ratio)


def voice_locale_prefix(voice: str) -> str:
    v = str(voice or "").strip()
    parts = v.split("-")
    if len(parts) >= 2:
        return f"{parts[0]}-{parts[1]}"
    return v[:5]


def assert_voice_matches_target(
    voice: str,
    target_lang: str,
    *,
    raise_error: bool = True,
) -> tuple[bool, str]:
    """Return (ok, reason). For uk: Edge uk-UA-*, tts_uk, or Piper uk_UA-*."""
    tgt = str(target_lang or "").split("-")[0].lower()
    v = str(voice or "").strip()
    if not v:
        msg = "empty_voice"
        if raise_error:
            raise RuntimeError(f"PIPELINE_VOICE_LOCALE: {msg}")
        return False, msg
    if tgt == "uk":
        for bad in _FORBIDDEN_VOICE_PREFIXES_FOR_UK:
            if v.startswith(bad):
                msg = f"forbidden_voice={v} for target={tgt}"
                if raise_error:
                    raise RuntimeError(f"PIPELINE_VOICE_LOCALE: {msg}")
                return False, msg
        try:
            from engines.tts_backends import is_uk_tts_voice

            if is_uk_tts_voice(v):
                return True, "ok"
        except Exception:
            pass
        if not v.startswith("uk-UA-"):
            msg = f"voice={v} locale!={tgt} (need uk-UA-* / tts_uk / piper uk_UA-*)"
            if raise_error:
                raise RuntimeError(f"PIPELINE_VOICE_LOCALE: {msg}")
            return False, msg
    return True, "ok"


def force_remt_segment_no_cache(
    source_text: str,
    *,
    src_lang: str,
    tgt_lang: str,
    app_dir: Path | None = None,
) -> str:
    """One Marian pass without reading/writing MT cache."""
    from engines.mt.glossary_en_uk import finalize_mt_text
    from engines.mt.stable_translate import translate_direct_marian

    base = Path(app_dir) if app_dir else Path(__file__).resolve().parents[1]
    src = " ".join(str(source_text or "").split()).strip()
    if not src:
        return ""
    out, _meta = translate_direct_marian(
        src, src_lang, tgt_lang, app_dir=base, segment_index=-1
    )
    return finalize_mt_text(src_lang, tgt_lang, str(out or "").strip())


def guard_uk_tts_text(
    text: str,
    *,
    source_text: str = "",
    src_lang: str = "en",
    tgt_lang: str = "uk",
    app_dir: Path | None = None,
    segment_index: int = -1,
    allow_remt: bool = True,
    fail_loud: bool = False,
) -> tuple[str, dict[str, Any]]:
    """Ensure TTS text is Ukrainian. Returns (text, meta).

    If cyrillic ratio < 0.55 → remt once.
    Stage 18 Simple/Happy Path (fail_loud): Latin/empty remt fail → raise
    PIPELINE_LANG_MIX. Stage 34 leftover Russian (cyrillic but RU leak) after
    remt/rewrite → skip_tts + pad, never brick the job.
    """
    meta: dict[str, Any] = {
        "tts_lang_ok": True,
        "cyrillic_ratio": 0.0,
        "rejected_non_target": False,
        "remt_attempted": False,
        "skipped": False,
        "fail_loud": bool(fail_loud),
    }
    tgt = str(tgt_lang or "").split("-")[0].lower()
    clean = " ".join(str(text or "").split()).strip()
    if tgt == "uk" and clean:
        try:
            from engines.text_slot_fit import prepare_uk_spoken_text

            clean = prepare_uk_spoken_text(clean)
        except Exception:
            pass
    if tgt != "uk":
        meta["tts_lang_ok"] = True
        return clean, meta

    if not clean:
        meta["tts_lang_ok"] = False
        meta["rejected_non_target"] = True
        if fail_loud:
            raise RuntimeError(
                f"PIPELINE_LANG_MIX: empty TTS text seg#{segment_index if segment_index >= 0 else '?'}"
            )
        meta["skipped"] = True
        return "", meta

    ratio = cyrillic_letter_ratio(clean)
    meta["cyrillic_ratio"] = round(ratio, 3)
    if is_uk_tts_text_ok(clean, min_ratio=DEFAULT_UK_CYRILLIC_MIN):
        return clean, meta

    logger.warning(
        "[TTS] reject_non_target lang_mix seg#%s ratio=%.2f text=%.80s",
        segment_index if segment_index >= 0 else "?",
        ratio,
        clean,
    )
    meta["rejected_non_target"] = True
    meta["tts_lang_ok"] = False

    if allow_remt and source_text.strip():
        meta["remt_attempted"] = True
        try:
            remt = force_remt_segment_no_cache(
                source_text,
                src_lang=src_lang,
                tgt_lang=tgt_lang,
                app_dir=app_dir,
            )
            remt_ratio = cyrillic_letter_ratio(remt)
            meta["remt_cyrillic_ratio"] = round(remt_ratio, 3)
            if is_uk_tts_text_ok(remt, min_ratio=DEFAULT_UK_CYRILLIC_MIN):
                logger.info(
                    "[TTS] remt_ok seg#%s ratio=%.2f",
                    segment_index if segment_index >= 0 else "?",
                    remt_ratio,
                )
                meta["tts_lang_ok"] = True
                meta["rejected_non_target"] = False
                meta["remt_ok"] = True
                meta["engine"] = "marian_remt"
                return remt, meta
            meta["remt_ok"] = False
            meta["fail_reason"] = f"remt_still_bad ratio={remt_ratio:.2f}"
            logger.warning(
                "[TTS] remt_still_bad seg#%s ratio=%.2f",
                segment_index if segment_index >= 0 else "?",
                remt_ratio,
            )
        except Exception as exc:
            logger.warning("[TTS] remt_failed seg#%s: %s", segment_index, exc)
            meta["remt_error"] = str(exc)
            meta["remt_ok"] = False
            meta["fail_reason"] = f"remt_failed:{exc}"
    else:
        meta["fail_reason"] = "no_remt_or_empty_source"

    # Stage 33: lexical RU→UK rewrite when remt is empty / still Russian.
    rewritten = rewrite_russian_leak_for_uk(clean)
    if (
        rewritten
        and rewritten != clean
        and is_uk_tts_text_ok(rewritten, min_ratio=DEFAULT_UK_CYRILLIC_MIN)
    ):
        logger.info(
            "[TTS] ruism_rewrite_ok seg#%s",
            segment_index if segment_index >= 0 else "?",
        )
        meta["tts_lang_ok"] = True
        meta["rejected_non_target"] = False
        meta["ruism_rewrite"] = True
        meta["fail_reason"] = ""
        return rewritten, meta

    ru_leftover = uk_text_has_russian_leak(clean)
    if ru_leftover:
        # Zip 955dd5ec: fail_loud raised PIPELINE_LANG_MIX on mixed RU with
        # empty English source. Pad the hole instead of killing TTS_PREP.
        meta["fail_reason"] = "russian_in_uk"
        meta["skipped"] = True
        meta["tts_lang_ok"] = False
        logger.warning(
            "[TTS] leftover_russian_skip_tts seg#%s ratio=%s",
            segment_index if segment_index >= 0 else "?",
            meta.get("cyrillic_ratio"),
        )
        return "", meta

    if fail_loud:
        reason = meta.get("fail_reason") or "cyrillic_ratio_low"
        raise RuntimeError(
            f"PIPELINE_LANG_MIX: seg#{segment_index if segment_index >= 0 else '?'} "
            f"{reason} ratio={meta.get('cyrillic_ratio')} — refuse skip→silence"
        )

    meta["skipped"] = True
    return "", meta


def enforce_segments_lang_lock(
    segments_data: list,
    *,
    target_lang: str,
    source_lang: str = "en",
    app_dir: Path | None = None,
    fail_if_reject_ratio: float = 0.20,
    simple_mode: bool = False,
    fail_loud: bool | None = None,
) -> dict[str, Any]:
    """In-place guard on segments_data TTS texts. Returns stats; may raise.

    Stage 18 Simple: fail_loud — never skip_tts / empty text (raise instead).
    """
    tgt = str(target_lang or "").split("-")[0].lower()
    # Stage 18b: simple_mode OR fail_loud → never skip→silence.
    loud = bool(simple_mode) or bool(fail_loud)
    stats: dict[str, Any] = {
        "checked": 0,
        "rejected_non_target": 0,
        "remt_ok": 0,
        "skipped": 0,
        "ok": 0,
        "fail_loud": loud,
    }
    if tgt != "uk":
        return stats

    active = 0
    for i, seg in enumerate(segments_data or []):
        if not isinstance(seg, dict):
            continue
        if seg.get("merged_into") is not None or seg.get("archived"):
            continue
        # Stage 18b: Simple/fail_loud — never skip→silence for Latin/empty.
        # Stage 34: keep russian_in_uk skip so PRE_TTS pad path survives lock.
        if loud and (seg.get("tts_blocked") or seg.get("skip_tts")):
            if str(seg.get("tts_skip_reason") or "") == "russian_in_uk":
                stats["skipped"] += 1
                continue
            seg.pop("skip_tts", None)
            seg.pop("tts_blocked", None)
            seg.pop("tts_skip_reason", None)
        elif seg.get("tts_blocked") or seg.get("skip_tts"):
            continue
        text = str(
            seg.get("final_tts_text")
            or seg.get("tts_text")
            or seg.get("plain_text")
            or seg.get("translated_text")
            or seg.get("text")
            or ""
        ).strip()
        if not text:
            if loud:
                raise RuntimeError(
                    f"PIPELINE_LANG_MIX: empty TTS text seg#{i} — refuse skip→silence"
                )
            continue
        active += 1
        stats["checked"] += 1
        src = segment_en_source(seg)
        new_text, meta = guard_uk_tts_text(
            text,
            source_text=src,
            src_lang=source_lang,
            tgt_lang=target_lang,
            app_dir=app_dir,
            segment_index=i,
            allow_remt=True,
            fail_loud=loud,
        )
        seg["tts_lang_hint"] = "uk" if meta.get("tts_lang_ok") else "reject"
        seg["tts_cyrillic_ratio"] = meta.get("cyrillic_ratio")
        if meta.get("skipped"):
            ru_skip = str(meta.get("fail_reason") or "") == "russian_in_uk"
            if loud and not ru_skip:
                raise RuntimeError(
                    f"PIPELINE_LANG_MIX: seg#{i} rejected_non_target — refuse skip→silence"
                )
            stats["skipped"] += 1
            stats["rejected_non_target"] += 1
            seg["skip_tts"] = True
            seg["tts_blocked"] = True
            seg["tts_skip_reason"] = (
                "russian_in_uk" if ru_skip else "reject_non_target_lang_mix"
            )
            for k in (
                "final_tts_text",
                "tts_text",
                "plain_text",
                "text",
                "translated_text",
            ):
                if k in seg:
                    seg[k] = ""
            continue
        if meta.get("tts_lang_ok") and new_text:
            if meta.get("remt_attempted"):
                stats["remt_ok"] += 1
            if (
                meta.get("remt_ok")
                or meta.get("ruism_rewrite")
                or new_text != text
            ):
                for k in (
                    "final_tts_text",
                    "tts_text",
                    "plain_text",
                    "text",
                    "translated_text",
                ):
                    if k in seg or k in ("final_tts_text", "tts_text", "plain_text"):
                        seg[k] = new_text
                if meta.get("engine"):
                    seg["mt_engine"] = meta["engine"]
            stats["ok"] += 1
            continue
        if meta.get("rejected_non_target"):
            stats["rejected_non_target"] += 1
        else:
            stats["ok"] += 1

    if not loud and active > 0 and stats["rejected_non_target"] / active > fail_if_reject_ratio:
        raise RuntimeError(
            f"PIPELINE_LANG_MIX: {stats['rejected_non_target']}/{active} segments "
            f"rejected_non_target (>{fail_if_reject_ratio:.0%}) — abort before mux"
        )
    return stats


def pre_mux_tts_integrity(
    segments_data: list,
    *,
    target_lang: str,
    timeline_ms: float | None = None,
    simple_mode: bool = False,
) -> dict[str, Any]:
    """Log per-segment voice/text; check duration sum vs timeline loosely."""
    rows: list[dict[str, Any]] = []
    dur_sum = 0.0
    rejected = 0
    voiced = 0
    rerouted_uk = False
    for i, seg in enumerate(segments_data or []):
        if not isinstance(seg, dict) or seg.get("merged_into") is not None:
            continue
        text = str(
            seg.get("final_tts_text")
            or seg.get("tts_text")
            or seg.get("plain_text")
            or ""
        ).strip()
        voice = str(
            seg.get("voice_id")
            or seg.get("assigned_voice")
            or seg.get("voice")
            or ""
        )
        hint = str(seg.get("tts_lang_hint") or "")
        if seg.get("skip_tts") or seg.get("tts_blocked"):
            reason = str(seg.get("tts_skip_reason") or "")
            padded = bool(seg.get("audio_padded") or seg.get("soft_padded"))
            if reason == "russian_in_uk" or padded:
                # Stage 34 — pad fills leftover RU; do not count as lang-mix reject.
                pass
            else:
                rejected += 1
                if (
                    simple_mode
                    and str(target_lang or "").split("-")[0].lower() == "uk"
                ):
                    # Stage 40: do not abort Simple — pin Ostap + mark pad.
                    try:
                        from engines.simple_voice_lock import DEFAULT_UK_VOICE

                        seg["voice"] = DEFAULT_UK_VOICE
                        seg["assigned_voice"] = DEFAULT_UK_VOICE
                        seg["tts_voice"] = DEFAULT_UK_VOICE
                    except Exception:
                        pass
                    seg["needs_re_tts"] = False
                    logger.warning(
                        "[TTS integrity] Simple uk skip_tts seg#%d — reroute default UK + pad",
                        i + 1,
                    )
        if voice and str(target_lang or "").split("-")[0].lower() == "uk":
            ok_v, why_v = assert_voice_matches_target(
                voice, target_lang, raise_error=False
            )
            if not ok_v:
                if simple_mode:
                    try:
                        from engines.simple_voice_lock import DEFAULT_UK_VOICE

                        seg["voice"] = DEFAULT_UK_VOICE
                        seg["assigned_voice"] = DEFAULT_UK_VOICE
                        seg["tts_voice"] = DEFAULT_UK_VOICE
                        voice = DEFAULT_UK_VOICE
                        rerouted_uk = True
                    except Exception:
                        pass
                    logger.warning(
                        "[TTS integrity] Simple forbidden voice %s (%s) — Ostap + pad",
                        voice,
                        why_v,
                    )
                else:
                    assert_voice_matches_target(voice, target_lang, raise_error=True)
        dur = float(seg.get("playback_duration") or seg.get("tts_duration") or 0)
        if dur <= 0:
            try:
                start = float(seg.get("start") or 0)
                end = float(seg.get("end") or 0)
                dur = max(0.0, end - start)
            except Exception:
                dur = 0.0
        if text and not seg.get("skip_tts"):
            voiced += 1
            dur_sum += dur
        row = {
            "index": i,
            "voice_id": voice,
            "tts_lang_hint": hint
            or (
                "uk"
                if cyrillic_letter_ratio(text) >= DEFAULT_UK_CYRILLIC_MIN
                else "?"
            ),
            "text": text[:80],
            "tts_text_hash": str(seg.get("tts_text_hash") or ""),
            "duration_s": round(dur, 3),
        }
        rows.append(row)
        logger.info(
            "[TTS integrity] seg#%d voice=%s lang=%s hash=%s dur=%.2f text=%.80s",
            i + 1,
            voice,
            row["tts_lang_hint"],
            row["tts_text_hash"] or "-",
            dur,
            text,
        )

    report = {
        "segments_logged": len(rows),
        "voiced": voiced,
        "rejected_or_skipped": rejected,
        "tts_duration_sum_s": round(dur_sum, 3),
        "timeline_ms": timeline_ms,
        "rows": rows,
        "rerouted_default_uk": bool(rerouted_uk),
    }
    if timeline_ms and timeline_ms > 0 and dur_sum > 0:
        ratio = (dur_sum * 1000.0) / float(timeline_ms)
        report["duration_vs_timeline"] = round(ratio, 3)
        if ratio < 0.35 or ratio > 2.5:
            logger.warning(
                "[TTS integrity] duration_sum/timeline odd ratio=%.2f sum=%.1fs timeline_ms=%.0f",
                ratio,
                dur_sum,
                timeline_ms,
            )
    if voiced > 0 and rejected / max(voiced + rejected, 1) > 0.20:
        if simple_mode and str(target_lang or "").split("-")[0].lower() == "uk":
            report["rerouted_default_uk"] = True
            logger.error(
                "[TTS integrity] Simple >20%% rejected_non_target (%s/%s) — "
                "reroute DEFAULT_UK_VOICE + pad, mux continues",
                rejected,
                voiced,
            )
            try:
                from engines.simple_voice_lock import (
                    DEFAULT_UK_VOICE,
                    lock_simple_pipeline_voice,
                )

                lock_simple_pipeline_voice(
                    list(segments_data or []),
                    pipeline_voice=DEFAULT_UK_VOICE,
                    task_info={"target_lang": target_lang, "simple_pipeline": True},
                )
            except Exception as _rr_exc:
                logger.warning("Simple UK reroute skipped: %s", _rr_exc)
        else:
            raise RuntimeError(
                f"PIPELINE_LANG_MIX: {rejected} skipped / {voiced} voiced "
                "(>20% rejected_non_target) — refuse mux"
            )
    return report
