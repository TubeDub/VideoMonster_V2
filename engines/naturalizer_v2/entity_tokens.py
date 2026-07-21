"""Protected entity tokenization — mask before MT, restore after Naturalizer."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from engines.placeholder_guard import (
    has_mt_garbage,
    make_segment_token,
    nuclear_restore_placeholders,
    reset_segment_tokens,
    restore_placeholders_fuzzy,
)

# Longest-first entity patterns (source-side, before MT)
_BUILTIN_ENTITIES: list[tuple[str, str, str]] = [
    ("University of Southern California", "ORG", "ORG_USC"),
    ("George Lucas", "PERSON", "PERSON_GL"),
    ("George Jr.", "PERSON", "PERSON_GJR"),
    ("George Jr", "PERSON", "PERSON_GJR"),
    ("Haskell Wexler", "PERSON", "PERSON_HW"),
    ("Star Wars", "TITLE", "TITLE_SW"),
    ("Hollywood", "PLACE", "PLACE_HW"),
    ("Fiat", "CAR", "CAR_FIAT"),
    ("USC", "ORG", "ORG_USC2"),
    ("U.S.C.", "ORG", "ORG_USC2"),
]


def _catalog_entities(app_dir: Path | None) -> list[tuple[str, str, str]]:
    from engines.proper_nouns_dict import (
        keep_latin_tokens,
        preferred_translations,
        transliterate_names,
    )

    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()

    # HF2: project glossary first (canonical source of entities — not engine hardcode)
    try:
        from engines.project_glossary import load_project_glossary

        gloss = load_project_glossary(app_dir=app_dir)
        for label, kind, tok in gloss.entities_for_mask():
            key = label.lower()
            if key not in seen:
                out.append((label, kind, tok))
                seen.add(key)
    except Exception:
        pass

    # Builtin fallback only for labels not already in glossary
    for label, kind, tok in _BUILTIN_ENTITIES:
        if label.lower() not in seen:
            out.append((label, kind, tok))
            seen.add(label.lower())

    base = app_dir or Path(__file__).resolve().parent.parent.parent
    for latin in keep_latin_tokens(base):
        if latin.lower() not in seen:
            out.append((latin, "ORG", f"ORG_{latin.upper()[:8]}"))
            seen.add(latin.lower())
    for title in preferred_translations(base):
        if title.lower() not in seen:
            out.append((title, "TITLE", f"TITLE_{len(seen)}"))
            seen.add(title.lower())
    for name in transliterate_names(base):
        if name.lower() not in seen:
            out.append((name, "PERSON", f"PERSON_{len(seen)}"))
            seen.add(name.lower())
    out.sort(key=lambda x: -len(x[0]))
    return out


def mask_entities(
    text: str,
    *,
    app_dir: Path | None = None,
) -> tuple[str, dict[str, str]]:
    """
    Replace known entities with tokens like PERSON_1, ORG_1.
    Returns (masked_text, token_to_original).
    """
    out = str(text or "")
    token_map: dict[str, str] = {}
    entity_to_token: dict[str, str] = {}

    for entity, kind, token_base in _catalog_entities(app_dir):
        pat = re.compile(r"(?<!\w)" + re.escape(entity) + r"(?!\w)", re.IGNORECASE)
        if not pat.search(out):
            continue
        key = entity.lower()
        if key not in entity_to_token:
            tok = make_segment_token()
            entity_to_token[key] = tok
            token_map[tok] = entity
        out = pat.sub(entity_to_token[key], out)

    return out.strip(), token_map


def restore_entities(
    text: str,
    token_map: dict[str, str],
    *,
    original: str = "",
    tgt_lang: str = "uk",
    app_dir: Path | None = None,
) -> tuple[str, list[str]]:
    """
    Restore tokens to proper target-language forms.
    Returns (text, list of restored entity labels).
    """
    out = str(text or "")
    restored: list[str] = []

    def _replace(entity: str) -> str:
        return _target_form(
            entity,
            original=original,
            tgt_lang=tgt_lang,
            app_dir=app_dir,
        )

    out, fuzzy = restore_placeholders_fuzzy(out, token_map, replace_fn=_replace)
    restored.extend(fuzzy)

    for token, entity in sorted(token_map.items(), key=lambda x: -len(x[0])):
        if token not in out:
            continue
        replacement = _target_form(entity, original=original, tgt_lang=tgt_lang, app_dir=app_dir)
        out = out.replace(token, replacement)
        restored.append(f"{entity}→{replacement}")

    out, nuclear = nuclear_restore_placeholders(out, token_map, replace_fn=_replace)
    restored.extend(nuclear)

    display_forms = [_target_form(e, original=original, tgt_lang=tgt_lang, app_dir=app_dir) for e in token_map.values()]
    from engines.placeholder_guard import collapse_repeated_phrases, sweep_cjk_clusters

    out = collapse_repeated_phrases(out, display_forms)
    out, cjk_notes = sweep_cjk_clusters(out, display_forms)
    restored.extend(cjk_notes)

    if has_mt_garbage(out) and token_map:
        out, nuclear2 = nuclear_restore_placeholders(out, token_map, replace_fn=_replace)
        restored.extend(nuclear2)

    if original.strip():
        from engines.naturalizer_v2.entity_fixup import sanitize_wrong_entity_substitutions
        from engines.proper_nouns_dict import apply_proper_noun_polish

        out = sanitize_wrong_entity_substitutions(
            out, original=original, tgt_lang=tgt_lang
        )
        polished = apply_proper_noun_polish(
            original, out, app_dir=app_dir, tgt_lang=tgt_lang
        )
        if polished != out:
            out = polished
            restored.append("proper_noun_polish")
        if (tgt_lang or "").split("-")[0].lower() == "ru":
            from engines.translation_naturalizer import fix_ru_jr_suffix

            ru_out = fix_ru_jr_suffix(out)
            if ru_out != out:
                out = ru_out
                restored.append("ru_jr_suffix")

    return out.strip(), restored


def _target_form(
    entity: str,
    *,
    original: str,
    tgt_lang: str,
    app_dir: Path | None,
) -> str:
    from engines.proper_nouns_dict import (
        apply_preferred_translations,
        apply_name_transliterations,
        restore_never_translate_tokens,
    )

    from engines.naturalizer_v2.uk_name_forms import GEORGE_LUCAS_UK

    lang = (tgt_lang or "uk").split("-")[0].lower()
    src = str(original or "")

    # HF2: project glossary canonical wins
    try:
        from engines.project_glossary import load_project_glossary

        gloss = load_project_glossary(app_dir=app_dir)
        can = gloss.canonical_for(entity)
        if can:
            return can
    except Exception:
        pass

    if entity.lower() not in src.lower():
        from engines.naturalizer_v2.entity_fixup import sanitize_wrong_entity_substitutions

        probe = sanitize_wrong_entity_substitutions(
            entity, original=src, tgt_lang=tgt_lang
        )
        if probe != entity:
            return probe
        return entity

    out = entity
    out = restore_never_translate_tokens(src, out, app_dir=app_dir)
    out = apply_preferred_translations(src, out, app_dir=app_dir)
    out = apply_name_transliterations(src, out, app_dir=app_dir)

    if lang == "uk" and entity == "Hollywood" and entity.lower() in src.lower():
        if "Hollywood" not in out and "Голлівуд" not in out:
            return "Голлівуд"
    if entity == "University of Southern California":
        if lang == "uk":
            return "USC"  # glossary default; full name acceptable via glossary.acceptable
        if lang == "ru":
            return "Университет Южной Калифорнии"
    if entity in ("George Jr.", "George Jr") and lang == "uk":
        from engines.naturalizer_v2.uk_name_forms import george_jr_target_form

        return george_jr_target_form(original=src, tgt_lang=tgt_lang)
    if entity == "George Lucas" and lang == "uk":
        return GEORGE_LUCAS_UK
    if entity == "Star Wars" and lang == "uk":
        from engines.naturalizer_v2.uk_name_forms import STAR_WARS_UK

        return STAR_WARS_UK
    if entity == "Fiat" and lang == "uk":
        return "Fiat"
    if entity == "Haskell Wexler" and lang == "uk":
        return "Хаскелл Векслер"
    if entity == "USC" and lang == "uk":
        return "USC"

    return out


def mask_segments(
    segments: list[str],
    *,
    app_dir: Path | None = None,
) -> tuple[list[str], list[dict[str, str]]]:
    """Mask entities with globally unique [##N##] ids across all segments."""
    reset_segment_tokens()
    masked: list[str] = []
    maps: list[dict[str, str]] = []
    for seg in segments:
        m, mp = mask_entities(seg, app_dir=app_dir)
        masked.append(m)
        maps.append(mp)
    return masked, maps


def merge_entity_maps(*maps: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for mp in maps:
        for k, v in (mp or {}).items():
            out.setdefault(k, v)
    return out


def entity_context_for_segment(
    segment_index: int,
    *,
    groups: list[list[int]],
    entity_maps: list[dict[str, str]],
    source_segments: list[str],
) -> tuple[dict[str, str], str]:
    """Entity map + source text for a segment, including merged translation groups."""
    grp = (segment_index,)
    for g in groups:
        if segment_index in g:
            grp = tuple(g)
            break
    emap = merge_entity_maps(
        *[entity_maps[j] for j in grp if 0 <= j < len(entity_maps)]
    )
    orig = " ".join(
        str(source_segments[j] or "").strip()
        for j in grp
        if 0 <= j < len(source_segments) and str(source_segments[j] or "").strip()
    ).strip()
    return emap, orig
