"""Context Intelligence — rules depend on sentence context, not isolated words."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any


def _app_dir(app_dir: Path | None) -> Path:
    return app_dir or Path(__file__).resolve().parent.parent.parent


@lru_cache(maxsize=4)
def _load_context_rules(app_dir_str: str) -> list[dict[str, Any]]:
    path = Path(app_dir_str) / "data" / "language_intelligence" / "context_rules.json"
    if not path.is_file():
        return _builtin_context_rules()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        rules = data.get("rules") if isinstance(data, dict) else []
        return list(rules) if rules else _builtin_context_rules()
    except Exception:
        return _builtin_context_rules()


def _builtin_context_rules() -> list[dict[str, Any]]:
    return [
        {
            "id": "name_jr",
            "source_pattern": r"\b([A-Z][a-z]+)\s+Jr\.?\b",
            "target_pattern": r"\bJunior\s+\w+",
            "anti_match_source": False,
            "replacement": r"\1 молодший",
            "confidence": 0.98,
            "category": "context_name",
        },
        {
            "id": "junior_title",
            "source_pattern": r"\bJunior\s+(\w+)",
            "target_pattern": r"\bмолодший\s+\1",
            "anti_match_source": True,
            "replacement": r"молодший \1",
            "confidence": 0.92,
            "category": "context_title",
        },
    ]


def propose_context_fixes(
    original: str,
    text: str,
    *,
    tgt_lang: str = "uk",
    app_dir: Path | None = None,
) -> list[dict[str, Any]]:
    if tgt_lang.split("-")[0] != "uk":
        return []

    src = str(original or "")
    out = str(text or "")
    proposals: list[dict[str, Any]] = []
    base = str(_app_dir(app_dir))

    m_jr = re.search(r"\b([A-Z][a-z]+)\s+Jr\.?\b", src)
    if m_jr and re.search(r"\bJunior\b", src, re.I) is None:
        name = m_jr.group(1)
        full = m_jr.group(0)
        candidate = f"{name} молодший"
        if candidate.lower() not in out.lower():
            candidate_text = (
                re.sub(re.escape(full), candidate, out, count=1, flags=re.I)
                if full.lower() in out.lower()
                else out.replace(full, candidate, 1)
            )
            proposals.append(
                {
                    "code": "context_name",
                    "before": full,
                    "after": candidate,
                    "candidate_text": candidate_text,
                    "source": "Context Rule",
                    "confidence": 0.98,
                    "rule_id": "name_jr",
                }
            )

    if re.search(r"\bJunior\s+\w+", src, re.I) and not re.search(
        r"\bJr\.?\b", src, re.I
    ):
        m = re.search(r"\bJunior\s+(\w+)", src, re.I)
        if m and re.search(r"\bJunior\s+" + m.group(1), out, re.I):
            cand = re.sub(r"\bJunior\s+(\w+)", r"молодший \1", out, count=1, flags=re.I)
            proposals.append(
                {
                    "code": "context_title",
                    "before": m.group(0),
                    "after": f"молодший {m.group(1)}",
                    "candidate_text": cand,
                    "source": "Context Rule",
                    "confidence": 0.92,
                    "rule_id": "junior_title",
                }
            )

    for rule in _load_context_rules(base):
        if rule.get("id") in ("name_jr", "junior_title"):
            continue
        src_pat = rule.get("source_pattern") or ""
        if src_pat and re.search(src_pat, src, re.I):
            repl = str(rule.get("replacement") or "")
            proposals.append(
                {
                    "code": rule.get("category") or "context",
                    "before": out[:40],
                    "after": repl,
                    "source": "Context Rule",
                    "confidence": float(rule.get("confidence") or 0.9),
                    "rule_id": rule.get("id"),
                }
            )
    return proposals


def apply_context_fix(
    original: str,
    text: str,
    proposal: dict[str, Any],
) -> str:
    """Apply one context fix when pattern matches."""
    after = str(proposal.get("after") or "").strip()
    if not after:
        return text
    rule_id = proposal.get("rule_id", "")
    if rule_id == "name_jr":
        m = re.search(r"\b([A-Z][a-z]+)\s+Jr\.?\b", original)
        if m:
            name = m.group(1)
            return re.sub(
                rf"\b{re.escape(name)}\s+молодший\b",
                f"{name} молодший",
                text,
                count=1,
                flags=re.I,
            ) or f"{name} молодший"
    if rule_id == "junior_title":
        return re.sub(r"\bJunior\s+(\w+)", r"молодший \1", text, count=1, flags=re.I)
    return after if len(after) > 3 else text
