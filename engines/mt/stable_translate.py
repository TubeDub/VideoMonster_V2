"""
Stable MT path — direct Marian in the main thread.

No Router, no pivot, no cascade, no ThreadPoolExecutor, no from_pretrained during dub.
Models must be preloaded during «Подготовка компонентов».
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
    num_beams = 1 if use_stable_mt() else 4

    def _infer_one(piece: str) -> str:
        with _MARIAN_INFER_LOCK:
            batch = tok(
                [piece],
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=512,
            )
            with torch.no_grad():
                out_ids = model.generate(**batch, max_length=512, num_beams=num_beams)
            return tok.decode(out_ids[0], skip_special_tokens=True).strip()

    try:
        from engines.mt.oversized_guard import (
            is_oversized_mt_unit,
            translate_oversized_safely,
        )

        if is_oversized_mt_unit(clean):
            result = translate_oversized_safely(clean, _infer_one)
            meta["oversized_split"] = True
        else:
            result = _infer_one(clean)
    except Exception as exc:
        ms = (time.perf_counter() - t0) * 1000.0
        logger.error("[StableMT] infer failed seg=%s %s→%s: %s", segment_index, src, tgt, exc)
        meta["engine"] = "failed"
        meta["error"] = str(exc)
        meta["elapsed_ms"] = round(ms, 1)
        return "", meta

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
    """Batch Marian inference — one ``generate()`` for multiple segments (TZ §2)."""
    import torch

    from engines.model_manager.downloader import load_marian
    from engines.model_manager.runtime import OfflineOnlyError
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
    num_beams = 1 if use_stable_mt() else 4
    t0 = time.perf_counter()

    non_empty_idx = [i for i, t in enumerate(cleaned) if t]
    if not non_empty_idx:
        return [("", {"engine": "none"}) for _ in cleaned]

    batch_texts = [cleaned[i] for i in non_empty_idx]
    try:
        from engines.mt.oversized_guard import (
            is_oversized_mt_unit,
            translate_oversized_safely,
        )

        # If any unit is oversized, translate those via split; batch the rest.
        decoded_map: dict[int, str] = {}
        oversized_idx: set[int] = set()
        batch_idx: list[int] = []
        batch_payload: list[str] = []
        for i, src_text in zip(non_empty_idx, batch_texts):
            if is_oversized_mt_unit(src_text):
                oversized_idx.add(i)

                def _infer_piece(piece: str, _tok=tok, _model=model, _beams=num_beams) -> str:
                    with _MARIAN_INFER_LOCK:
                        b = _tok(
                            [piece],
                            return_tensors="pt",
                            padding=True,
                            truncation=True,
                            max_length=512,
                        )
                        with torch.no_grad():
                            gen = _model.generate(**b, max_length=512, num_beams=_beams)
                        return _tok.decode(gen[0], skip_special_tokens=True).strip()

                decoded_map[i] = translate_oversized_safely(src_text, _infer_piece)
            else:
                batch_idx.append(i)
                batch_payload.append(src_text)

        if batch_payload:
            with _MARIAN_INFER_LOCK:
                batch = tok(
                    batch_payload,
                    return_tensors="pt",
                    padding=True,
                    truncation=True,
                    max_length=512,
                )
                with torch.no_grad():
                    generated = model.generate(**batch, max_length=512, num_beams=num_beams)
                decoded = [
                    tok.decode(row, skip_special_tokens=True).strip()
                    for row in generated
                ]
            for i, tr in zip(batch_idx, decoded):
                decoded_map[i] = tr
    except Exception as exc:
        logger.error("[StableMT] batch infer failed: %s", exc)
        return [("", {"engine": "failed", "error": str(exc)}) for _ in cleaned]

    ms = (time.perf_counter() - t0) * 1000.0
    per_ms = ms / max(len(non_empty_idx), 1)
    for i, src_text in enumerate(cleaned):
        if not src_text:
            out.append(("", {"engine": "none", "batch": True}))
            continue
        result = decoded_map.get(i, "")
        meta: dict[str, Any] = {
            "engine": "marian",
            "route": "direct",
            "route_label": f"{src}→{tgt}",
            "stable_mt": True,
            "batch": True,
            "elapsed_ms": round(per_ms, 1),
            "engine_version": name,
            "oversized_split": i in oversized_idx,
        }
        if result:
            score, qd = compute_quality_score(src_text, result, src_lang=src, tgt_lang=tgt)
            meta["quality_score"] = score
            meta["quality_details"] = qd
        out.append((result, meta))
    return out
