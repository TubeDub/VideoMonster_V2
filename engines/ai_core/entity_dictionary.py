"""Unified Entity Dictionary — names, orgs, abbreviations for dubbing."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from engines.ai_core.translation_agent.validators.entity_validator import extract_entities
from engines.mt.lang_codes import normalize_lang

# Canonical forms when source mentions these patterns (case-insensitive keys).
_CANONICAL_ENTITIES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bGeorge\s+Jr\.?\b", re.I), "Джордж-молодший"),
    (re.compile(r"\bGeorge\s+Lucas\b", re.I), "Джордж Лукас"),
    (re.compile(r"\bUSC\b|University of Southern California", re.I), "USC"),
    (re.compile(r"\bHollywood\b", re.I), "Голлівуд"),
    (re.compile(r"\bHaskell\s+Wexler\b", re.I), "Хаскелл Векслер"),
    (re.compile(r"\bintensive care unit\b|\bICU\b", re.I), "реанімації"),
]

# Wrong MT forms → canonical
_WRONG_FORMS_UK: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bДжордж\s+старш(?:ий|ого)\b", re.I), "Джордж-молодший"),
    (re.compile(r"\bДжордж\s+молодш(?:ий|ого)\b", re.I), "Джордж-молодший"),
    (re.compile(r"компанії з фільму [«\"]?Скарб США[»\"]?", re.I), "USC film school"),
    (re.compile(r"\bСкарб США\b", re.I), "USC"),
    (re.compile(r"\bпереможного\s+їзда\b", re.I), "переможця"),
    (re.compile(r"\bпереможна\s+швидкість\b", re.I), "переможець"),
]


@dataclass
class EntityEntry:
    source: str
    canonical: str
    kind: str = "name"  # name|org|geo|abbr|number|date


@dataclass
class EntityDictionary:
    """Project-wide entity map built from source segments."""

    entries: list[EntityEntry] = field(default_factory=list)
    target_lang: str = "uk"

    @classmethod
    def from_segments(
        cls,
        segments: list[dict[str, Any]],
        *,
        target_lang: str = "uk",
        manifest: dict[str, Any] | None = None,
    ) -> EntityDictionary:
        lang = normalize_lang(target_lang)
        entries: list[EntityEntry] = []
        seen: set[str] = set()

        for seg in segments:
            source = str(seg.get("text") or "").strip()
            if not source:
                continue
            for ent in extract_entities(source):
                key = ent.lower()
                if key in seen:
                    continue
                seen.add(key)
                entries.append(EntityEntry(source=ent, canonical=ent, kind="name"))

            for pattern, canonical in _CANONICAL_ENTITIES:
                m = pattern.search(source)
                if m:
                    key = canonical.lower()
                    if key not in seen:
                        seen.add(key)
                        entries.append(
                            EntityEntry(
                                source=m.group(0),
                                canonical=canonical,
                                kind="abbr" if canonical.isupper() else "name",
                            )
                        )

        glossary = (manifest or {}).get("glossary") or {}
        if isinstance(glossary, dict):
            for src, tgt in glossary.items():
                key = str(src).lower()
                if key not in seen:
                    seen.add(key)
                    entries.append(
                        EntityEntry(source=str(src), canonical=str(tgt), kind="term")
                    )

        return cls(entries=entries, target_lang=lang)

    def apply(self, text: str, *, source: str = "") -> str:
        """Enforce canonical entity forms in adapted text."""
        out = str(text or "").strip()
        if not out:
            return out

        src_blob = f"{source} {out}"
        for pattern, canonical in _CANONICAL_ENTITIES:
            if pattern.search(source or src_blob):
                if canonical == "Джордж-молодший":
                    out = re.sub(r"\bДжордж\s+старш(?:ий|ого)\b", canonical, out, flags=re.I)
                    out = re.sub(r"\bДжордж\s+молодш(?:ий|ого)\b", canonical, out, flags=re.I)
                if canonical == "USC":
                    out = re.sub(r"компанії з фільму [«\"]?Скарб США[»\"]?", "USC film school", out, flags=re.I)
                    out = re.sub(r"\bСкарб США\b", "USC", out, flags=re.I)

        if normalize_lang(self.target_lang) == "uk":
            for pattern, replacement in _WRONG_FORMS_UK:
                out = pattern.sub(replacement, out)

        for entry in self.entries:
            if entry.kind in ("name", "abbr", "org") and entry.source.lower() in (source or "").lower():
                if entry.canonical and entry.canonical.lower() not in out.lower():
                    wrong = re.compile(re.escape(entry.source), re.I)
                    if wrong.search(out):
                        out = wrong.sub(entry.canonical, out)

        return out.strip()

    def accuracy(self, text: str, *, source: str = "") -> float:
        """0..1 — how well entities from source appear correctly in text."""
        if not source.strip():
            return 1.0
        issues = 0
        checks = 0
        for pattern, canonical in _CANONICAL_ENTITIES:
            if pattern.search(source):
                checks += 1
                if canonical.lower() not in str(text or "").lower() and canonical not in str(text or ""):
                    if canonical == "USC" and "USC" in str(text or ""):
                        continue
                    issues += 1
        for pattern, _ in _WRONG_FORMS_UK:
            if pattern.search(str(text or "")):
                checks += 1
                issues += 1
        if checks == 0:
            ev_missing = []
            for ent in extract_entities(source):
                checks += 1
                if ent not in str(text or "") and ent.lower() not in str(text or "").lower():
                    ev_missing.append(ent)
            if ev_missing:
                issues += len(ev_missing)
        if checks == 0:
            return 1.0
        return round(max(0.0, 1.0 - issues / checks), 4)
