"""Engine ranking from Knowledge Base + registry priority."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from engines.mt.base import BaseMTEngine
from engines.str.knowledge_base import engine_stats


def _norm(code: str) -> str:
    return (code or "en").split("-")[0].lower()


def compute_engine_score(
    engine: BaseMTEngine,
    stats: dict[str, Any],
    *,
    prefer_offline: bool = True,
    source_word_count: int = 0,
) -> tuple[float, str]:
    """
    Internal ranking score for one engine on a language pair.
    Higher = try first.
    """
    attempts = int(stats.get("attempts", 0))
    if attempts <= 0:
        base = 100.0 - engine.priority
        reason = f"default priority={engine.priority}"
        if prefer_offline and engine.offline:
            base += 5.0
            reason += " offline_bonus"
        return base, reason

    avg_q = float(stats.get("avg_quality", 0))
    successes = int(stats.get("successes", 0))
    errors = int(stats.get("errors", 0))
    avg_speed = float(stats.get("avg_speed_ms", 0))
    avg_mixed = float(stats.get("avg_mixed_pct", 0))
    success_rate = successes / max(attempts, 1)
    error_penalty = min(20.0, errors * 3.0)
    mixed_penalty = min(15.0, avg_mixed * 0.5)
    speed_bonus = max(0.0, 5.0 - avg_speed / 2000.0) if avg_speed > 0 else 0.0

    score = avg_q * 0.55 + success_rate * 35.0 - error_penalty - mixed_penalty + speed_bonus

    if source_word_count >= 18:
        long_avg = float(stats.get("long_sentence_avg", -1))
        if long_avg >= 0:
            score = score * 0.6 + long_avg * 0.4
            reason = f"long_sent avg={long_avg:.1f}"
        else:
            reason = f"history avg={avg_q:.1f}"
    else:
        reason = f"history avg={avg_q:.1f}"

    if prefer_offline and engine.offline:
        score += 3.0

    return round(score, 2), reason


def ranked_engines_for_pair(
    app_dir: Path,
    src_lang: str,
    tgt_lang: str,
    *,
    source_text: str = "",
    prefer_offline: bool = True,
) -> list[tuple[BaseMTEngine, float, str]]:
    from engines.str.adapters import list_available_engines

    src, tgt = _norm(src_lang), _norm(tgt_lang)
    wc = len(str(source_text or "").split())
    available = [e for e in list_available_engines() if e.supports_pair(src, tgt)]
    if not available:
        return []

    scored: list[tuple[float, BaseMTEngine, str]] = []
    for eng in available:
        stats = engine_stats(app_dir, src, tgt, eng.id)
        score, reason = compute_engine_score(
            eng,
            stats,
            prefer_offline=prefer_offline,
            source_word_count=wc,
        )
        scored.append((score, eng, reason))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [(eng, sc, reason) for sc, eng, reason in scored]


def engine_order_ids(
    app_dir: Path,
    src_lang: str,
    tgt_lang: str,
    *,
    source_text: str = "",
) -> tuple[list[str], str]:
    ranked = ranked_engines_for_pair(
        app_dir, src_lang, tgt_lang, source_text=source_text
    )
    if not ranked:
        return [], "no_engines"
    order = [eng.id for eng, _, _ in ranked]
    top_reason = ranked[0][2] if ranked else "default"
    return order, f"str_rank: {top_reason} (top={ranked[0][1]:.1f})"
