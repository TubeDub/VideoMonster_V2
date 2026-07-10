"""Placeholder leak detection and fuzzy restore — shared guard for MT pipeline."""

from __future__ import annotations

import re
from typing import Any, Callable

# Legacy PERSON_GJR / Cyrillic patterns
_PLACEHOLDER_STRICT = re.compile(
    r"\b(?:PERSON|ORG|TITLE|PLACE|CAR)_[A-Z0-9]{2,12}(?:_\d+)?\b",
    re.IGNORECASE,
)
_PLACEHOLDER_SPACED = re.compile(
    r"\b(?:PERSON|ORG|TITLE|PLACE|CAR)\s+[A-Z0-9]{2,12}(?:\s+\d+)?\b",
    re.IGNORECASE,
)
_PLACEHOLDER_CYR = re.compile(
    r"\b(?:ОСОБА|ОРГ|НАЗВА|МІСЦЕ|МЕСТО|АВТО)_[A-ZА-ЯЁІЇЄ0-9]{2,12}(?:_\d+)?\b",
    re.IGNORECASE,
)

# Broadcast-safe ASCII tokens (preferred — MT corrupts Unicode ⟦ less often)
_BCAST_TOKEN = re.compile(r"\[##\s*(\d+)\s*##\]", re.IGNORECASE)
_BCAST_DAMAGED = re.compile(r"\[##[^\]\n]{0,16}##\]?", re.IGNORECASE)
# MT often eats one '#': [##1##] → [#1#] / [#1##] / [##1#]
_TOKEN_LOOSE = re.compile(r"\[\s*#+\s*(\d+)\s*#+\s*\]", re.IGNORECASE)

# Legacy Unicode opaque (backward compat)
_OPAQUE_TOKEN = re.compile(r"⟦[0-9a-f]{6}⟧", re.IGNORECASE)
_OPAQUE_LOOSE = re.compile(r"⟦\s*([0-9a-fA-F]{4,8})\s*⟧?", re.IGNORECASE)
_OPAQUE_FRAGMENT = re.compile(r"⟦[^⟧\n]{0,24}⟧?", re.IGNORECASE)
_ORPHAN_BRACKET = re.compile(r"⟦\s*,?\s*")

_CJK_GARBAGE = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]{2,}")
_BARE_HEX = re.compile(r"\b[0-9a-f]{5,8}\b", re.IGNORECASE)

_LEGACY_KIND = re.compile(
    r"^(PERSON|ORG|TITLE|PLACE|CAR)_([A-Z0-9]+)(?:_(\d+))?$",
    re.IGNORECASE,
)

_seg_counter = 0


def reset_segment_tokens() -> None:
    global _seg_counter
    _seg_counter = 0


def make_segment_token() -> str:
    """ASCII placeholder [##N##] — survives MT better than Unicode brackets."""
    global _seg_counter
    _seg_counter += 1
    return f"[##{_seg_counter}##]"


def make_opaque_token() -> str:
    return make_segment_token()


def _token_id(token: str) -> str:
    t = str(token or "")
    for pat in (
        re.compile(r"##(\d+)##"),
        _TOKEN_LOOSE,
        re.compile(r"\b(\d+)\b"),
    ):
        m = pat.search(t)
        if m:
            return m.group(1)
    m = re.search(r"([0-9a-f]{4,8})", t, re.I)
    return m.group(1).lower() if m else ""


def global_token_registry(entity_maps: list[dict[str, str]]) -> dict[str, str]:
    """Merge all segment token maps; last wins on duplicate ids."""
    merged: dict[str, str] = {}
    id_seen: dict[str, str] = {}
    for emap in entity_maps or []:
        for tok, ent in (emap or {}).items():
            merged[tok] = ent
            tid = _token_id(tok)
            if tid:
                id_seen[tid] = ent
    return merged


