"""MT engine registry — discovery, ranking, cascade engine selection."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from engines.mt.argos_engine import ArgosEngine
from engines.mt.base import BaseMTEngine, MTResult
from engines.mt.deep_engine import DeepTranslatorEngine
from engines.mt.lang_codes import normalize_lang, pair_key
from engines.mt.marian_engine import MarianEngine
from engines.mt.nllb_engine import NLLBEngine
from engines.translation_quality_score import compute_quality_score

logger = logging.getLogger("tubedub.engines.mt.registry")

MT_ROUTER_VERSION = 5
RANKINGS_FILE = "mt_pair_rankings.json"
BENCHMARK_FILE = "mt_benchmark_report.json"

# Set MT_CHALLENGE_MODE=1 + VM_DEV_MODE=1 for multi-engine benchmark (dev only).
_CHALLENGE_MODE = (
    os.environ.get("MT_CHALLENGE_MODE", "").strip() in ("1", "true", "yes")
    and os.environ.get("VM_DEV_MODE", "").strip().lower() in ("1", "true", "yes")
)

_ALL_ENGINE_CLASSES: list[type[BaseMTEngine]] = [
    MarianEngine,
    NLLBEngine,
    ArgosEngine,
    DeepTranslatorEngine,
]


def get_registry() -> list[BaseMTEngine]:
    """All registered engine instances that pass is_available()."""
    out: list[BaseMTEngine] = []
    for cls in _ALL_ENGINE_CLASSES:
        eng = cls()
        if eng.is_available():
            out.append(eng)
    return out


def _rankings_path(app_dir: Path) -> Path:
    return app_dir / "data" / RANKINGS_FILE


def load_pair_rankings(app_dir: Path) -> dict[str, list[str]]:
    path = _rankings_path(app_dir)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        pairs = data.get("pairs") if isinstance(data, dict) else {}
        return pairs if isinstance(pairs, dict) else {}
    except Exception:
        return {}


def save_pair_rankings(app_dir: Path, pairs: dict[str, list[str]]) -> None:
    path = _rankings_path(app_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": 1, "pairs": pairs}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _runtime_engine_score(app_dir: Path, pair: str, engine_id: str) -> float:
    from engines.translation_router import load_engine_stats

    stats = load_engine_stats(app_dir)
    eng = (stats.get("pairs") or {}).get(pair, {}).get(engine_id, {})
    attempts = int(eng.get("attempts", 0))
    if attempts <= 0:
        return -1.0
    total_q = float(eng.get("total_quality", 0.0))
    successes = int(eng.get("successes", 0))
    manual = int(eng.get("manual_corrections", 0))
    avg = total_q / attempts
    return avg * 0.7 + (successes / attempts) * 30.0 - min(15.0, manual * 2.0)


def ordered_engines_for_pair_planning(
    app_dir: Path,
    src_lang: str,
    tgt_lang: str,
) -> list[BaseMTEngine]:
    """Rank engines for preparation — no supports_pair / network side effects."""
    src, tgt = normalize_lang(src_lang), normalize_lang(tgt_lang)
    pk = pair_key(src, tgt)
    available = [e for cls in _ALL_ENGINE_CLASSES for e in [cls()] if e.is_available()]
    if not available:
        return []

    bench_order = load_pair_rankings(app_dir).get(pk, [])
    scored: list[tuple[float, BaseMTEngine]] = []
    for eng in available:
        rank_bonus = 0.0
        if eng.id in bench_order:
            rank_bonus = (len(bench_order) - bench_order.index(eng.id)) * 5.0
        rt = _runtime_engine_score(app_dir, pk, eng.id)
        rt_score = rt if rt >= 0 else (100 - eng.priority)
        scored.append((rt_score + rank_bonus, eng))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [e for _, e in scored]


def ordered_engines_for_pair(
    app_dir: Path,
    src_lang: str,
    tgt_lang: str,
) -> list[BaseMTEngine]:
    """Rank engines for src→tgt: benchmark file + runtime stats + priority."""
    src, tgt = normalize_lang(src_lang), normalize_lang(tgt_lang)
    pk = pair_key(src, tgt)
    available = [e for e in get_registry() if e.supports_pair(src, tgt)]
    if not available:
        return []

    bench_order = load_pair_rankings(app_dir).get(pk, [])
    cjk_src = src in ("zh", "ja", "ko")
    scored: list[tuple[float, BaseMTEngine]] = []
    for eng in available:
        rank_bonus = 0.0
        if eng.id in bench_order:
            rank_bonus = (len(bench_order) - bench_order.index(eng.id)) * 5.0
        rt = _runtime_engine_score(app_dir, pk, eng.id)
        rt_score = rt if rt >= 0 else (100 - eng.priority)
        # CJK→* : prefer online deep when offline Marian/NLLB missing; demote Argos
        if cjk_src:
            if eng.id == "deep":
                rank_bonus += 25.0
            elif eng.id == "nllb":
                rank_bonus += 15.0
            elif eng.id == "argos":
                # Argos zh→uk/ru invents flower waffle / birth-flips — last resort only
                if tgt in ("uk", "ru", "be"):
                    rank_bonus -= 55.0
                else:
                    rank_bonus -= 20.0
        # en→uk/ru: Argos can emit flower waffle — keep behind deep when Marian absent
        if src == "en" and tgt in ("uk", "ru") and eng.id == "deep":
            rank_bonus += 8.0
        if src == "en" and tgt in ("uk", "ru") and eng.id == "argos":
            rank_bonus -= 12.0
        # Skip Argos entirely for CJK→Cyrillic when deep/nllb/marian present
        if (
            cjk_src
            and tgt in ("uk", "ru", "be")
            and eng.id == "argos"
            and any(e.id in ("deep", "nllb", "marian") for e in available)
        ):
            continue
        scored.append((rt_score + rank_bonus, eng))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [e for _, e in scored]


def engines_for_pair(app_dir: Path, src_lang: str, tgt_lang: str) -> tuple[str, str | None]:
    """Primary + optional single offline fallback from static routing table."""
    src, tgt = normalize_lang(src_lang), normalize_lang(tgt_lang)
    pk = pair_key(src, tgt)
    bench = load_pair_rankings(app_dir).get(pk, ["marian"])
    offline = [e for e in bench if e in ("marian", "argos", "nllb")]
    if not offline:
        offline = ["marian"]
    primary = offline[0]
    fallback = offline[1] if len(offline) > 1 else None
    if fallback == primary:
        fallback = "marian" if primary != "marian" else None
    return primary, fallback


def get_engine_by_id(engine_id: str) -> BaseMTEngine | None:
    for cls in _ALL_ENGINE_CLASSES:
        eng = cls()
        if eng.id == engine_id and eng.is_available():
            return eng
    return None


def _hard_success(result: MTResult) -> bool:
    if result.error:
        return False
    return bool(str(result.text or "").strip())


def translate_with_best_engine(
    text: str,
    src_lang: str,
    tgt_lang: str,
    *,
    app_dir: Path,
    segment_index: int = -1,
) -> tuple[str, dict[str, Any]]:
    """
    Stable production path: one primary engine per pair, one offline fallback on hard failure.
    No quality-score cascade during dubbing. Main-thread only (no ThreadPoolExecutor).
    """
    from engines.mt.translate_guard import is_dev_mode

    src, tgt = normalize_lang(src_lang), normalize_lang(tgt_lang)
    meta: dict[str, Any] = {
        "engine": None,
        "engines_tried": [],
        "quality_score": 0.0,
        "quality_details": {},
        "mt_retries": 0,
        "router_reason": "",
        "mt_router_version": MT_ROUTER_VERSION,
        "cascade_mode": "simple",
    }

    if not text or not str(text).strip():
        return text, meta
    if src == tgt:
        meta["engine"] = "none"
        meta["quality_score"] = 100.0
        return text, meta

    if _CHALLENGE_MODE:
        return _translate_challenge_mode(text, src, tgt, app_dir=app_dir, meta=meta)

    primary_id, fallback_id = engines_for_pair(app_dir, src, tgt)
    try_ids = [primary_id] + ([fallback_id] if fallback_id else [])

    for idx, eng_id in enumerate(try_ids):
        if not eng_id:
            continue
        eng = get_engine_by_id(eng_id)
        if not eng:
            continue
        meta["engines_tried"].append(eng_id)

        try:
            result = eng.translate(text, src, tgt)
        except Exception as exc:
            logger.warning("[MT] %s %s→%s failed: %s", eng_id, src, tgt, exc)
            if idx + 1 >= len(try_ids):
                raise
            meta["mt_retries"] += 1
            continue

        if not _hard_success(result):
            logger.info("[MT] %s %s→%s empty/error — try fallback", eng_id, src, tgt)
            meta["mt_retries"] += 1
            continue

        score, metrics = compute_quality_score(text, result.text, src_lang=src, tgt_lang=tgt)
        metrics["engine_ms"] = result.elapsed_ms
        meta.update(
            {
                "engine": result.engine_id,
                "engine_version": result.engine_version,
                "quality_score": round(score, 2),
                "quality_details": metrics,
                "router_reason": f"primary={eng_id}" if idx == 0 else f"fallback={eng_id}",
            }
        )
        if is_dev_mode():
            from engines.translation_router import record_engine_result

            record_engine_result(
                app_dir,
                src_lang=src,
                tgt_lang=tgt,
                engine=result.engine_id,
                quality_score=score,
                success=True,
                retries=meta["mt_retries"],
            )
        return result.text, meta

    # Offline primary/fallback empty — try online deep unless offline lock / mode
    offline_lock = False
    try:
        from engines.model_manager.runtime import is_offline_only

        offline_lock = bool(is_offline_only())
    except Exception:
        pass
    if (os.getenv("VM_MT_MODE") or os.getenv("VM_DUB_MODE") or "").strip().lower() == "offline":
        offline_lock = True

    if offline_lock:
        meta["engine"] = "failed"
        meta["quality_score"] = 0.0
        meta["router_reason"] = "offline_mode_no_engine"
        meta["error"] = (
            "Offline MT failed: Argos/Marian/NLLB unavailable for this pair. "
            "Prepare language packs before dubbing, or switch mode to online/auto."
        )
        meta["error_ru"] = (
            "Офлайн-перевод недоступен: нет Argos/Marian/NLLB для этой пары. "
            "Подготовьте языковые пакеты до дубляжа или включите online/auto."
        )
        return text, meta

    deep = get_engine_by_id("deep")
    if deep and deep.supports_pair(src, tgt) and "deep" not in meta["engines_tried"]:
        meta["engines_tried"].append("deep")
        try:
            result = deep.translate(text, src, tgt)
        except Exception as exc:
            logger.warning("[MT] deep %s→%s failed: %s", src, tgt, exc)
            result = None
        if result is not None and _hard_success(result):
            try:
                from engines.mt.cross_script_guard import is_meta_waffle

                if is_meta_waffle(result.text):
                    result = None
            except Exception:
                pass
        if result is not None and _hard_success(result):
            score, metrics = compute_quality_score(
                text, result.text, src_lang=src, tgt_lang=tgt
            )
            meta.update(
                {
                    "engine": result.engine_id,
                    "engine_version": result.engine_version,
                    "quality_score": round(score, 2),
                    "quality_details": metrics,
                    "router_reason": "online_deep_fallback",
                    "mt_retries": int(meta.get("mt_retries", 0)) + 1,
                }
            )
            return result.text, meta

    meta["engine"] = "failed"
    meta["quality_score"] = 0.0
    return text, meta


def _translate_challenge_mode(
    text: str,
    src: str,
    tgt: str,
    *,
    app_dir: Path,
    meta: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Dev-only: try all offline engines, pick best QE score."""
    from engines.translation_quality_score import MIN_ACCEPT_QUALITY

    pk = pair_key(src, tgt)
    engines = ordered_engines_for_pair_planning(app_dir, src, tgt)
    engines = [e for e in engines if e.offline]
    best_text = ""
    best_score = -1.0
    best_result: MTResult | None = None
    best_metrics: dict[str, Any] = {}

    for eng in engines:
        meta["engines_tried"].append(eng.id)
        try:
            result = eng.translate(text, src, tgt)
        except Exception as e:
            logger.warning("[MT/dev] %s failed: %s", eng.id, e)
            continue
        if not _hard_success(result):
            continue
        score, metrics = compute_quality_score(text, result.text, src_lang=src, tgt_lang=tgt)
        if score > best_score:
            best_score = score
            best_text = result.text
            best_result = result
            best_metrics = metrics

    if not best_result:
        meta["engine"] = "failed"
        return text, meta

    meta.update(
        {
            "engine": best_result.engine_id,
            "engine_version": best_result.engine_version,
            "quality_score": round(best_score, 2),
            "quality_details": best_metrics,
            "router_reason": f"challenge_best score={best_score:.1f}",
            "cascade_mode": "challenge",
        }
    )
    return best_text, meta
