"""Translation Memory — proper-name consistency across segments (language-agnostic)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from engines.translation_quality import extract_preserved_tokens

MEMORY_DIR = "translation_memory"


def _memory_path(app_dir: Path, src: str, tgt: str) -> Path:
    s = (src or "en").split("-")[0].lower()
    t = (tgt or "ru").split("-")[0].lower()
    d = app_dir / "data" / MEMORY_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{s}_{t}.json"


def load_memory(app_dir: Path, src: str, tgt: str) -> dict[str, str]:
    path = _memory_path(app_dir, src, tgt)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = data.get("names") if isinstance(data, dict) else {}
        return {str(k).lower(): str(v) for k, v in (entries or {}).items()}
    except Exception:
        return {}


def save_memory(app_dir: Path, src: str, tgt: str, names: dict[str, str]) -> None:
    path = _memory_path(app_dir, src, tgt)
    path.write_text(
        json.dumps({"names": names, "version": 1}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _find_name_in_translation(name: str, translated: str) -> str | None:
    """Heuristic: capitalized token in target matching source name."""
    t = str(translated or "")
    if not t.strip():
        return None
    key = name.lower()
    for m in re.finditer(r"\b[\w'-]+\b", t):
        tok = m.group(0)
        if tok.lower() == key:
            return tok
    for m in re.finditer(r"\b[А-ЯЁІЇЄA-Z][\w'-]*\b", t):
        tok = m.group(0)
        if key in tok.lower() or tok.lower() in key:
            return tok
    return None


def learn_from_segment(
    app_dir: Path,
    *,
    source: str,
    translated: str,
    src_lang: str,
    tgt_lang: str,
) -> dict[str, str]:
    """Update TM from a successful source→translation pair."""
    names = load_memory(app_dir, src_lang, tgt_lang)
    for tok in extract_preserved_tokens(source):
        found = _find_name_in_translation(tok, translated)
        if found:
            names[tok.lower()] = found
    if names:
        save_memory(app_dir, src_lang, tgt_lang, names)
    return names


def get_memory_proper_nouns(
    source: str,
    *,
    app_dir: Path,
    src_lang: str,
    tgt_lang: str,
) -> list[str]:
    """Known name forms from TM + source extraction (hints only, no text mutation)."""
    seen: set[str] = set()
    out: list[str] = []
    names = load_memory(app_dir, src_lang, tgt_lang)
    for tok in extract_preserved_tokens(source):
        key = tok.lower()
        if key not in seen:
            seen.add(key)
            out.append(tok)
        known = names.get(key)
        if known and known.lower() not in seen:
            seen.add(known.lower())
            out.append(known)
    return out


def memory_summary(app_dir: Path, src: str, tgt: str) -> dict[str, Any]:
    names = load_memory(app_dir, src, tgt)
    return {"pair": f"{src}->{tgt}", "name_count": len(names), "names": list(names.items())[:20]}