def resolve_token_map_for_text(
    text: str,
    entity_maps: list[dict[str, str]],
) -> dict[str, str]:
    """
    Find token→entity entries for placeholders in text.
    Only uses the provided maps (e.g. translation group) — never global id collision.
    """
    t = str(text or "")
    if not t or not entity_maps:
        return {}
    out: dict[str, str] = {}

    for emap in entity_maps:
        for tok, ent in (emap or {}).items():
            if tok in t:
                out[tok] = ent

    id_owners: dict[str, list[tuple[str, str]]] = {}
    for emap in entity_maps:
        for tok, ent in (emap or {}).items():
            tid = _token_id(tok)
            if tid:
                id_owners.setdefault(tid, []).append((tok, ent))

    for pat in (_BCAST_TOKEN, _TOKEN_LOOSE):
        for m in pat.finditer(t):
            frag = m.group(0)
            if frag in out:
                continue
            tid = m.group(1) if m.lastindex else ""
            owners = id_owners.get(tid or "", [])
            if not owners:
                continue
            picked: tuple[str, str] | None = None
            for tok, ent in owners:
                if tok in t:
                    picked = (tok, ent)
                    break
            if picked is None and len(owners) == 1:
                picked = owners[0]
            if picked:
                out[frag] = picked[1]
                out[picked[0]] = picked[1]
    return out


