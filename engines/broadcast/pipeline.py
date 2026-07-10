"""Broadcast-grade segment translation pipeline."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from pathlib import Path
from typing import Any

from engines.broadcast.config import (
    block_on_corruption,
    tournament_max_engines,
    use_broadcast_pipeline,
)
from engines.broadcast.exceptions import DataCorruptionException, SegmentFailedException
from engines.broadcast.gatekeeper import PipelineGateKeeper
from engines.broadcast.masking import mask_text, populate_termbase_from_text
from engines.broadcast.report import build_broadcast_report
from engines.broadcast.smart_restore import SmartRestore
from engines.broadcast.termbase import Termbase

logger = logging.getLogger("tubedub.broadcast.pipeline")

_ENGINE_TIMEOUT = 45.0


def translate_segment_broadcast(
    text: str,
    src_lang: str,
    tgt_lang: str,
    *,
    app_dir: Path,
    segment_index: int = -1,
    engine_ids: list[str] | None = None,
) -> tuple[str, dict[str, Any]]:
    """
    Termbase → Mask → Tournament → Validation Gate → SmartRestore → Final.
    """
    t0 = time.perf_counter()
    src = (src_lang or "en").split("-")[0].lower()
    tgt = (tgt_lang or "uk").split("-")[0].lower()

    termbase = Termbase(app_dir)
    populate_termbase_from_text(text, termbase, app_dir)
    mask = mask_text(text, termbase)

    if not engine_ids:
        from engines.mt.registry import ordered_engines_for_pair

        engine_ids = [e.id for e in ordered_engines_for_pair(app_dir, src, tgt)]

    max_n = tournament_max_engines()
    engine_ids = engine_ids[:max_n]

    smart = SmartRestore(app_dir)
    candidates: list[dict[str, Any]] = []
    gate_fatals: list[dict[str, Any]] = []

    def _translate_one(engine_id: str) -> dict[str, Any]:
        t_eng = time.perf_counter()
        try:
            from engines.mt.registry import get_engine_by_id

            eng = get_engine_by_id(engine_id)
            if not eng:
                raise RuntimeError(f"engine not found: {engine_id}")
            result = eng.translate(mask.masked_text, src, tgt)
            raw = result.text or ""
            if not raw.strip():
                raise RuntimeError(result.error or "empty translation")

            gate = PipelineGateKeeper.validation_gate(
                mask.masked_text,
                raw,
                engine_id=engine_id,
                segment_index=segment_index,
            )
            if gate.get("fatal") and not gate.get("needs_fuzzy_restore"):
                gate_fatals.append(gate)
                return {
                    "engine": engine_id,
                    "text": raw,
                    "score": 0.0,
                    "fatal": True,
                    "gate": gate,
                    "elapsed_ms": (time.perf_counter() - t_eng) * 1000,
                }

            restored, incidents = smart.restore_tokens_in_text(
                raw,
                termbase,
                engine=engine_id,
                segment_index=segment_index,
                original_masked=mask.masked_text,
            )
            unrecoverable = [i for i in incidents if i.get("failed")]
            if unrecoverable and block_on_corruption():
                gate_fatals.append(
                    {
                        "fatal": True,
                        "engine": engine_id,
                        "error": f"unrecoverable tokens: {unrecoverable}",
                    }
                )
                return {
                    "engine": engine_id,
                    "text": restored,
                    "score": 0.0,
                    "fatal": True,
                    "incidents": incidents,
                    "elapsed_ms": (time.perf_counter() - t_eng) * 1000,
                }

            # Score: token survival + length heuristic
            try:
                PipelineGateKeeper.assert_integrity(
                    mask.masked_text,
                    raw,
                    stage="post_restore_check",
                    engine=engine_id,
                    allow_fuzzy=False,
                )
                token_ok = True
            except DataCorruptionException:
                token_ok = bool(incidents) and not unrecoverable

            score = 85.0 if token_ok else 0.0
            if incidents and not unrecoverable:
                score = max(50.0, score - len(incidents) * 5)

            return {
                "engine": engine_id,
                "text": restored,
                "raw_mt": raw,
                "score": score,
                "fatal": False,
                "incidents": incidents,
                "gate": gate,
                "elapsed_ms": (time.perf_counter() - t_eng) * 1000,
            }
        except Exception as exc:
            return {
                "engine": engine_id,
                "text": "",
                "score": 0.0,
                "fatal": True,
                "error": str(exc),
                "elapsed_ms": (time.perf_counter() - t_eng) * 1000,
            }

    with ThreadPoolExecutor(max_workers=min(len(engine_ids), max_n)) as pool:
        futures = {pool.submit(_translate_one, eid): eid for eid in engine_ids}
        for fut in futures:
            try:
                candidates.append(fut.result(timeout=_ENGINE_TIMEOUT))
            except FuturesTimeout:
                eid = futures[fut]
                candidates.append(
                    {
                        "engine": eid,
                        "text": "",
                        "score": 0.0,
                        "fatal": True,
                        "error": "timeout",
                    }
                )

    valid = [c for c in candidates if not c.get("fatal") and c.get("score", 0) > 0]
    if not valid:
        if block_on_corruption():
            raise SegmentFailedException(
                "Broadcast: no engine passed validation gate",
                segment_index=segment_index,
                stage="validation_gate",
            )
        valid = candidates

    winner = max(valid, key=lambda c: c.get("score", 0))
    final_text = str(winner.get("text") or text)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    termbase.save()
    meta: dict[str, Any] = {
        "engine": winner.get("engine"),
        "route_label": "broadcast_tournament",
        "quality_score": winner.get("score"),
        "broadcast": True,
        "broadcast_restored": True,
        "broadcast_version": 1,
        "tournament_engines": [c.get("engine") for c in candidates],
        "tournament_scores": {c.get("engine", ""): c.get("score") for c in candidates},
        "winner_engine": winner.get("engine"),
        "fusion_reason": f"broadcast_gate winner={winner.get('engine')} score={winner.get('score')}",
        "restore_incidents": smart.incidents,
        "gate_fatals": gate_fatals,
        "elapsed_ms": round(elapsed_ms, 1),
        "translation_path": "broadcast",
    }

    seg_report = build_broadcast_report(
        task_id=f"seg_{segment_index}",
        segments=[
            {
                "segment_index": segment_index,
                "quality_score": winner.get("score"),
                "restore_incidents": smart.incidents,
                "gate_fatals": gate_fatals,
                "failed": winner.get("fatal"),
            }
        ],
        app_dir=app_dir,
    )
    meta["broadcast_report"] = seg_report

    return final_text, meta


__all__ = ["translate_segment_broadcast", "use_broadcast_pipeline"]
