"""
Stable MT path — direct Marian in the main thread.

No Router, no pivot, no cascade, no ThreadPoolExecutor, no from_pretrained during dub.
Models must be preloaded during «Подготовка компонентов».

Stage 10c: beams via resolve_marian_beams (default 2); EN→UK glossary protect/restore.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from engines.mt.lang_codes import normalize_lang

logger = logging.getLogger("tubedub.engines.mt.stable")

_MARIAN_INFER_LOCK = threading.Lock()
_TOKEN_MAX = 512
_GEN_MAX = 512


def use_stable_mt() -> bool:
    """
    Legacy Marian-only path.
    Default OFF when Translation Manager is active (VM_TRANSLATION_MANAGER=1).
    Set VM_STABLE_MT_ONLY=1 to force Marian-only dubbing.
    Set VM_USE_ROUTER=1 + VM_DEV_MODE=1 for legacy router dev path.
    """
    if os.getenv("VM_STABLE_MT_ONLY", "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    from engines.translation_manager import use_translation_manager

    if use_translation_manager():
        return False
    if os.getenv("VM_USE_ROUTER", "").strip().lower() in ("1", "true", "yes"):
        return os.getenv("VM_DEV_MODE", "").strip().lower() in ("1", "true", "yes")
    return True


def resolve_marian_beams(simple: bool = True) -> int:
    raw = (os.getenv("MT_NUM_BEAMS") or os.getenv("VM_MT_NUM_BEAMS") or "").strip()
    if raw.isdigit():
        return max(1, min(4, int(raw)))
    return 2 if simple else 4


def ensure_marian_ready(app_dir: Path, src_lang: str, tgt_lang: str) -> None:
    """Load Marian weights on the main thread before segment loop."""
    from engines.model_manager.downloader import is_mt_engine_ready, load_marian
    from engines.model_manager.runtime import OfflineOnlyError

    src, tgt = normalize_lang(src_lang), normalize_lang(tgt_lang)
    if src == tgt:
        return
    if not is_mt_engine_ready(app_dir, "marian", src, tgt):
        raise OfflineOnlyError(
            f"Marian {src}->{tgt} not prepared. Run «Подготовка компонентов» first."
        )
    t0 = time.perf_counter()
    load_marian(app_dir, src, tgt)
    ms = (time.perf_counter() - t0) * 1000.0
    logger.info("[StableMT] Marian ready %s→%s preload_ms=%.0f", src, tgt, ms)


def translate_direct_marian(
    text: str,
    src_lang: str,
    tgt_lang: str,
    *,
    app_dir: Path,
    segment_index: int = -1,
) -> tuple[str, dict[str, Any]]:
    """Single-segment Marian translate — main thread only."""
    import torch

    from engines.model_manager.downloader import load_marian
    from engines.model_manager.runtime import OfflineOnlyError
    from engines.mt.glossary_en_uk import (
        apply_glossary_en_uk,
        apply_post_mt_glossary_fixes,
        protect_glossary,
        restore_glossary,
    )

    src, tgt = normalize_lang(src_lang), normalize_lang(tgt_lang)
    meta: dict[str, Any] = {
        "engine": "marian",
        "route": "direct",
        "route_label": f"{src}→{tgt}",
        "direct": True,
        "pivot": None,
        "router": False,
        "stable_mt": True,
        "segment_index": segment_index,
        "engines_tried": ["marian"],
        "mt_retries": 0,
        "quality_score": 0.0,
        "quality_details": {},
        "router_reason": "stable_direct_marian",
    }

    clean = " ".join(str(text or "").split()).strip()
    if not clean:
        return text, meta
    if src == tgt:
        meta["engine"] = "none"
        meta["quality_score"] = 100.0
        return text, meta

    try:
        loaded = load_marian(app_dir, src, tgt)
    except OfflineOnlyError:
        raise
    except Exception as exc:
        logger.error("[StableMT] load failed %s→%s: %s", src, tgt, exc)
        meta["engine"] = "failed"
        meta["error"] = str(exc)
        return "", meta

    if not loaded:
        meta["engine"] = "failed"
        meta["error"] = "no_model"
        return "", meta

    tok, model, name = loaded
    t0 = time.perf_counter()
    num_beams = resolve_marian_beams(simple=True)
    meta["num_beams"] = num_beams

    # Glossary protect on full text before infer (incl. oversized split parts).
    forms: list[str] = []
    work = clean
    if src == "en" and tgt == "uk":
        work, forms = protect_glossary(clean)
        if forms:
            meta["glossary_protected"] = len(forms)

    def _infer_one(piece: str) -> str:
        with _MARIAN_INFER_LOCK:
            batch = tok(
                [piece],
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=_TOKEN_MAX,
            )
            with torch.no_grad():
                out_ids = model.generate(
                    **batch, max_length=_GEN_MAX, num_beams=num_beams
                )
            return tok.decode(out_ids[0], skip_special_tokens=True).strip()

    try:
        from engines.mt.oversized_guard import (
            is_oversized_mt_unit,
            split_oversized_unit,
            translate_oversized_safely,
        )

        if is_oversized_mt_unit(work):
            parts = split_oversized_unit(work)
            logger.warning(
                "[MT Guard] oversized seg#%s → %d parts",
                segment_index if segment_index >= 0 else "?",
                len(parts),
            )
            result = translate_oversized_safely(work, _infer_one)
            meta["oversized_split"] = True
            meta["oversized_parts"] = len(parts)
        else:
            result = _infer_one(work)
    except Exception as exc:
        ms = (time.perf_counter() - t0) * 1000.0
        logger.error(
            "[StableMT] infer failed seg=%s %s→%s: %s", segment_index, src, tgt, exc
        )
        meta["engine"] = "failed"
        meta["error"] = str(exc)
        meta["elapsed_ms"] = round(ms, 1)
        return "", meta

    if src == "en" and tgt == "uk":
        result = restore_glossary(result, forms)
        result = apply_post_mt_glossary_fixes(result)
        result = apply_glossary_en_uk(result)

    ms = (time.perf_counter() - t0) * 1000.0
    meta["engine_version"] = name
    meta["elapsed_ms"] = round(ms, 1)
    if not result:
        meta["engine"] = "failed"
        meta["error"] = "empty"
        return "", meta

    from engines.translation_quality_score import compute_quality_score

    score, qd = compute_quality_score(clean, result, src_lang=src, tgt_lang=tgt)
    meta["quality_score"] = score
    meta["quality_details"] = qd
    return result, meta


def translate_batch_marian(
    texts: list[str],
    src_lang: str,
    tgt_lang: str,
    *,
    app_dir: Path,
) -> list[tuple[str, dict[str, Any]]]:
    """Batch Marian — split oversized units first, then tokenize (truncate safe)."""
    import torch

    from engines.model_manager.downloader import load_marian
    from engines.model_manager.runtime import OfflineOnlyError
    from engines.mt.glossary_en_uk import (
        apply_glossary_en_uk,
        apply_post_mt_glossary_fixes,
        protect_glossary,
        restore_glossary,
    )
    from engines.mt.oversized_guard import (
        guard_segments_before_mt,
        is_oversized_mt_unit,
    )
    from engines.translation_quality_score import compute_quality_score

    src, tgt = normalize_lang(src_lang), normalize_lang(tgt_lang)
    cleaned = [" ".join(str(t or "").split()).strip() for t in texts]
    out: list[tuple[str, dict[str, Any]]] = []
    if src == tgt:
        for t in cleaned:
            meta = {"engine": "none", "route": "direct", "batch": True}
            out.append((t, meta))
        return out

    try:
        loaded = load_marian(app_dir, src, tgt)
    except OfflineOnlyError:
        raise
    except Exception as exc:
        logger.error("[StableMT] batch load failed %s→%s: %s", src, tgt, exc)
        return [("", {"engine": "failed", "error": str(exc)}) for _ in cleaned]

    if not loaded:
        return [("", {"engine": "failed", "error": "no_model"}) for _ in cleaned]

    tok, model, name = loaded
    num_beams = resolve_marian_beams(simple=True)
    t0 = time.perf_counter()

    # Protect glossary on full segment text, then expand oversized → units.
    protected: list[str] = []
    gloss_forms: list[list[str]] = []
    for t in cleaned:
        if not t:
            protected.append("")
            gloss_forms.append([])
            continue
        forms: list[str] = []
        work = t
        if src == "en" and tgt == "uk":
            work, forms = protect_glossary(t)
        protected.append(work)
        gloss_forms.append(forms)

    guard = guard_segments_before_mt(protected, log=True)
    unit_texts = guard.texts
    parents = guard.parent_indices

    decoded_units: list[str] = [""] * len(unit_texts)
    non_empty = [(i, t) for i, t in enumerate(unit_texts) if t]
    try:
        if non_empty:
            idxs = [i for i, _ in non_empty]
            payload = [t for _, t in non_empty]
            chunk = 12
            for off in range(0, len(payload), chunk):
                sl_idx = idxs[off : off + chunk]
                sl_txt = payload[off : off + chunk]
                with _MARIAN_INFER_LOCK:
                    batch = tok(
                        sl_txt,
                        return_tensors="pt",
                        padding=True,
                        truncation=True,
                        max_length=_TOKEN_MAX,
                    )
                    with torch.no_grad():
                        generated = model.generate(
                            **batch, max_length=_GEN_MAX, num_beams=num_beams
                        )
                    decoded = [
                        tok.decode(row, skip_special_tokens=True).strip()
                        for row in generated
                    ]
                for ui, tr in zip(sl_idx, decoded):
                    decoded_units[ui] = tr
    except Exception as exc:
        logger.error("[StableMT] batch infer failed: %s", exc)
        return [("", {"engine": "failed", "error": str(exc)}) for _ in cleaned]

    joined: list[list[str]] = [[] for _ in cleaned]
    for ui, parent in enumerate(parents):
        if 0 <= parent < len(joined) and decoded_units[ui]:
            joined[parent].append(decoded_units[ui])

    ms = (time.perf_counter() - t0) * 1000.0
    per_ms = ms / max(len([t for t in cleaned if t]), 1)
    for i, src_text in enumerate(cleaned):
        if not src_text:
            out.append(("", {"engine": "none", "batch": True}))
            continue
        result = " ".join(joined[i]).strip()
        if src == "en" and tgt == "uk":
            result = restore_glossary(result, gloss_forms[i])
            result = apply_post_mt_glossary_fixes(result)
            result = apply_glossary_en_uk(result)
        meta: dict[str, Any] = {
            "engine": "marian",
            "route": "direct",
            "route_label": f"{src}→{tgt}",
            "stable_mt": True,
            "batch": True,
            "elapsed_ms": round(per_ms, 1),
            "engine_version": name,
            "num_beams": num_beams,
            "oversized_split": bool(src_text and is_oversized_mt_unit(protected[i])),
            "glossary_protected": len(gloss_forms[i]),
        }
        if result:
            score, qd = compute_quality_score(
                src_text, result, src_lang=src, tgt_lang=tgt
            )
            meta["quality_score"] = score
            meta["quality_details"] = qd
        out.append((result, meta))
    return out
