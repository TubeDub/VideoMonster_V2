"""Apply confident fixes — proposals only, pipeline decides apply/suggest/reject."""

from __future__ import annotations

import re
from typing import Any

from engines.language_intelligence import rules as R
from engines.language_intelligence.context import apply_context_fix, propose_context_fixes


def _find_in_source(source: str, token: str) -> bool:
    return bool(re.search(r"(?<!\w)" + re.escape(token) + r"(?!\w)", source or "", re.I))


def propose_brand_name_fixes(original: str, text: str) -> list[dict[str, Any]]:
    out = str(text or "")
    proposals: list[dict[str, Any]] = []

    for latin in R.KEEP_LATIN:
        if not _find_in_source(original, latin):
            continue
        if re.search(r"(?<!\w)" + re.escape(latin) + r"(?!\w)", out, re.I):
            continue
        for bad in R.CYRILLIC_MISTRANSLATIONS.get(latin, []):
            if bad.lower() in out.lower():
                proposals.append(
                    {
                        "code": "brand_latin",
                        "before": bad,
                        "after": latin,
                        "source": "Language Rule",
                        "confidence": 0.99,
                    }
                )
                break

    for src_title, ua_title in R.PREFERRED_UA_TITLES.items():
        if not _find_in_source(original, src_title):
            continue
        if ua_title.lower() in out.lower():
            continue
        if re.search(r"(?<!\w)" + re.escape(src_title) + r"(?!\w)", out, re.I):
            proposals.append(
                {
                    "code": "title_preferred",
                    "before": src_title,
                    "after": ua_title,
                    "source": "Language Rule",
                    "confidence": 0.95,
                }
            )

    for name, tr_name in R.TRANSLITERATE_NAMES.items():
        if not _find_in_source(original, name):
            continue
        if tr_name.lower() in out.lower():
            continue
        if re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", out, re.I):
            proposals.append(
                {
                    "code": "name_transliterate",
                    "before": name,
                    "after": tr_name,
                    "source": "Language Rule",
                    "confidence": 0.93,
                }
            )

    return proposals


def propose_regex_fixes(
    text: str,
    rules: list[tuple[str, str, str, float]],
    *,
    original: str,
) -> list[dict[str, Any]]:
    out = str(text or "")
    proposals: list[dict[str, Any]] = []
    for pat, repl, cat, conf in rules:
        if not re.search(pat, out, flags=re.IGNORECASE):
            continue
        candidate = re.sub(pat, repl, out, count=1, flags=re.IGNORECASE)
        if candidate == out:
            continue
        m = re.search(pat, out, flags=re.IGNORECASE)
        proposals.append(
            {
                "code": cat,
                "before": m.group(0) if m else out[:30],
                "after": repl,
                "candidate_text": candidate,
                "source": "Learned Rule" if cat == "learned" else "Language Rule",
                "confidence": conf,
            }
        )
    return proposals


def apply_proposal(text: str, proposal: dict[str, Any], *, original: str = "") -> str:
    if proposal.get("candidate_text"):
        return str(proposal["candidate_text"]).strip()
    after = str(proposal.get("after") or "")
    before = str(proposal.get("before") or "")
    out = str(text or "")
    if proposal.get("rule_id") or proposal.get("code", "").startswith("context"):
        return apply_context_fix(original, out, proposal)
    if before and before in out:
        return out.replace(before, after, 1)
    if proposal.get("code") in ("title_preferred", "name_transliterate", "brand_latin") and before:
        return re.sub(re.escape(before), after, out, count=1, flags=re.I)
    pat = proposal.get("pattern")
    if pat:
        return re.sub(pat, after, out, count=1, flags=re.IGNORECASE)
    return out


def propose_all_fixes(
    *,
    original: str,
    final: str,
    tgt_lang: str,
    learned_rules: list[dict[str, Any]],
    app_dir=None,
    fast_mode: bool = False,
) -> list[dict[str, Any]]:
    text = str(final or "").strip()
    if not text:
        return []

    proposals: list[dict[str, Any]] = []
    if not fast_mode:
        proposals.extend(propose_context_fixes(original, text, tgt_lang=tgt_lang, app_dir=app_dir))
    proposals.extend(propose_brand_name_fixes(original, text))

    rules = R.all_fix_rules(tgt_lang, learned_rules)
    proposals.extend(propose_regex_fixes(text, rules, original=original))

    if not fast_mode:
        words = text.split()
        for i in range(1, len(words)):
            if words[i].lower() == words[i - 1].lower() and len(words[i]) > 2:
                proposals.append(
                    {
                        "code": "repetition",
                        "before": f"{words[i-1]} {words[i]}",
                        "after": words[i],
                        "candidate_text": " ".join(
                            words[: i - 1] + [words[i]] + words[i + 1 :]
                        ),
                        "source": "Language Rule",
                        "confidence": 0.88,
                    }
                )
                break

    return proposals
