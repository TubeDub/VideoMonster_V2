"""Proper nouns — Latin brands, preferred UA titles, name transliteration."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

_WORD_BOUNDARY = r"(?<!\w)"


def _app_dir(app_dir: Path | None = None) -> Path:
    return app_dir or Path(__file__).resolve().parent.parent


@lru_cache(maxsize=4)
def _load_catalog(app_dir_str: str) -> dict:
    path = Path(app_dir_str) / "data" / "proper_nouns_never_translate.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _token_list(cat: dict, key: str, fallback: str = "") -> list[str]:
    items = cat.get(key) or cat.get(fallback) or []
    out: list[str] = []
    seen: set[str] = set()
    for tok in items:
        s = str(tok or "").strip()
        if not s:
            continue
        k = s.lower()
        if k not in seen:
            seen.add(k)
            out.append(s)
    return sorted(out, key=len, reverse=True)


def keep_latin_tokens(app_dir: Path | None = None) -> list[str]:
    cat = _load_catalog(str(_app_dir(app_dir)))
    return _token_list(cat, "keep_latin", "never_translate")


def preferred_translations(app_dir: Path | None = None) -> dict[str, str]:
    cat = _load_catalog(str(_app_dir(app_dir)))
    raw = cat.get("preferred_translations") or {}
    return {str(k): str(v) for k, v in raw.items() if k and v}


def transliterate_names(app_dir: Path | None = None) -> dict[str, str]:
    cat = _load_catalog(str(_app_dir(app_dir)))
    raw = cat.get("transliterate_names") or {}
    return {str(k): str(v) for k, v in raw.items() if k and v}


def mistranslation_map(app_dir: Path | None = None) -> dict[str, list[str]]:
    cat = _load_catalog(str(_app_dir(app_dir)))
    raw = cat.get("cyrillic_mistranslations") or {}
    out: dict[str, list[str]] = {}
    if not isinstance(raw, dict):
        return out
    for canonical, variants in raw.items():
        canon = str(canonical or "").strip()
        if not canon:
            continue
        vals = [str(v).strip() for v in (variants or []) if str(v).strip()]
        out[canon] = vals
    return out


def wrong_title_map(app_dir: Path | None = None) -> dict[str, list[str]]:
    cat = _load_catalog(str(_app_dir(app_dir)))
    raw = cat.get("wrong_title_translations") or {}
    out: dict[str, list[str]] = {}
    if not isinstance(raw, dict):
        return out
    for canonical, variants in raw.items():
        canon = str(canonical or "").strip()
        if not canon:
            continue
        out[canon] = [str(v).strip() for v in (variants or []) if str(v).strip()]
    return out


def _find_in_source(source: str, tokens: list[str]) -> list[str]:
    src = str(source or "")
    found: list[str] = []
    seen: set[str] = set()
    for tok in tokens:
        pat = re.compile(_WORD_BOUNDARY + re.escape(tok) + r"(?!\w)", re.IGNORECASE)
        if pat.search(src):
            key = tok.lower()
            if key not in seen:
                seen.add(key)
                found.append(tok)
    return found


def find_latin_tokens_in_source(source: str, app_dir: Path | None = None) -> list[str]:
    return _find_in_source(source, keep_latin_tokens(app_dir))


def find_preferred_keys_in_source(source: str, app_dir: Path | None = None) -> list[str]:
    return _find_in_source(source, list(preferred_translations(app_dir).keys()))


def find_name_keys_in_source(source: str, app_dir: Path | None = None) -> list[str]:
    return _find_in_source(source, list(transliterate_names(app_dir).keys()))


def never_translate_tokens(app_dir: Path | None = None) -> list[str]:
    """Backward-compatible alias — Latin-only tokens."""
    return keep_latin_tokens(app_dir)


def find_tokens_in_source(source: str, app_dir: Path | None = None) -> list[str]:
    return find_latin_tokens_in_source(source, app_dir)


def extra_preserved_tokens(source: str, app_dir: Path | None = None) -> list[str]:
    """Tokens to preserve in output (Latin brands + preferred UA forms + names)."""
    out: list[str] = []
    seen: set[str] = set()
    for tok in (
        find_latin_tokens_in_source(source, app_dir)
        + find_preferred_keys_in_source(source, app_dir)
        + find_name_keys_in_source(source, app_dir)
    ):
        key = tok.lower()
        if key not in seen:
            seen.add(key)
            out.append(tok)
    prefs = preferred_translations(app_dir)
    for key in find_preferred_keys_in_source(source, app_dir):
        ua = prefs.get(key)
        if ua and ua.lower() not in seen:
            seen.add(ua.lower())
            out.append(ua)
    names = transliterate_names(app_dir)
    for key in find_name_keys_in_source(source, app_dir):
        tr = names.get(key)
        if tr and tr.lower() not in seen:
            seen.add(tr.lower())
            out.append(tr)
    return out


def restore_never_translate_tokens(
    source: str,
    text: str,
    *,
    app_dir: Path | None = None,
) -> str:
    """Restore Latin spelling for keep_latin brands."""
    out = str(text or "")
    src = str(source or "")
    if not out.strip() or not src.strip():
        return out.strip()

    mistrans = mistranslation_map(app_dir)
    for canonical in find_latin_tokens_in_source(src, app_dir):
        if re.search(
            _WORD_BOUNDARY + re.escape(canonical) + r"(?!\w)",
            out,
            flags=re.IGNORECASE,
        ):
            continue
        for bad in mistrans.get(canonical, []):
            if bad and bad.lower() in out.lower():
                pat = re.compile(re.escape(bad), re.IGNORECASE)
                out = pat.sub(canonical, out, count=1)
                break
    return out.strip()


def apply_preferred_translations(
    source: str,
    text: str,
    *,
    app_dir: Path | None = None,
) -> str:
    """Replace phonetic/wrong title forms with preferred Ukrainian (e.g. Star Wars)."""
    out = str(text or "")
    src = str(source or "")
    prefs = preferred_translations(app_dir)
    wrong = wrong_title_map(app_dir)

    for key in find_preferred_keys_in_source(src, app_dir):
        preferred = prefs.get(key, "")
        if not preferred:
            continue
        if preferred.lower() in out.lower():
            continue
        for bad in wrong.get(key, []) + mistranslation_map(app_dir).get(key, []):
            if bad and bad.lower() in out.lower():
                pat = re.compile(re.escape(bad), re.IGNORECASE)
                out = pat.sub(preferred, out, count=1)
                break
        if re.search(_WORD_BOUNDARY + re.escape(key) + r"(?!\w)", src, re.I):
            if re.search(_WORD_BOUNDARY + re.escape(key) + r"(?!\w)", out, re.I):
                pat = re.compile(_WORD_BOUNDARY + re.escape(key) + r"(?!\w)", re.I)
                out = pat.sub(preferred, out, count=1)
    return out.strip()


def _jr_name_form(*, tgt_lang: str = "") -> str:
    base = (tgt_lang or "").split("-")[0].lower()
    if base == "ru":
        return "Джордж-младший"
    return "Джордж-молодший"


def apply_name_transliterations(
    source: str,
    text: str,
    *,
    app_dir: Path | None = None,
    tgt_lang: str = "",
) -> str:
    """Fix known personal names (George Lucas → Джордж Лукас)."""
    out = str(text or "")
    src = str(source or "")
    names = transliterate_names(app_dir)
    mistrans = mistranslation_map(app_dir)

    has_jr = bool(
        re.search(r"(?<!\w)George\s+Jr\.?(?!\w)", src, re.I)
    )
    has_lucas = bool(
        re.search(r"(?<!\w)George\s+Lucas(?!\w)", src, re.I)
    )
    if has_jr and not has_lucas:
        repl = names.get("George Jr.", "") or _jr_name_form(tgt_lang=tgt_lang)
        if (tgt_lang or "").split("-")[0].lower() == "ru":
            repl = "Джордж-младший"
        out = re.sub(r"\bGeorge\s+Lucas\b", repl, out, flags=re.I)
        out = re.sub(r"\bДжордж\s+Лукас\b", repl, out, flags=re.I)
        out = re.sub(r"\bДжордж-молодший\b", repl, out, flags=re.I)
        out = re.sub(r"\bДжордж-младший\b", repl, out, flags=re.I)

    for key in find_name_keys_in_source(src, app_dir):
        target = names.get(key, "")
        if key in ("George Jr.", "George Jr") and (tgt_lang or "").split("-")[0].lower() == "ru":
            target = _jr_name_form(tgt_lang=tgt_lang)
        if not target:
            continue
        if target.lower() in out.lower():
            continue
        for bad in mistrans.get(key, []):
            if bad and bad.lower() in out.lower():
                pat = re.compile(re.escape(bad), re.IGNORECASE)
                out = pat.sub(target, out, count=1)
                break
        if re.search(_WORD_BOUNDARY + re.escape(key) + r"(?!\w)", out, re.I):
            pat = re.compile(_WORD_BOUNDARY + re.escape(key) + r"(?!\w)", re.I)
            out = pat.sub(target, out, count=1)
    return out.strip()


def apply_proper_noun_polish(
    source: str,
    text: str,
    *,
    app_dir: Path | None = None,
    tgt_lang: str = "",
) -> str:
    out = restore_never_translate_tokens(source, text, app_dir=app_dir)
    out = apply_preferred_translations(source, out, app_dir=app_dir)
    out = apply_name_transliterations(source, out, app_dir=app_dir, tgt_lang=tgt_lang)
    return out.strip()


def wrong_phonetic_brand_hits(
    source: str,
    translated: str,
    *,
    app_dir: Path | None = None,
) -> list[str]:
    hits: list[str] = []
    tr = str(translated or "")
    for canonical in find_latin_tokens_in_source(source, app_dir):
        if re.search(
            _WORD_BOUNDARY + re.escape(canonical) + r"(?!\w)",
            tr,
            flags=re.IGNORECASE,
        ):
            continue
        for bad in mistranslation_map(app_dir).get(canonical, []):
            if bad and bad.lower() in tr.lower():
                hits.append(canonical)
                break
    return hits


def wrong_title_hits(
    source: str,
    translated: str,
    *,
    app_dir: Path | None = None,
) -> list[str]:
    hits: list[str] = []
    tr = str(translated or "")
    prefs = preferred_translations(app_dir)
    for key in find_preferred_keys_in_source(source, app_dir):
        preferred = prefs.get(key, "")
        if preferred and preferred.lower() in tr.lower():
            continue
        for bad in wrong_title_map(app_dir).get(key, []):
            if bad and bad.lower() in tr.lower():
                hits.append(key)
                break
        if re.search(_WORD_BOUNDARY + re.escape(key) + r"(?!\w)", tr, re.I):
            hits.append(key)
    return hits
