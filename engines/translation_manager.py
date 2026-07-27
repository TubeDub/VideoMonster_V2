"""
Translation Manager — restored multi-route / multi-engine selection.

Uses existing router, registry, and quality score modules.
Does not replace Naturalizer or pipeline — only the MT step inside translate_text_traced.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.engines.translation_manager")

MANAGER_VERSION = 1
MAX_ROUTES = int(os.getenv("VM_TM_MAX_ROUTES", "3") or "3")
MAX_ENGINES_PER_ROUTE = int(os.getenv("VM_TM_MAX_ENGINES", "2") or "2")


def use_translation_manager() -> bool:
    """
    Restored smart translation path (default ON).
    Set VM_STABLE_MT_ONLY=1 for legacy Marian-only dubbing.
    Set VM_TRANSLATION_MANAGER=0 to disable explicitly.
    """
    if os.getenv("VM_STABLE_MT_ONLY", "").strip().lower() in ("1", "true", "yes", "on"):
        return False
    v = (os.getenv("VM_TRANSLATION_MANAGER") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


@dataclass
class TranslationCandidate:
    text: str
    score: float
    engine: str
    route_label: str
    route_name: str
    pivot: str | None
    direct: bool
    elapsed_ms: float
    quality_details: dict[str, Any] = field(default_factory=dict)
    engines_tried: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


def ensure_manager_ready(app_dir: Path, src_lang: str, tgt_lang: str) -> None:
    """Preload primary offline engine for the pair (usually Marian)."""
    from engines.mt.registry import ordered_engines_for_pair

    src = (src_lang or "en").split("-")[0].lower()
    tgt = (tgt_lang or "uk").split("-")[0].lower()
    if src == tgt:
        return
    engines = ordered_engines_for_pair(app_dir, src, tgt)
    for eng in engines[:1]:
        if eng.id == "marian":
            from engines.mt.stable_translate import ensure_marian_ready

            ensure_marian_ready(app_dir, src, tgt)
            return
        try:
            eng.translate("ok", src, tgt)
        except Exception:
            pass
        return


def _translate_route_with_engines(
    text: str,
    route,
    app_dir: Path,
    *,
    segment_index: int = -1,
    score_source: str | None = None,
    max_engines: int | None = None,
) -> list[TranslationCandidate]:
    """Run a route; for each leg try top-N engines and keep best leg scores."""
    from engines.mt.registry import get_engine_by_id, ordered_engines_for_pair
    from engines.translation_quality_score import compute_quality_score
    from engines.translation_router import TranslationRoute

    if not isinstance(route, TranslationRoute):
        return []

    src0, tgt0 = route.chain[0]
    candidates: list[TranslationCandidate] = []
    q_src = (score_source or text).strip()
    eng_limit = max_engines if max_engines is not None else MAX_ENGINES_PER_ROUTE

    if route.is_direct:
        engines = ordered_engines_for_pair(app_dir, src0, tgt0)[:eng_limit]
        for eng in engines:
            t0 = time.perf_counter()
            try:
                result = eng.translate(text, src0, tgt0)
            except Exception as exc:
                logger.debug("[TM] %s failed: %s", eng.id, exc)
                continue
            ms = (time.perf_counter() - t0) * 1000.0
            if not str(result.text or "").strip():
                continue
            from engines.placeholder_guard import has_mt_garbage

            if has_mt_garbage(result.text):
                score, qd = 0.0, {"placeholder_leak_count": 99, "placeholder_leaks": ["garbage"]}
            else:
                score, qd = compute_quality_score(
                    q_src, result.text, src_lang=src0, tgt_lang=tgt0
                )
            candidates.append(
                TranslationCandidate(
                    text=result.text.strip(),
                    score=score,
                    engine=result.engine_id,
                    route_label=route.label,
                    route_name=route.name,
                    pivot=None,
                    direct=True,
                    elapsed_ms=ms,
                    quality_details=qd,
                    engines_tried=[eng.id],
                    meta={"engine_version": result.engine_version},
                )
            )
        return candidates

    # Pivot route — execute full chain via router helper
    from engines.translation_router import _execute_route

    t0 = time.perf_counter()
    try:
        out, route_meta = _execute_route(
            text, route, app_dir, segment_index=segment_index
        )
    except Exception as exc:
        logger.debug("[TM] pivot route %s failed: %s", route.label, exc)
        return []
    ms = (time.perf_counter() - t0) * 1000.0
    if not str(out or "").strip():
        return []

    src_chain = route.chain[0][0]
    tgt_chain = route.chain[-1][1]
    from engines.placeholder_guard import has_placeholder_leak

    score, qd = compute_quality_score(
        q_src, out, src_lang=src_chain, tgt_lang=tgt_chain
    )
    if has_placeholder_leak(out):
        score, qd = 0.0, {**qd, "placeholder_leak_count": 99}
    engines_tried: list[str] = []
    for leg in route_meta.get("legs") or []:
        engines_tried.extend(leg.get("engines_tried") or [])
        if leg.get("engine"):
            engines_tried.append(str(leg["engine"]))
    candidates.append(
        TranslationCandidate(
            text=str(out).strip(),
            score=score,
            engine=str(route_meta.get("engine") or "pivot"),
            route_label=route.label,
            route_name=route.name,
            pivot=route_meta.get("pivot"),
            direct=False,
            elapsed_ms=ms,
            quality_details=qd,
            engines_tried=sorted(set(engines_tried)),
            meta=dict(route_meta),
        )
    )
    return candidates


def _placeholder_penalty(source: str, translated: str) -> float:
    from engines.placeholder_guard import detect_placeholder_leaks, has_cjk_garbage

    leaks = detect_placeholder_leaks(translated)
    if leaks or has_cjk_garbage(translated):
        return 100.0
    return 0.0


def _marian_fallback(
    source_text: str,
    src: str,
    tgt: str,
    app_dir: Path,
    *,
    segment_index: int = -1,
) -> TranslationCandidate | None:
    """Unmasked Marian retry when tournament outputs are unusable."""
    from engines.mt.registry import get_engine_by_id
    from engines.placeholder_guard import has_mt_garbage
    from engines.translation_quality_score import MIN_ACCEPT_QUALITY, compute_quality_score

    eng = get_engine_by_id("marian")
    if not eng or not eng.is_available() or not eng.supports_pair(src, tgt):
        return None
    plain = " ".join(str(source_text or "").split()).strip()
    if not plain:
        return None
    t0 = time.perf_counter()
    try:
        result = eng.translate(plain, src, tgt)
    except Exception as exc:
        logger.debug("[TM] marian fallback failed: %s", exc)
        return None
    text = str(result.text or "").strip()
    if not text or has_mt_garbage(text):
        return None
    score, qd = compute_quality_score(plain, text, src_lang=src, tgt_lang=tgt)
    if score < MIN_ACCEPT_QUALITY * 0.35:
        return None
    return TranslationCandidate(
        text=text,
        score=score,
        engine=result.engine_id,
        route_label=f"{src}→{tgt}",
        route_name="marian_fallback_unmasked",
        pivot=None,
        direct=True,
        elapsed_ms=(time.perf_counter() - t0) * 1000.0,
        quality_details=qd,
        engines_tried=[eng.id],
        meta={"fallback": True, "segment_index": segment_index},
    )


def _has_target_language_leak(text: str, source: str, tgt: str) -> bool:
    """True when translated text is clearly not in target language (e.g. EN in UK)."""
    from engines.pipeline_language_gate import is_critical_language_mismatch

    bad, _ = is_critical_language_mismatch(
        text, target_lang=tgt, original=source or ""
    )
    return bad


def _name_damage_penalty(source: str, translated: str) -> float:
    """Penalty when proper names get mangled into technical terms during MT."""
    from engines.translation_quality import name_to_tech_term_damage

    hits = name_to_tech_term_damage(source, translated)
    return min(40.0, len(hits) * 15.0)


def _rank_candidates(
    source: str,
    candidates: list[TranslationCandidate],
) -> list[TranslationCandidate]:
    def key(c: TranslationCandidate) -> float:
        return (
            c.score
            - _name_damage_penalty(source, c.text)
            - _placeholder_penalty(source, c.text)
        )

    return sorted(candidates, key=key, reverse=True)


def translate_with_manager(
    text: str,
    src_lang: str,
    tgt_lang: str,
    *,
    app_dir: Path | None = None,
    context: str | None = None,
    next_context: str | None = None,
    segment_index: int = -1,
    source_original: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """
    Compare routes and engines; pick best natural output silently.
    """
    from engines.translation_router import candidate_routes, record_engine_result
    from engines.translation_quality import name_to_tech_term_damage

    base = app_dir or Path(__file__).resolve().parent.parent
    src = (src_lang or "en").split("-")[0].lower()
    tgt = (tgt_lang or "uk").split("-")[0].lower()

    meta: dict[str, Any] = {
        "engine": None,
        "route": "direct",
        "route_label": f"{src}→{tgt}",
        "direct": True,
        "pivot": None,
        "router": True,
        "translation_manager": True,
        "manager_version": MANAGER_VERSION,
        "context_used": bool(context and str(context).strip()),
        "next_context_used": bool(next_context and str(next_context).strip()),
        "quality_score": 0.0,
        "quality_details": {},
        "engines_tried": [],
        "routes_tried": [],
        "mt_retries": 0,
        "router_reason": "",
        "alternative_translation": "",
        "alternative_route": "",
        "alternative_engine": "",
        "alternative_score": 0.0,
        "candidates_count": 0,
        "segment_index": segment_index,
    }

    clean = " ".join(str(text or "").split()).strip()
    score_source = " ".join(str(source_original or text or "").split()).strip()
    if not clean:
        return text, meta
    if src == tgt:
        meta["engine"] = "none"
        meta["quality_score"] = 100.0
        return text, meta

    routes = candidate_routes(src, tgt, base)[: max(1, MAX_ROUTES)]
    # CJK sources need deep + pivot in the tournament
    if src in ("zh", "ja", "ko"):
        routes = candidate_routes(src, tgt, base)[: max(MAX_ROUTES, 4)]
    all_candidates: list[TranslationCandidate] = []

    _max_eng = MAX_ENGINES_PER_ROUTE
    if src in ("zh", "ja", "ko"):
        _max_eng = max(_max_eng, 3)

    for route in routes:
        meta["routes_tried"].append(route.label)
        for cand in _translate_route_with_engines(
            clean,
            route,
            base,
            segment_index=segment_index,
            score_source=score_source,
            max_engines=_max_eng,
        ):
            all_candidates.extend([cand])
            meta["engines_tried"].extend(cand.engines_tried)

    # Online fallback if no offline result
    if not all_candidates:
        from engines.mt.registry import get_engine_by_id

        eng = get_engine_by_id("deep")
        if eng:
            try:
                if eng.is_available() and eng.supports_pair(src, tgt):
                    t0 = time.perf_counter()
                    result = eng.translate(clean, src, tgt)
                    ms = (time.perf_counter() - t0) * 1000.0
                    if str(result.text or "").strip():
                        from engines.translation_quality_score import compute_quality_score

                        from engines.placeholder_guard import has_mt_garbage

                        if has_mt_garbage(result.text):
                            score, qd = 0.0, {"placeholder_leak_count": 99}
                        else:
                            score, qd = compute_quality_score(
                                score_source, result.text, src_lang=src, tgt_lang=tgt
                            )
                        all_candidates.append(
                            TranslationCandidate(
                                text=result.text.strip(),
                                score=score,
                                engine=result.engine_id,
                                route_label=f"{src}→{tgt}",
                                route_name="online_fallback",
                                pivot=None,
                                direct=True,
                                elapsed_ms=ms,
                                quality_details=qd,
                                engines_tried=[eng.id],
                            )
                        )
            except Exception as exc:
                logger.debug("[TM] online deep skipped: %s", exc)

    meta["engines_tried"] = sorted(set(meta["engines_tried"]))
    meta["candidates_count"] = len(all_candidates)

    if not all_candidates:
        meta["engine"] = "failed"
        meta["router_reason"] = "all_routes_failed"
        return text, meta

    from engines.placeholder_guard import has_mt_garbage

    clean_candidates = [c for c in all_candidates if not has_mt_garbage(c.text)]
    if not clean_candidates and score_source:
        fb = _marian_fallback(score_source, src, tgt, base, segment_index=segment_index)
        if fb:
            meta["mt_retries"] = int(meta.get("mt_retries", 0)) + 1
            meta["engines_tried"].append(fb.engine)
            meta.update(
                {
                    "engine": fb.engine,
                    "route": fb.route_name,
                    "route_label": fb.route_label,
                    "quality_score": round(fb.score, 2),
                    "quality_details": fb.quality_details,
                    "unmasked_fallback": True,
                    "router_reason": f"marian_fallback score={fb.score:.1f}",
                }
            )
            return fb.text, meta
        logger.warning("[TM] all candidates garbage, marian fallback failed seg=%s", segment_index)
    elif clean_candidates:
        all_candidates = clean_candidates
    else:
        meta["mt_retries"] = int(meta.get("mt_retries", 0)) + 1
        logger.warning("[TM] all candidates have garbage seg=%s", segment_index)

    ranked = _rank_candidates(score_source, all_candidates)
    best = ranked[0]
    alt = ranked[1] if len(ranked) > 1 else None

    def _is_collapse(cand: TranslationCandidate | None) -> bool:
        if cand is None:
            return True
        qd = cand.quality_details or {}
        if qd.get("meaning_collapse") or qd.get("cjk_meaning_collapse"):
            return True
        try:
            from engines.mt.cross_script_guard import is_meta_waffle, meaning_collapse

            if is_meta_waffle(cand.text):
                return True
            if score_source and meaning_collapse(
                score_source, cand.text, source_lang=src, target_lang=tgt
            ):
                return True
        except Exception:
            pass
        return False

    # Prefer first non-collapse candidate (deep/NLLB over Argos flower)
    if _is_collapse(best):
        for cand in ranked[1:]:
            if not _is_collapse(cand):
                meta["mt_retries"] = int(meta.get("mt_retries", 0)) + 1
                logger.info(
                    "[TM] skip collapse eng=%s → eng=%s seg=%s",
                    best.engine,
                    cand.engine,
                    segment_index,
                )
                best = cand
                break

    from engines.translation_quality_score import MIN_ACCEPT_QUALITY

    if score_source and (best.score < MIN_ACCEPT_QUALITY or has_mt_garbage(best.text)):
        fb = _marian_fallback(score_source, src, tgt, base, segment_index=segment_index)
        if fb and not _is_collapse(fb) and (fb.score > best.score or has_mt_garbage(best.text)):
            meta["mt_retries"] = int(meta.get("mt_retries", 0)) + 1
            logger.info("[TM] marian fallback seg=%s score=%.1f", segment_index, fb.score)
            best = fb

    if has_mt_garbage(best.text) and alt and not has_mt_garbage(alt.text):
        meta["mt_retries"] = int(meta.get("mt_retries", 0)) + 1
        logger.info("[TM] placeholder leak — using alternative seg=%s", segment_index)
        best = alt
        alt = ranked[2] if len(ranked) > 2 else None

    if name_to_tech_term_damage(score_source, best.text) and alt:
        meta["mt_retries"] = 1
        logger.info(
            "[TM] name damage on best — using alternative seg=%s",
            segment_index,
        )
        best = alt
        alt = ranked[2] if len(ranked) > 2 else None

    if has_mt_garbage(best.text) and score_source:
        fb = _marian_fallback(score_source, src, tgt, base, segment_index=segment_index)
        if fb and not _is_collapse(fb):
            meta["mt_retries"] = int(meta.get("mt_retries", 0)) + 1
            logger.info("[TM] last-resort marian seg=%s", segment_index)
            best = fb

    if score_source and _has_target_language_leak(best.text, score_source, tgt):
        meta["language_leak"] = True
        fb = _marian_fallback(score_source, src, tgt, base, segment_index=segment_index)
        if fb and not _has_target_language_leak(fb.text, score_source, tgt):
            meta["mt_retries"] = int(meta.get("mt_retries", 0)) + 1
            meta["language_leak_retry"] = "marian_fallback"
            logger.info(
                "[TM] language leak retry marian seg=%s",
                segment_index,
            )
            best = fb
        else:
            logger.warning(
                "[TM] target language leak persists seg=%s text=%r",
                segment_index,
                best.text[:80],
            )
            meta["language_leak_code"] = "english_in_target_track"

    # Final: never ship flower waffle; allow partial non-waffle MT for long ASR
    if _is_collapse(best):
        from engines.mt.registry import get_engine_by_id
        from engines.mt.cross_script_guard import is_meta_waffle

        deep = get_engine_by_id("deep")
        if deep and deep.supports_pair(src, tgt):
            try:
                dr = deep.translate(clean, src, tgt)
                if str(dr.text or "").strip() and not is_meta_waffle(dr.text):
                    from engines.translation_quality_score import compute_quality_score

                    ds, dqd = compute_quality_score(
                        score_source, dr.text, src_lang=src, tgt_lang=tgt
                    )
                    deep_cand = TranslationCandidate(
                        text=dr.text.strip(),
                        score=ds,
                        engine="deep",
                        route_label=f"{src}→{tgt}",
                        route_name="deep_collapse_rescue",
                        pivot=None,
                        direct=True,
                        elapsed_ms=float(dr.elapsed_ms or 0),
                        quality_details=dqd,
                        engines_tried=["deep"],
                    )
                    # Accept deep rescue if no waffle even when secondary cues missing
                    if not is_meta_waffle(deep_cand.text):
                        meta["mt_retries"] = int(meta.get("mt_retries", 0)) + 1
                        meta["collapse_rescue"] = "deep"
                        best = deep_cand
            except Exception as exc:
                logger.debug("[TM] deep collapse rescue failed: %s", exc)

        # CJK→uk/ru: LLM direct from source when offline/deep still collapsed
        if _is_collapse(best):
            try:
                from engines.mt.zh_drama_gloss import try_offline_gloss_rescue

                gloss = try_offline_gloss_rescue(
                    clean or score_source,
                    best.text if best else "",
                    src_lang=src,
                    tgt_lang=tgt,
                )
                if gloss and str(gloss.get("text") or "").strip():
                    gtext = str(gloss["text"]).strip()
                    if not _is_collapse(
                        TranslationCandidate(
                            text=gtext,
                            score=0.0,
                            engine="gloss",
                            route_label=f"{src}→{tgt}",
                            route_name="offline_gloss_rescue",
                            pivot=None,
                            direct=True,
                            elapsed_ms=0.0,
                        )
                    ):
                        meta["mt_retries"] = int(meta.get("mt_retries", 0)) + 1
                        meta["collapse_rescue"] = f"offline_gloss:{gloss.get('method')}"
                        best = TranslationCandidate(
                            text=gtext,
                            score=85.0,
                            engine="gloss",
                            route_label=f"{src}→{tgt}",
                            route_name="offline_gloss_rescue",
                            pivot=None,
                            direct=True,
                            elapsed_ms=0.0,
                            quality_details={"offline_gloss": True},
                            engines_tried=list((best.engines_tried if best else []) or [])
                            + ["gloss"],
                        )
            except Exception as exc:
                logger.debug("[TM] offline gloss rescue failed: %s", exc)

        if _is_collapse(best):
            try:
                from engines.mt.llm_retranslate import (
                    llm_direct_translate,
                    should_llm_retranslate,
                )

                if should_llm_retranslate(src_lang=src, tgt_lang=tgt):
                    llm_text = llm_direct_translate(
                        clean or score_source,
                        src_lang=src,
                        tgt_lang=tgt,
                        segment_idx=segment_index if segment_index >= 0 else None,
                    )
                    if llm_text and not _is_collapse(
                        TranslationCandidate(
                            text=llm_text,
                            score=0.0,
                            engine="llm",
                            route_label=f"{src}→{tgt}",
                            route_name="llm_collapse_rescue",
                            pivot=None,
                            direct=True,
                            elapsed_ms=0.0,
                        )
                    ):
                        from engines.translation_quality_score import compute_quality_score

                        ls, lqd = compute_quality_score(
                            score_source, llm_text, src_lang=src, tgt_lang=tgt
                        )
                        meta["mt_retries"] = int(meta.get("mt_retries", 0)) + 1
                        meta["collapse_rescue"] = "llm_direct"
                        best = TranslationCandidate(
                            text=llm_text,
                            score=ls,
                            engine="llm",
                            route_label=f"{src}→{tgt}",
                            route_name="llm_collapse_rescue",
                            pivot=None,
                            direct=True,
                            elapsed_ms=0.0,
                            quality_details=lqd,
                            engines_tried=list(best.engines_tried or []) + ["llm"],
                        )
            except Exception as exc:
                logger.debug("[TM] llm collapse rescue failed: %s", exc)

        if is_meta_waffle(best.text) or (
            (best.quality_details or {}).get("meaning_collapse")
            and is_meta_waffle(best.text)
        ):
            meta["meaning_collapse"] = True
            meta["engine"] = best.engine
            meta["quality_score"] = 0.0
            meta["quality_details"] = {
                **(best.quality_details or {}),
                "meaning_collapse": True,
            }
            meta["router_reason"] = "meta_waffle_rejected"
            logger.warning(
                "[TM] rejecting waffle MT seg=%s eng=%s",
                segment_index,
                best.engine,
            )
            return "", meta

    if alt:
        meta["alternative_translation"] = alt.text
        meta["alternative_route"] = alt.route_label
        meta["alternative_engine"] = alt.engine
        meta["alternative_score"] = round(alt.score, 2)

    meta.update(
        {
            "engine": best.engine,
            "route": best.route_name,
            "route_label": best.route_label,
            "direct": best.direct,
            "pivot": best.pivot,
            "quality_score": round(best.score, 2),
            "quality_details": best.quality_details,
            "elapsed_ms": round(best.elapsed_ms, 1),
            "router_reason": (
                f"manager_best route={best.route_label} engine={best.engine} "
                f"score={best.score:.1f}"
            ),
            "unmasked_fallback": bool(best.meta.get("fallback")),
        }
    )
    if best.meta.get("engine_version"):
        meta["engine_version"] = best.meta["engine_version"]

    try:
        record_engine_result(
            base,
            src_lang=src,
            tgt_lang=tgt,
            engine=str(best.engine),
            quality_score=float(best.score),
            success=True,
            retries=int(meta["mt_retries"]),
            route_label=str(best.route_label),
        )
    except Exception:
        pass

    return best.text, meta
