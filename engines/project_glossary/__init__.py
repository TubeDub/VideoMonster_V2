"""Project glossary — canonical entity forms per project (not engine hardcode).

HF2: load from projects/{id}/glossary.json or data/glossaries/{id}.json.
Fallback: data/glossaries/default_en_uk.json (includes George Lucas film terms).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.project_glossary")

_APP = Path(__file__).resolve().parents[2]
_CACHE: dict[str, "ProjectGlossary"] = {}


@dataclass
class GlossaryEntry:
    source: str
    canonical: str
    aliases: list[str] = field(default_factory=list)
    kind: str = "ENTITY"
    acceptable: list[str] = field(default_factory=list)  # alternate OK forms
    critical: bool = True


@dataclass
class ProjectGlossary:
    project_id: str
    source_lang: str = "en"
    target_lang: str = "uk"
    entries: list[GlossaryEntry] = field(default_factory=list)

    def entities_for_mask(self) -> list[tuple[str, str, str]]:
        """Longest-first (label, kind, token_hint) for mask_entities."""
        rows: list[tuple[str, str, str]] = []
        seen: set[str] = set()
        for e in self.entries:
            labels = [e.source] + list(e.aliases or [])
            for lab in labels:
                key = lab.lower().strip()
                if not key or key in seen:
                    continue
                seen.add(key)
                hint = re.sub(r"[^A-Za-z0-9]+", "_", lab.upper())[:16] or "ENT"
                rows.append((lab, e.kind or "ENTITY", hint))
        rows.sort(key=lambda x: -len(x[0]))
        return rows

    def canonical_for(self, source_label: str) -> str | None:
        key = str(source_label or "").strip().lower()
        if not key:
            return None
        for e in self.entries:
            labels = [e.source] + list(e.aliases or [])
            if any(lab.lower() == key for lab in labels):
                return e.canonical
        return None

    def critical_sources(self) -> list[str]:
        return [e.source for e in self.entries if e.critical]

    def is_acceptable(self, source_label: str, surface: str) -> bool:
        key = str(source_label or "").strip().lower()
        surf = str(surface or "").strip()
        if not key or not surf:
            return False
        for e in self.entries:
            labels = [e.source] + list(e.aliases or [])
            if not any(lab.lower() == key for lab in labels):
                continue
            forms = [e.canonical, *list(e.acceptable or [])]
            return entity_surface_present(forms, surf)
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "source_lang": self.source_lang,
            "target_lang": self.target_lang,
            "entries": [
                {
                    "source": e.source,
                    "canonical": e.canonical,
                    "aliases": list(e.aliases),
                    "kind": e.kind,
                    "acceptable": list(e.acceptable),
                    "critical": e.critical,
                }
                for e in self.entries
            ],
        }


def _parse(data: dict[str, Any], project_id: str) -> ProjectGlossary:
    entries: list[GlossaryEntry] = []
    for raw in data.get("entries") or []:
        if not isinstance(raw, dict):
            continue
        src = str(raw.get("source") or "").strip()
        can = str(raw.get("canonical") or "").strip()
        if not src or not can:
            continue
        entries.append(
            GlossaryEntry(
                source=src,
                canonical=can,
                aliases=[str(a) for a in (raw.get("aliases") or []) if str(a).strip()],
                kind=str(raw.get("kind") or "ENTITY"),
                acceptable=[
                    str(a) for a in (raw.get("acceptable") or []) if str(a).strip()
                ],
                critical=bool(raw.get("critical", True)),
            )
        )
    return ProjectGlossary(
        project_id=project_id,
        source_lang=str(data.get("source_lang") or "en"),
        target_lang=str(data.get("target_lang") or "uk"),
        entries=entries,
    )


def glossary_paths(app_dir: Path | None = None, project_id: str = "default") -> list[Path]:
    base = Path(app_dir) if app_dir else _APP
    pid = str(project_id or "default").strip() or "default"
    return [
        base / "projects" / pid / "glossary.json",
        base / "data" / "glossaries" / f"{pid}.json",
        base / "data" / "glossaries" / "default_en_uk.json",
    ]


def load_project_glossary(
    *,
    app_dir: Path | None = None,
    project_id: str | None = None,
    info: dict[str, Any] | None = None,
) -> ProjectGlossary:
    """Resolve glossary for task/info. Never hardcode film terms in engine logic."""
    base = Path(app_dir) if app_dir else _APP
    pid = (
        str(project_id or "").strip()
        or str((info or {}).get("glossary_id") or "").strip()
        or str((info or {}).get("project_id") or "").strip()
        or str((info or {}).get("project_uuid") or "").strip()
        or "default"
    )
    cache_key = f"{base}|{pid}"
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    gloss = ProjectGlossary(project_id=pid)
    for path in glossary_paths(base, pid):
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            gloss = _parse(data if isinstance(data, dict) else {}, pid)
            logger.info("[Glossary] loaded %s (%d entries)", path, len(gloss.entries))
            break
        except Exception as exc:
            logger.debug("glossary load failed %s: %s", path, exc)

    _CACHE[cache_key] = gloss
    return gloss


def clear_glossary_cache() -> None:
    _CACHE.clear()


def _token_flex_in(token: str, haystack: str) -> bool:
    """True if token or a light UK/RU stem appears in haystack."""
    t = str(token or "").lower()
    h = str(haystack or "").lower()
    if not t or not h:
        return False
    if t in h:
        return True
    for cut in (3, 2, 1):
        if len(t) > cut + 2:
            stem = t[:-cut]
            if len(stem) >= 3 and stem in h:
                return True
    return False


def entity_surface_present(forms: list[str], translation: str) -> bool:
    """Accept exact forms or inflected UK phrases (рідне місто → рідним містом)."""
    tr = str(translation or "")
    tr_l = tr.lower()
    if not tr_l:
        return False
    for form in forms:
        f = str(form or "").strip()
        if not f:
            continue
        f_l = f.lower()
        if f_l in tr_l:
            return True
        f_stripped = f_l.strip("«»\"'")
        if f_stripped and f_stripped in tr_l:
            return True
        tokens = re.findall(r"[^\W\d_]+", f_l, flags=re.UNICODE)
        content = [tok for tok in tokens if len(tok) > 2]
        if len(content) >= 2 and all(_token_flex_in(tok, tr_l) for tok in content):
            return True
        if len(content) == 1 and len(content[0]) > 3 and _token_flex_in(content[0], tr_l):
            return True
    return False


def check_glossary_entities(
    original: str,
    translation: str,
    glossary: ProjectGlossary,
) -> list[str]:
    """Return missing critical entity sources (labels) from glossary."""
    src = str(original or "")
    tr = str(translation or "")
    missing: list[str] = []
    src_l = src.lower()
    for e in glossary.entries:
        if not e.critical:
            continue
        labels = [e.source] + list(e.aliases or [])
        if not any(lab.lower() in src_l for lab in labels if lab):
            continue
        forms = [e.canonical, *list(e.acceptable or [])]
        if entity_surface_present(forms, tr):
            continue
        missing.append(e.source)
    return missing
