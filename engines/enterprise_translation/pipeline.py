"""Enterprise Translation Pipeline orchestrator."""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

from engines.enterprise_translation.config import architect_mode
from engines.enterprise_translation.dev_log import EnterpriseDevLog
from engines.enterprise_translation.entity_manager import EntityManager
from engines.enterprise_translation.exceptions import IntegrityException
from engines.enterprise_translation.fusion import fuse_candidates
from engines.enterprise_translation.health_monitor import AutoHealthMonitor
from engines.enterprise_translation.tournament import run_tournament
from engines.enterprise_translation.types import EntityType

logger = logging.getLogger("tubedub.enterprise_translation")

# Catalog patterns (same sources as naturalizer entity_tokens)
_CATALOG: list[tuple[str, EntityType]] = [
    ("University of Southern California", EntityType.ORG),
    ("George Lucas", EntityType.PERSON),
    ("George Jr.", EntityType.PERSON),
    ("George Jr", EntityType.PERSON),
    ("Haskell Wexler", EntityType.PERSON),
    ("Star Wars", EntityType.TITLE),
    ("Hollywood", EntityType.PLACE),
    ("Fiat", EntityType.PRODUCT),
    ("USC", EntityType.ORG),
    ("U.S.C.", EntityType.ORG),
]


def _catalog_from_app(app_dir: Path) -> list[tuple[str, EntityType]]:
    out = list(_CATALOG)
    seen = {t[0].lower() for t in out}
    try:
        from engines.proper_nouns_dict import (
            keep_latin_tokens,
            preferred_translations,
            transliterate_names,
        )

        for latin in keep_latin_tokens(app_dir):
            if latin.lower() not in seen:
                out.append((latin, EntityType.ORG))
                seen.add(latin.lower())
        for title in preferred_translations(app_dir):
            if title.lower() not in seen:
                out.append((title, EntityType.TITLE))
                seen.add(title.lower())
        for name in transliterate_names(app_dir):
            if name.lower() not in seen:
                out.append((name, EntityType.PERSON))
                seen.add(name.lower())
    except Exception:
        pass
    out.sort(key=lambda x: -len(x[0]))
    return out


def populate_registry_from_text(
    entity_manager: EntityManager,
    text: str,
    app_dir: Path,
    *,
    display_map: dict[str, str] | None = None,
) -> None:
    """NER substitute: catalog-based entity detection."""
    display_map = display_map or {}
    src = str(text or "")
    for entity, etype in _catalog_from_app(app_dir):
        pat = re.compile(r"(?<!\w)" + re.escape(entity) + r"(?!\w)", re.IGNORECASE)
        if pat.search(src):
            entity_manager.registry.register(
                entity,
                etype,
                display=display_map.get(entity, ""),
            )


def translate_segment_enterprise(
    text: str,
    src_lang: str,
    tgt_lang: str,
    *,
    app_dir: Path,
    segment_index: int = -1,
    engine_ids: list[str] | None = None,
    display_map: dict[str, str] | None = None,
) -> tuple[str, dict[str, Any]]:
    """
    Full enterprise MT path: EntityManager → Tournament → Fusion → Restore.
    Naturalizer runs later in pipeline on restored text.
    """
    t0 = time.perf_counter()
    src = (src_lang or "en").split("-")[0].lower()
    tgt = (tgt_lang or "uk").split("-")[0].lower()

    entity_manager = EntityManager(app_dir)
    populate_registry_from_text(entity_manager, text, app_dir, display_map=display_map)

    if not entity_manager.registry.all_records():
        # No entities — still run tournament on plain text
        pass

    if not engine_ids:
        from engines.mt.registry import ordered_engines_for_pair

        engine_ids = [e.id for e in ordered_engines_for_pair(app_dir, src, tgt)]

    health = AutoHealthMonitor(app_dir)
    dev_log = EnterpriseDevLog(app_dir)

    def _translate(engine_id: str, masked: str, sl: str, tl: str) -> str:
        from engines.mt.registry import get_engine_by_id

        eng = get_engine_by_id(engine_id)
        if not eng:
            raise RuntimeError(f"engine not found: {engine_id}")
        result = eng.translate(masked, sl, tl)
        if not result.text:
            raise RuntimeError(result.error or "empty translation")
        return result.text

    candidates = run_tournament(
        text,
        source_lang=src,
        target_lang=tgt,
        engine_ids=engine_ids,
        translate_fn=_translate,
        entity_manager=entity_manager,
        app_dir=app_dir,
        health=health,
    )

    try:
        fusion = fuse_candidates(candidates, entity_manager)
    except IntegrityException:
        # Retry with best-effort fuzzy on top candidate if any text exists
        top = next((c for c in candidates if c.text.strip()), None)
        if not top:
            raise
        restored, restored_ids, warnings = entity_manager.restore_text(
            top.text,
            engine_id=top.engine_id,
            stage="fusion_fallback_restore",
        )
        fusion_text = restored
        fusion_reason = f"fallback={top.engine_id};warnings={len(warnings)}"
        winner_engine = top.engine_id
        winner_score = top.score
        restored_entities = restored_ids
    else:
        fusion_text = fusion.text
        fusion_reason = fusion.fusion_reason
        winner_engine = fusion.winner_engine
        winner_score = fusion.winner_score
        restored_entities = fusion.restored_entities

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    health.record(
        winner_engine,
        score=winner_score,
        latency_ms=elapsed_ms,
        placeholder_ok=True,
        won=True,
    )

    meta: dict[str, Any] = {
        "engine": winner_engine,
        "route_label": "enterprise_tournament",
        "quality_score": winner_score,
        "enterprise": True,
        "enterprise_restored": True,
        "enterprise_version": 1,
        "fusion_reason": fusion_reason,
        "tournament_engines": [c.engine_id for c in candidates],
        "tournament_scores": {c.engine_id: c.score for c in candidates},
        "restored_entities": restored_entities,
        "elapsed_ms": round(elapsed_ms, 1),
    }

    if architect_mode():
        meta["architect"] = {
            "original": text,
            "registry": [r.to_dict() for r in entity_manager.registry.all_records()],
            "candidates": [
                {
                    "engine": c.engine_id,
                    "text": c.text,
                    "score": c.score,
                    "score_details": c.score_details,
                    "placeholder_ok": c.placeholder_ok,
                    "error": c.error,
                    "elapsed_ms": c.elapsed_ms,
                }
                for c in candidates
            ],
            "fusion": fusion_text,
            "fusion_reason": fusion_reason,
            "health": health.snapshot(),
            "timing_ms": elapsed_ms,
        }

    dev_log.log_segment(
        {
            "segment_index": segment_index,
            "original": text,
            "src_lang": src,
            "tgt_lang": tgt,
            "registry": meta.get("architect", {}).get("registry", []),
            "candidates": meta.get("architect", {}).get("candidates", []),
            "fusion": fusion_text,
            "winner": winner_engine,
            "scores": meta.get("tournament_scores"),
            "errors": [c.error for c in candidates if c.error],
            "timing_ms": elapsed_ms,
        }
    )

    entity_manager.registry.save_session()
    return fusion_text, meta