def _entity_for_token_id(token_map: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for tok, ent in token_map.items():
        tid = _token_id(tok)
        if tid:
            out[tid] = ent
    return out


def detect_placeholder_leaks(text: str) -> list[str]:
    t = str(text or "")
    hits: list[str] = []
    seen: set[str] = set()
    patterns = (
        _PLACEHOLDER_STRICT,
        _PLACEHOLDER_SPACED,
        _PLACEHOLDER_CYR,
        _BCAST_TOKEN,
        _BCAST_DAMAGED,
        _TOKEN_LOOSE,
        _OPAQUE_TOKEN,
        _OPAQUE_LOOSE,
        _OPAQUE_FRAGMENT,
        _CJK_GARBAGE,
    )
    for pat in patterns:
        for m in pat.finditer(t):
            frag = m.group(0)
            if frag not in seen:
                hits.append(frag)
                seen.add(frag)
    if "⟦" in t and "⟦" not in seen:
        hits.append("⟦")
    if "[##" in t and not any("[##" in h for h in hits):
        hits.append("[##…")
    if _TOKEN_LOOSE.search(t) and not any(h.startswith("[") for h in hits):
        m = _TOKEN_LOOSE.search(t)
        if m:
            hits.append(m.group(0))
    return hits


def has_cjk_garbage(text: str) -> bool:
    return bool(_CJK_GARBAGE.search(str(text or "")))


def has_mt_garbage(text: str) -> bool:
    t = str(text or "")
    return bool(
        detect_placeholder_leaks(t)
        or "⟦" in t
        or "[##" in t
        or _TOKEN_LOOSE.search(t)
    )


def has_placeholder_leak(text: str) -> bool:
    return has_mt_garbage(text)


def _replace_once_with_spacing(text: str, fragment: str, replacement: str) -> tuple[str, bool]:
    idx = text.find(fragment)
    if idx < 0:
        return text, False
    repl = str(replacement or "").strip()
    if not repl:
        return text[:idx] + " " + text[idx + len(fragment) :], True
    end = idx + len(fragment)
    before = text[idx - 1] if idx > 0 else " "
    after = text[end] if end < len(text) else " "
    if after.isalnum():
        repl = repl + " "
    if before.isalnum() and not repl.startswith(" "):
        repl = " " + repl
    return text[:idx] + repl + text[end:], True


def collapse_repeated_phrases(text: str, phrases: list[str]) -> str:
    out = str(text or "")
    for phrase in sorted({p.strip() for p in phrases if p and len(p.strip()) >= 4}, key=len, reverse=True):
        p = phrase.strip()
        out = re.sub(r"(?:\s*" + re.escape(p) + r"\s*){2,}", p + " ", out)
        out = re.sub(r"(?:" + re.escape(p) + r"){2,}", p, out)
    return re.sub(r"  +", " ", out).strip()


def sweep_cjk_clusters(text: str, replacements: list[str]) -> tuple[str, list[str]]:
    out = str(text or "")
    notes: list[str] = []
    if not replacements or not has_cjk_garbage(out):
        return out, notes
    idx = 0
    for m in list(_CJK_GARBAGE.finditer(out)):
        repl = replacements[min(idx, len(replacements) - 1)]
        idx += 1
        out, _ = _replace_once_with_spacing(out, m.group(0), repl)
        notes.append(f"cjk:{m.group(0)}→{repl}")
    return out, notes


def _fuzzy_find_bcast(text: str, canonical: str) -> str | None:
    if canonical in text:
        return canonical
    tid = _token_id(canonical)
    if not tid:
        return None
    for pat in (
        re.compile(rf"\[##\s*{re.escape(tid)}\s*##\]", re.I),
        re.compile(rf"\[##\s*{re.escape(tid)}\s*##\]?", re.I),
        re.compile(rf"\[\s*#+\s*{re.escape(tid)}\s*#+\s*\]", re.I),
        re.compile(rf"\[##\s*{re.escape(tid)}", re.I),
        re.compile(rf"\[\s*#+\s*{re.escape(tid)}", re.I),
    ):
        hit = pat.search(text)
        if hit:
            return hit.group(0)
    return None


def _find_next_damaged_token(text: str, token_map: dict[str, str]) -> tuple[str, str] | None:
    """Return (fragment, entity) for the next recoverable damaged token in text."""
    t = str(text or "")
    id_to_entity = _entity_for_token_id(token_map)
    for pat in (_BCAST_TOKEN, _TOKEN_LOOSE, _BCAST_DAMAGED):
        m = pat.search(t)
        if not m:
            continue
        frag = m.group(0)
        tid = m.group(1) if m.lastindex else _token_id(frag)
        ent = id_to_entity.get(tid or "", "")
        if ent:
            return frag, ent
    for tok, ent in sorted(token_map.items(), key=lambda x: -len(x[0])):
        found = _fuzzy_find_token(t, tok)
        if found:
            return found, ent
        if tok in t:
            return tok, ent
    return None


def _fuzzy_find_opaque(text: str, canonical: str) -> str | None:
    if canonical in text:
        return canonical
    hex_id = _token_id(canonical)
    if not hex_id:
        return None
    for pat in (
        re.compile(rf"⟦\s*{re.escape(hex_id)}\s*⟧", re.I),
        re.compile(rf"⟦\s*{re.escape(hex_id)}\s*⟧?", re.I),
        re.compile(rf"⟦{re.escape(hex_id)}", re.I),
    ):
        hit = pat.search(text)
        if hit:
            return hit.group(0)
    return None


def _fuzzy_find_token(text: str, canonical: str) -> str | None:
    if canonical in text:
        return canonical
    if canonical.startswith("[") and "#" in canonical:
        return _fuzzy_find_bcast(text, canonical)
    if canonical.startswith("⟦"):
        return _fuzzy_find_opaque(text, canonical)
    norm_canon = re.sub(r"\s+", "_", canonical.strip()).upper()
    m = _LEGACY_KIND.match(norm_canon)
    if not m:
        return None
    kind, body, num = m.group(1).upper(), m.group(2).upper(), m.group(3) or "1"
    for pat in (
        re.compile(rf"\b{kind}_{body}_{num}\b", re.I),
        re.compile(rf"\b{kind}\s+{body}\s+{num}\b", re.I),
    ):
        hit = pat.search(text)
        if hit:
            return hit.group(0)
    return None


def restore_placeholders_fuzzy(
    text: str,
    token_map: dict[str, str],
    *,
    replace_fn: Callable[[str], str],
    max_passes: int = 128,
) -> tuple[str, list[str]]:
    out = str(text or "")
    restored: list[str] = []
    for _ in range(max_passes):
        hit = _find_next_damaged_token(out, token_map)
        if not hit:
            break
        fragment, entity = hit
        replacement = replace_fn(entity)
        out, ok = _replace_once_with_spacing(out, fragment, replacement)
        if not ok:
            out = out.replace(fragment, replacement, 1)
        restored.append(f"{fragment}→{replacement}")
    return out.strip(), restored


def nuclear_restore_placeholders(
    text: str,
    token_map: dict[str, str],
    *,
    replace_fn: Callable[[str], str],
    max_passes: int = 64,
) -> tuple[str, list[str]]:
    """
    Remove all placeholder debris (⟦, [##, bare hex) and inject display forms.
    """
    out = str(text or "")
    notes: list[str] = []
    if not token_map:
        out = re.sub(r"⟦+|⟧+", " ", out)
        out = _TOKEN_LOOSE.sub(" ", out)
        out = re.sub(r"\[##[^\]]*\]?", " ", out)
        return re.sub(r"  +", " ", out).strip(), notes

    id_to_entity = _entity_for_token_id(token_map)
    entities = list(dict.fromkeys(token_map.values()))
    default_ent = entities[0] if entities else ""

    for _ in range(max_passes):
        if not has_mt_garbage(out) and "⟦" not in out:
            break
        hit = _find_next_damaged_token(out, token_map)
        if hit:
            frag, ent = hit
            out, ok = _replace_once_with_spacing(out, frag, replace_fn(ent))
            if not ok:
                out = out.replace(frag, replace_fn(ent), 1)
            notes.append(f"nuclear:{frag[:12]}")
            continue

        replaced = False
        for pat in (_OPAQUE_FRAGMENT, _ORPHAN_BRACKET):
            m = pat.search(out)
            if not m:
                continue
            frag = m.group(0)
            tid = _token_id(frag)
            ent = id_to_entity.get(tid or "", "")
            if ent:
                out, _ = _replace_once_with_spacing(out, frag, replace_fn(ent))
                notes.append(f"nuclear:{frag[:12]}")
            else:
                pending = [
                    e
                    for e in entities
                    if replace_fn(e).lower() not in out.lower() and e.lower() not in out.lower()
                ]
                if pending:
                    out, _ = _replace_once_with_spacing(out, frag, replace_fn(pending[0]))
                    notes.append(f"nuclear:{frag[:12]}")
                else:
                    out = out.replace(frag, " ", 1)
            replaced = True
            break

        if not replaced and "⟦" in out:
            out = out.replace("⟦", " ", 1)
            replaced = True
        if not replaced and "⟧" in out:
            out = out.replace("⟧", " ", 1)
            replaced = True
        if not replaced:
            for tid, ent in id_to_entity.items():
                pat = re.compile(rf"(?<![0-9a-f]){re.escape(tid)}(?![0-9a-f])", re.I)
                m = pat.search(out)
                if not m:
                    continue
                out, _ = _replace_once_with_spacing(out, m.group(0), replace_fn(ent))
                notes.append(f"hex:{tid}")
                replaced = True
                break
        if not replaced:
            break

    out = re.sub(r"⟦+|⟧+", " ", out)
    out = _TOKEN_LOOSE.sub(" ", out)
    out = re.sub(r"\[##[^\]]*\]?", " ", out)
    out = re.sub(r",\s*,+", ", ", out)
    out = re.sub(r"  +", " ", out).strip()
    return out, notes


def aggressive_opaque_sweep(
    text: str,
    token_map: dict[str, str],
    *,
    replace_fn: Callable[[str], str],
) -> tuple[str, list[str]]:
    return nuclear_restore_placeholders(text, token_map, replace_fn=replace_fn)


def placeholder_health(
    text: str,
    *,
    stage: str,
    token_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    leaks = detect_placeholder_leaks(text)
    ok = not has_mt_garbage(text)
    issues: list[str] = []
    if leaks:
        issues.append(f"placeholder_leak:{','.join(leaks[:5])}")
    return {
        "stage": stage,
        "ok": ok,
        "placeholder_leaks": leaks,
        "placeholder_leak_count": len(leaks),
        "issues": issues,
    }
