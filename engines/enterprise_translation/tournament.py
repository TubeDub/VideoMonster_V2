"""Translation Tournament — async multi-engine parallel translate."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from pathlib import Path
from typing import Callable

from engines.enterprise_translation.benchmark import run_engine_benchmark
from engines.enterprise_translation.config import engine_timeout_sec, tournament_max_engines
from engines.enterprise_translation.entity_manager import EntityManager
from engines.enterprise_translation.health_monitor import AutoHealthMonitor
from engines.enterprise_translation.scoring import score_translation
from engines.enterprise_translation.types import TournamentCandidate

logger = logging.getLogger(__name__)

_BENCH_DONE: set[str] = set()


def _ensure_benchmark(
    engine_id: str,
    translate_fn: Callable[[str, str, str], str],
    app_dir: Path | None,
    source_lang: str,
    target_lang: str,
) -> None:
    key = f"{engine_id}:{source_lang}:{target_lang}"
    if key in _BENCH_DONE:
        return
    if app_dir and (app_dir / "data" / "enterprise_serializer_benchmark.json").is_file():
        _BENCH_DONE.add(key)
        return
    try:
        run_engine_benchmark(engine_id, translate_fn, app_dir, source_lang=source_lang, target_lang=target_lang)
    except Exception as exc:
        logger.warning("Benchmark skipped for %s: %s", engine_id, exc)
    _BENCH_DONE.add(key)


def run_tournament(
    text: str,
    *,
    source_lang: str,
    target_lang: str,
    engine_ids: list[str],
    translate_fn: Callable[[str, str, str, str], str],
    entity_manager: EntityManager,
    app_dir: Path | None,
    health: AutoHealthMonitor | None = None,
) -> list[TournamentCandidate]:
    """
    Translate masked text with multiple engines in parallel.
    translate_fn(engine_id, masked_text, source_lang, target_lang) -> raw translation
    """
    max_n = tournament_max_engines()
    if health and app_dir:
        engine_ids = health.ranked_engines(engine_ids)[:max_n]
    else:
        engine_ids = engine_ids[:max_n]

    timeout = engine_timeout_sec()
    candidates: list[TournamentCandidate] = []

    def _one(engine_id: str) -> TournamentCandidate:
        t0 = time.perf_counter()
        try:
            _ensure_benchmark(
                engine_id,
                lambda m, sl, tl: translate_fn(engine_id, m, sl, tl),
                app_dir,
                source_lang,
                target_lang,
            )
            mask = entity_manager.mask_text(text, engine_id)
            raw = translate_fn(engine_id, mask.masked_text, source_lang, target_lang)
            elapsed = (time.perf_counter() - t0) * 1000
            score, details = score_translation(
                mask.masked_text,
                raw,
                registry=entity_manager.registry,
                serializer=entity_manager.serializer,
                engine_id=engine_id,
                expected_tokens=list(mask.token_map.keys()),
            )
            ph_ok = score > 0 and details.get("placeholder", {}).get("ok", False)
            return TournamentCandidate(
                engine_id=engine_id,
                text=raw,
                elapsed_ms=elapsed,
                score=score,
                score_details=details,
                placeholder_ok=ph_ok,
            )
        except Exception as exc:
            elapsed = (time.perf_counter() - t0) * 1000
            return TournamentCandidate(
                engine_id=engine_id,
                text="",
                elapsed_ms=elapsed,
                score=0.0,
                placeholder_ok=False,
                error=str(exc),
            )

    with ThreadPoolExecutor(max_workers=min(len(engine_ids), max_n)) as pool:
        futures = {pool.submit(_one, eid): eid for eid in engine_ids}
        for fut, eid in futures.items():
            try:
                cand = fut.result(timeout=timeout)
            except FuturesTimeout:
                cand = TournamentCandidate(
                    engine_id=eid,
                    text="",
                    elapsed_ms=timeout * 1000,
                    score=0.0,
                    placeholder_ok=False,
                    error="timeout",
                )
            candidates.append(cand)
            if health:
                health.record(
                    eid,
                    score=cand.score,
                    latency_ms=cand.elapsed_ms,
                    placeholder_ok=cand.placeholder_ok,
                    failed=bool(cand.error),
                    grammar=float(cand.score_details.get("grammar", 0)),
                    semantic=float(cand.score_details.get("semantic", 0)),
                )

    return sorted(candidates, key=lambda c: c.score, reverse=True)
