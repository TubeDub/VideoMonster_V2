"""Smart Translation Router — route selection, pivot fallback, engine learning."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from engines.translation_quality_score import compute_quality_score

logger = logging.getLogger("tubedub.engines.translation_router")

ROUTER_VERSION = 5
STATS_FILE = "translation_engine_stats.json"

# Well-connected hub languages for pivot discovery (language-agnostic)
_HUB_LANGS = ("ru", "en", "de", "fr", "es", "pl", "pt", "it", "uk", "tr", "nl")


@dataclass
class TranslationRoute:
    """One translation path: e.g. direct en→uk or pivot en→ru→uk."""

    name: str
    chain: list[tuple[str, str]] = field(default_factory=list)

    @property
    def label(self) -> str:
        if len(self.chain) <= 1:
            a, b = self.chain[0]
            return f"{a}→{b}"
        parts = [self.chain[0][0]]
        for _s, t in self.chain:
            parts.append(t)
        return "→".join(parts)

    @property
    def is_direct(self) -> bool:
        return len(self.chain) == 1


def _norm(code: str | None) -> str:
    return (code or "en").split("-")[0].lower()


def _pair_key(src: str, tgt: str) -> str:
    return f"{_norm(src)}->{_norm(tgt)}"


def _stats_path(app_dir: Path) -> Path:
    return app_dir / "data" / STATS_FILE


def load_engine_stats(app_dir: Path) -> dict[str, Any]:
    path = _stats_path(app_dir)
    if not path.is_file():
        return {"pairs": {}, "routes": {}, "version": 2}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"pairs": {}, "routes": {}, "version": 2}
    except Exception:
        return {"pairs": {}, "routes": {}, "version": 2}


def save_engine_stats(app_dir: Path, data: dict[str, Any]) -> None:
    path = _stats_path(app_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    data["version"] = 2
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _engine_record(stats: dict[str, Any], pair: str, engine: str) -> dict[str, Any]:
    pairs = stats.setdefault("pairs", {})
    rec = pairs.setdefault(pair, {})
    return rec.setdefault(
        engine,
        {
            "attempts": 0,
            "successes": 0,
            "total_quality": 0.0,
            "manual_corrections": 0,
            "retries": 0,
            "last_used": "",
        },
    )


def _route_record(stats: dict[str, Any], route_label: str) -> dict[str, Any]:
    routes = stats.setdefault("routes", {})
    return routes.setdefault(
        route_label,
        {"attempts": 0, "successes": 0, "total_quality": 0.0, "last_used": ""},
    )


def record_engine_result(
    app_dir: Path,
    *,
    src_lang: str,
    tgt_lang: str,
    engine: str,
    quality_score: float,
    success: bool,
    retries: int = 0,
    route_label: str = "",
) -> None:
    stats = load_engine_stats(app_dir)
    eng = _engine_record(stats, _pair_key(src_lang, tgt_lang), engine)
    eng["attempts"] = int(eng.get("attempts", 0)) + 1
    if success:
        eng["successes"] = int(eng.get("successes", 0)) + 1
    eng["total_quality"] = float(eng.get("total_quality", 0.0)) + float(quality_score)
    eng["retries"] = int(eng.get("retries", 0)) + int(retries)
    eng["last_used"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if route_label:
        rr = _route_record(stats, route_label)
        rr["attempts"] = int(rr.get("attempts", 0)) + 1
        if success:
            rr["successes"] = int(rr.get("successes", 0)) + 1
        rr["total_quality"] = float(rr.get("total_quality", 0.0)) + float(quality_score)
        rr["last_used"] = eng["last_used"]
    save_engine_stats(app_dir, stats)


def record_manual_correction(
    app_dir: Path,
    *,
    src_lang: str,
    tgt_lang: str,
    engine: str,
) -> None:
    stats = load_engine_stats(app_dir)
    eng = _engine_record(stats, _pair_key(src_lang, tgt_lang), engine)
    eng["manual_corrections"] = int(eng.get("manual_corrections", 0)) + 1
    save_engine_stats(app_dir, stats)


def _leg_score(app_dir: Path, src: str, tgt: str) -> float:
    ranked = _engine_rankings(app_dir, src, tgt)
    return ranked[0][1] if ranked else 50.0


def _route_historical_score(app_dir: Path, route: TranslationRoute) -> float:
    stats = load_engine_stats(app_dir)
    rr = (stats.get("routes") or {}).get(route.label, {})
    attempts = int(rr.get("attempts", 0))
    if attempts > 0:
        return float(rr.get("total_quality", 0.0)) / attempts
    leg_scores = [_leg_score(app_dir, s, t) for s, t in route.chain]
    return sum(leg_scores) / max(len(leg_scores), 1)


def _engine_rankings(
    app_dir: Path,
    src_lang: str,
    tgt_lang: str,
) -> list[tuple[str, float, str]]:
    from engines.mt.registry import ordered_engines_for_pair

    stats = load_engine_stats(app_dir)
    pair = _pair_key(src_lang, tgt_lang)
    pair_data = (stats.get("pairs") or {}).get(pair, {})
    ranked: list[tuple[str, float, str]] = []
    for eng in ordered_engines_for_pair(app_dir, src_lang, tgt_lang):
        rec = pair_data.get(eng.id, {})
        attempts = int(rec.get("attempts", 0))
        successes = int(rec.get("successes", 0))
        total_q = float(rec.get("total_quality", 0.0))
        manual = int(rec.get("manual_corrections", 0))
        if attempts <= 0:
            avg_q = 100.0 - eng.priority
            reason = "default_priority"
        else:
            avg_q = total_q / max(attempts, 1)
            success_rate = successes / attempts
            penalty = min(15.0, manual * 2.0)
            avg_q = avg_q * 0.7 + success_rate * 30.0 - penalty
            reason = f"history avg={avg_q:.1f}"
        ranked.append((eng.id, avg_q, reason))
    ranked.sort(key=lambda x: x[1], reverse=True)
    return ranked


def engine_rankings(app_dir: Path, src_lang: str, tgt_lang: str) -> list[tuple[str, float, str]]:
    return _engine_rankings(app_dir, src_lang, tgt_lang)


def select_engine_order(
    app_dir: Path,
    src_lang: str,
    tgt_lang: str,
) -> tuple[list[str], str]:
    ranked = _engine_rankings(app_dir, src_lang, tgt_lang)
    order = [e for e, _, _ in ranked]
    reason = ranked[0][2] if ranked else "default"
    return order, f"{_pair_key(src_lang, tgt_lang)}: {reason}"


def _discover_pivot_hubs(app_dir: Path, src: str, tgt: str) -> list[str]:
    """Universal pivot hub ranking — not hardcoded to any target language."""
    src_n, tgt_n = _norm(src), _norm(tgt)
    stats = load_engine_stats(app_dir)
    candidates: set[str] = set()
    for hub in _HUB_LANGS:
        if hub not in (src_n, tgt_n):
            candidates.add(hub)
    for pair_key in (stats.get("pairs") or {}):
        if "->" not in pair_key:
            continue
        a, b = pair_key.split("->", 1)
        for lang in (a, b):
            if lang not in (src_n, tgt_n):
                candidates.add(lang)

    scored: list[tuple[str, float]] = []
    for hub in candidates:
        leg1 = _leg_score(app_dir, src_n, hub)
        leg2 = _leg_score(app_dir, hub, tgt_n)
        scored.append((hub, (leg1 + leg2) / 2.0))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [h for h, _ in scored[:5]]


def candidate_routes(
    src_lang: str,
    tgt_lang: str,
    app_dir: Path,
) -> list[TranslationRoute]:
    """Direct route first, then pivot routes ordered by historical success."""
    src_n, tgt_n = _norm(src_lang), _norm(tgt_lang)
    if src_n == tgt_n:
        return [TranslationRoute("direct", [(src_n, tgt_n)])]

    routes: list[TranslationRoute] = [
        TranslationRoute("direct", [(src_n, tgt_n)]),
    ]
    for hub in _discover_pivot_hubs(app_dir, src_n, tgt_n):
        routes.append(
            TranslationRoute(f"via_{hub}", [(src_n, hub), (hub, tgt_n)])
        )

    # Configured CJK fallback (zh→en→uk) — ensure present early
    fb = fallback_route_for_pair(app_dir, src_n, tgt_n)
    if fb is not None:
        labels = {r.label for r in routes}
        if fb.label not in labels and fb.name not in {r.name for r in routes}:
            # Normalize name to via_en style when chain is src→en→tgt
            if (
                len(fb.chain) == 2
                and fb.chain[0][0] == src_n
                and fb.chain[0][1] == "en"
                and fb.chain[1][1] == tgt_n
            ):
                fb = TranslationRoute("via_en", fb.chain)
            routes.append(fb)

    direct = routes[0]
    pivots = sorted(routes[1:], key=lambda r: _route_historical_score(app_dir, r), reverse=True)

    # CJK → Cyrillic/Latin: prefer English pivot right after direct
    if src_n in ("zh", "ja", "ko"):
        via_en = [r for r in pivots if r.name == "via_en" or (
            len(r.chain) == 2 and r.chain[0][1] == "en"
        )]
        rest = [r for r in pivots if r not in via_en]
        pivots = via_en + rest

    return [direct] + pivots


def score_mt_output(
    original: str,
    translated: str,
    *,
    src_lang: str,
    tgt_lang: str,
) -> tuple[float, dict[str, Any]]:
    return compute_quality_score(
        original, translated, src_lang=src_lang, tgt_lang=tgt_lang
    )


def load_fallback_routes(app_dir: Path) -> dict[str, list[list[str]]]:
    path = app_dir / "data" / "mt_fallback_routes.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        pairs = data.get("pairs") if isinstance(data, dict) else {}
        return pairs if isinstance(pairs, dict) else {}
    except Exception:
        return {}


def fallback_route_for_pair(app_dir: Path, src: str, tgt: str) -> TranslationRoute | None:
    """Optional reserve route from config — only used on hard direct failure."""
    chain_raw = load_fallback_routes(app_dir).get(_pair_key(src, tgt))
    if not chain_raw or not isinstance(chain_raw, list):
        return None
    chain: list[tuple[str, str]] = []
    for leg in chain_raw:
        if isinstance(leg, (list, tuple)) and len(leg) == 2:
            chain.append((_norm(str(leg[0])), _norm(str(leg[1]))))
        elif isinstance(leg, str) and "->" in leg:
            a, b = leg.split("->", 1)
            chain.append((_norm(a), _norm(b)))
    if not chain:
        return None
    return TranslationRoute("fallback", chain)


def _translate_leg(
    text: str,
    src: str,
    tgt: str,
    app_dir: Path,
    *,
    segment_index: int = -1,
) -> tuple[str, dict[str, Any]]:
    """Single src→tgt leg — primary engine (+ one fallback inside MT layer)."""
    from engines.mt.registry import translate_with_best_engine

    t0 = time.perf_counter()
    result_text, eng_meta = translate_with_best_engine(
        text, src, tgt, app_dir=app_dir, segment_index=segment_index
    )
    total_ms = (time.perf_counter() - t0) * 1000.0
    leg_meta = {
        "engine": eng_meta.get("engine"),
        "engine_version": eng_meta.get("engine_version"),
        "quality_score": eng_meta.get("quality_score", 0.0),
        "quality_details": eng_meta.get("quality_details") or {},
        "engines_tried": eng_meta.get("engines_tried") or [],
        "router_reason": eng_meta.get("router_reason", ""),
        "src": src,
        "tgt": tgt,
        "total_ms": round(total_ms, 1),
    }
    return result_text, leg_meta


def _execute_route(
    text: str,
    route: TranslationRoute,
    app_dir: Path,
    *,
    segment_index: int = -1,
) -> tuple[str, dict[str, Any]]:
    """Run route chain (direct or configured fallback)."""
    current = text
    legs: list[dict[str, Any]] = []
    total_ms = 0.0

    for src, tgt in route.chain:
        current, leg_meta = _translate_leg(current, src, tgt, app_dir, segment_index=segment_index)
        total_ms += float(leg_meta.get("total_ms", 0))
        legs.append(leg_meta)
        if not current or not str(current).strip():
            break

    final_engine = legs[-1].get("engine", "") if legs else ""
    return current, {
        "legs": legs,
        "route": route.label,
        "route_name": route.name,
        "engine": final_engine,
        "pivot": route.chain[0][1] if not route.is_direct and len(route.chain) > 1 else None,
        "direct": route.is_direct,
        "total_ms": round(total_ms, 1),
    }


def translate_with_router(
    text: str,
    src_lang: str,
    tgt_lang: str,
    *,
    app_dir: Path | None = None,
    context: str | None = None,
    next_context: str | None = None,
    segment_index: int = -1,
) -> tuple[str, dict[str, Any]]:
    """
    Minimal Router (stable):
    1) direct route + primary MT engine
    2) optional configured fallback route only on hard failure (empty/error)
    No quality-score route switching during dubbing.
    """
    app_dir = app_dir or Path(__file__).resolve().parent.parent
    src = _norm(src_lang)
    tgt = _norm(tgt_lang)

    meta: dict[str, Any] = {
        "src": src,
        "tgt": tgt,
        "engine": None,
        "pivot": None,
        "direct": True,
        "route": "direct",
        "route_label": f"{src}→{tgt}",
        "router_reason": "",
        "context_used": bool(context and str(context).strip()),
        "next_context_used": bool(next_context and str(next_context).strip()),
        "quality_score": 0.0,
        "quality_details": {},
        "mt_retries": 0,
        "engines_tried": [],
        "routes_tried": [f"{src}→{tgt}"],
        "router_version": ROUTER_VERSION,
        "router": True,
    }

    if not text or not text.strip():
        return text, meta
    if src == tgt:
        meta["engine"] = "none"
        meta["quality_score"] = 100.0
        return text, meta

    direct = TranslationRoute("direct", [(src, tgt)])
    result, route_meta = _execute_route(text, direct, app_dir, segment_index=segment_index)

    if result and str(result).strip():
        meta.update(_finalize_meta(meta, text, result, route_meta, src, tgt))
        return result, meta

    fb = fallback_route_for_pair(app_dir, src, tgt)
    if fb:
        meta["routes_tried"].append(fb.label)
        meta["mt_retries"] = 1
        result, route_meta = _execute_route(text, fb, app_dir, segment_index=segment_index)
        if result and str(result).strip():
            meta["router_reason"] = f"fallback_route={fb.label}"
            meta.update(_finalize_meta(meta, text, result, route_meta, src, tgt))
            meta["direct"] = False
            meta["route"] = "fallback"
            meta["route_label"] = fb.label
            meta["pivot"] = route_meta.get("pivot")
            return result, meta

    meta["engine"] = "failed"
    meta["quality_score"] = 0.0
    meta["router_reason"] = "direct_failed"
    return text, meta


def _finalize_meta(
    base: dict[str, Any],
    original: str,
    translated: str,
    route_meta: dict[str, Any],
    src: str,
    tgt: str,
) -> dict[str, Any]:
    score, metrics = compute_quality_score(original, translated, src_lang=src, tgt_lang=tgt)
    engines_tried: list[str] = []
    for leg in route_meta.get("legs") or []:
        engines_tried.extend(leg.get("engines_tried") or [])
    return {
        "engine": route_meta.get("engine"),
        "pivot": route_meta.get("pivot"),
        "direct": route_meta.get("direct", True),
        "route_label": route_meta.get("route", f"{src}→{tgt}"),
        "quality_score": round(score, 2),
        "quality_details": metrics,
        "engines_tried": engines_tried,
        "router_reason": base.get("router_reason") or route_meta.get("legs", [{}])[-1].get("router_reason", ""),
    }
