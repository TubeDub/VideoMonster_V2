"""Post-job learning — promote rules only with statistical confidence."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from engines.language_intelligence.memory import (
    fetch_correction_stats,
    load_learning_rules,
    record_correction_stat,
    save_learning_rules,
)

MIN_OCCURRENCES = 5
MIN_SUCCESS_RATE = 0.85
MIN_CONFIDENCE = 0.88


def _escape_pattern(word: str) -> str:
    w = str(word or "").strip()
    if not w:
        return ""
    return r"\b" + re.escape(w) + r"\b"


def ingest_job_corrections(
    corrections: list[dict[str, Any]],
    *,
    src_lang: str = "en",
    tgt_lang: str = "uk",
    app_dir: Path | None = None,
) -> dict[str, Any]:
    """Record corrections from one dub job; returns promotion summary."""
    for c in corrections:
        record_correction_stat(
            str(c.get("before") or ""),
            str(c.get("after") or ""),
            category=str(c.get("code") or "learned"),
            src_lang=src_lang,
            tgt_lang=tgt_lang,
            success=True,
            app_dir=app_dir,
        )

    promoted: list[dict[str, Any]] = []
    doc = load_learning_rules(app_dir)
    permanent = {r.get("pattern"): r for r in (doc.get("permanent") or [])}

    stats = fetch_correction_stats(tgt_lang=tgt_lang, min_count=MIN_OCCURRENCES, app_dir=app_dir)
    candidates = {c.get("pattern"): c for c in (doc.get("candidates") or [])}

    for row in stats:
        before = str(row.get("before") or "")
        after = str(row.get("after") or "")
        cnt = int(row.get("count") or 0)
        conf = float(row.get("confidence") or 0.0)
        if cnt < MIN_OCCURRENCES or conf < MIN_SUCCESS_RATE:
            pat = _escape_pattern(before)
            if pat:
                candidates[pat] = {
                    "pattern": pat,
                    "replacement": after,
                    "category": row.get("category") or "learned",
                    "count": cnt,
                    "confidence": conf,
                    "permanent": False,
                }
            continue
        if conf < MIN_CONFIDENCE:
            continue
        pat = _escape_pattern(before)
        if not pat or pat in permanent:
            continue
        rule = {
            "pattern": pat,
            "replacement": after,
            "category": str(row.get("category") or "learned"),
            "count": cnt,
            "confidence": round(conf, 3),
            "permanent": True,
            "promoted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        permanent[pat] = rule
        promoted.append(rule)

    doc["permanent"] = list(permanent.values())
    doc["candidates"] = list(candidates.values())
    doc.setdefault("stats", {})
    doc["stats"]["total_jobs"] = int(doc["stats"].get("total_jobs") or 0) + 1
    doc["stats"]["total_corrections"] = int(doc["stats"].get("total_corrections") or 0) + len(
        corrections
    )
    doc["stats"]["last_job_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    save_learning_rules(doc, app_dir=app_dir)

    return {
        "corrections_recorded": len(corrections),
        "rules_promoted": len(promoted),
        "promoted_rules": promoted,
    }


def run_background_learning(
    corrections: list[dict[str, Any]],
    *,
    src_lang: str = "en",
    tgt_lang: str = "uk",
    app_dir: Path | None = None,
) -> dict[str, Any]:
    """Deep learning pass — safe to call after dub completes."""
    return ingest_job_corrections(
        corrections,
        src_lang=src_lang,
        tgt_lang=tgt_lang,
        app_dir=app_dir,
    )
