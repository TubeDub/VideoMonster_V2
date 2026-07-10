"""STR Knowledge Base — statistics only, no stored translations."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from engines.str.config import KNOWLEDGE_BASE_FILE, STR_VERSION

_DEFAULT_ENGINE_RECORD: dict[str, Any] = {
    "attempts": 0,
    "successes": 0,
    "errors": 0,
    "total_quality": 0.0,
    "total_speed_ms": 0.0,
    "total_mixed_pct": 0.0,
    "total_retries": 0,
    "long_sentence_quality": 0.0,
    "long_sentence_count": 0,
    "name_damage_count": 0,
    "recent_scores": [],
    "last_used": "",
}


def _kb_path(app_dir: Path) -> Path:
    return app_dir / "data" / KNOWLEDGE_BASE_FILE


def load_knowledge_base(app_dir: Path) -> dict[str, Any]:
    path = _kb_path(app_dir)
    if not path.is_file():
        return {"version": STR_VERSION, "pairs": {}, "updated": ""}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("pairs", {})
            return data
    except Exception:
        pass
    return {"version": STR_VERSION, "pairs": {}, "updated": ""}


def save_knowledge_base(app_dir: Path, data: dict[str, Any]) -> None:
    path = _kb_path(app_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    data["version"] = STR_VERSION
    data["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _pair_key(src: str, tgt: str) -> str:
    return f"{(src or 'en').split('-')[0].lower()}->{(tgt or 'uk').split('-')[0].lower()}"


def _engine_record(kb: dict[str, Any], pair: str, engine_id: str) -> dict[str, Any]:
    pairs = kb.setdefault("pairs", {})
    pair_data = pairs.setdefault(pair, {})
    rec = pair_data.setdefault(engine_id, dict(_DEFAULT_ENGINE_RECORD))
    for k, v in _DEFAULT_ENGINE_RECORD.items():
        rec.setdefault(k, v if not isinstance(v, list) else [])
    return rec


def _segment_bucket(text: str) -> str:
    wc = len(str(text or "").split())
    if wc >= 18:
        return "long"
    if wc >= 8:
        return "medium"
    return "short"


def record_translation(
    app_dir: Path,
    *,
    src_lang: str,
    tgt_lang: str,
    engine_id: str,
    quality_score: float,
    elapsed_ms: float,
    mixed_language_pct: float = 0.0,
    retries: int = 0,
    success: bool = True,
    error: str = "",
    source_text: str = "",
    quality_details: dict[str, Any] | None = None,
) -> None:
    """Update KB after each STR translation attempt."""
    kb = load_knowledge_base(app_dir)
    pair = _pair_key(src_lang, tgt_lang)
    rec = _engine_record(kb, pair, engine_id)

    rec["attempts"] = int(rec.get("attempts", 0)) + 1
    if success:
        rec["successes"] = int(rec.get("successes", 0)) + 1
    if error:
        rec["errors"] = int(rec.get("errors", 0)) + 1

    rec["total_quality"] = float(rec.get("total_quality", 0.0)) + float(quality_score)
    rec["total_speed_ms"] = float(rec.get("total_speed_ms", 0.0)) + float(elapsed_ms)
    rec["total_mixed_pct"] = float(rec.get("total_mixed_pct", 0.0)) + float(mixed_language_pct)
    rec["total_retries"] = int(rec.get("total_retries", 0)) + int(retries)
    rec["last_used"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    qd = quality_details or {}
    if qd.get("missing_preserved_tokens", 0) > 0 or qd.get("wrongful_substitutions", 0) > 0:
        rec["name_damage_count"] = int(rec.get("name_damage_count", 0)) + 1

    if _segment_bucket(source_text) == "long" and success:
        rec["long_sentence_count"] = int(rec.get("long_sentence_count", 0)) + 1
        rec["long_sentence_quality"] = float(rec.get("long_sentence_quality", 0.0)) + quality_score

    recent: list[float] = list(rec.get("recent_scores") or [])
    recent.append(round(quality_score, 1))
    from engines.str.config import TREND_WINDOW

    rec["recent_scores"] = recent[-TREND_WINDOW:]

    save_knowledge_base(app_dir, kb)

    # Keep legacy stats in sync for existing UI/tools
    try:
        from engines.translation_router import record_engine_result

        record_engine_result(
            app_dir,
            src_lang=src_lang,
            tgt_lang=tgt_lang,
            engine=engine_id,
            quality_score=quality_score,
            success=success,
            retries=retries,
        )
    except Exception:
        pass


def engine_stats(
    app_dir: Path,
    src_lang: str,
    tgt_lang: str,
    engine_id: str,
) -> dict[str, Any]:
    kb = load_knowledge_base(app_dir)
    pair = _pair_key(src_lang, tgt_lang)
    rec = (kb.get("pairs") or {}).get(pair, {}).get(engine_id, {})
    attempts = int(rec.get("attempts", 0))
    if attempts <= 0:
        return {"attempts": 0, "avg_quality": -1.0, "avg_speed_ms": -1.0, "avg_mixed_pct": -1.0}

    return {
        "attempts": attempts,
        "successes": int(rec.get("successes", 0)),
        "errors": int(rec.get("errors", 0)),
        "avg_quality": round(float(rec.get("total_quality", 0)) / attempts, 2),
        "avg_speed_ms": round(float(rec.get("total_speed_ms", 0)) / attempts, 1),
        "avg_mixed_pct": round(float(rec.get("total_mixed_pct", 0)) / attempts, 2),
        "avg_retries": round(float(rec.get("total_retries", 0)) / attempts, 2),
        "name_damage_count": int(rec.get("name_damage_count", 0)),
        "long_sentence_avg": (
            round(float(rec.get("long_sentence_quality", 0)) / max(int(rec.get("long_sentence_count", 0)), 1), 2)
            if int(rec.get("long_sentence_count", 0)) > 0
            else -1.0
        ),
        "recent_scores": list(rec.get("recent_scores") or []),
        "last_used": rec.get("last_used", ""),
    }


def pair_summary(app_dir: Path, src_lang: str, tgt_lang: str) -> dict[str, dict[str, Any]]:
    kb = load_knowledge_base(app_dir)
    pair = _pair_key(src_lang, tgt_lang)
    engines = (kb.get("pairs") or {}).get(pair, {})
    return {
        eid: engine_stats(app_dir, src_lang, tgt_lang, eid)
        for eid in engines
    }
