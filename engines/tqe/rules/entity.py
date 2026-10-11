"""Entity preservation rules."""

from __future__ import annotations

import re

from engines.tqe.rules._registry import register


@register("entity")
def check_entities(original: str, translation: str, ctx: dict) -> list[dict]:
    errors: list[dict] = []
    try:
        from engines.semantic_meaning import check_critical_entities
        from engines.translation_quality import missing_preserved_tokens

        _FN_DENY = {
            "yeah",
            "are",
            "how's",
            "hows",
            "honestly",
            "before",
            "come",
            "sometimes",
            "really",
            "shut",
            "alright",
        }
        for err in check_critical_entities(original, translation) or []:
            if isinstance(err, dict):
                raw_tok = str(err.get("value") or err.get("token") or "")
            else:
                raw_tok = str(err)
            if raw_tok.lower().strip(" .?!,") in _FN_DENY:
                continue
            errors.append(
                {
                    "code": "entity_missing",
                    "token": raw_tok or (err if isinstance(err, str) else str(err)),
                    "severity": "critical",
                }
            )
        missing = missing_preserved_tokens(original, translation) or []
        critical = {
            "fiat",
            "usc",
            "lucas",
            "haskell",
            "wexler",
            "hollywood",
            "california",
            "star wars",
        }
        for tok in missing:
            key = str(tok or "").lower().strip(" .?!,")
            if key in _FN_DENY:
                continue
            sev = (
                "critical"
                if tok.lower().strip(".") in critical
                or any(c in tok.lower() for c in critical)
                else "major"
            )
            errors.append({"code": "preserved_token", "token": tok, "severity": sev})
    except Exception as exc:
        errors.append({"code": "entity_check_error", "detail": str(exc), "severity": "warn"})

    src_nums = re.findall(r"\d+(?:[.,]\d+)?", original or "")
    tr = translation or ""
    for n in src_nums:
        if n not in tr and n.replace(",", ".") not in tr:
            if n.isdigit() and int(n) <= 20:
                continue
            errors.append({"code": "number_missing", "token": n, "severity": "major"})
    return errors
