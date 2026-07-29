# -*- coding: utf-8 -*-
"""Batch MT for Simple — cache + Marian batch / parallel fallback, 1:1 parity.

Stage 11: glossary on cache hit; per-segment engine labels (cache / cache+glossary / marian*).
"""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("tubedub.mt_batch")

DEFAULT_BATCH_SIZE = 10
MAX_BATCH_SIZE = 15
DEFAULT_ONLINE_CONCURRENCY = 3
DEFAULT_LOCAL_CONCURRENCY = 2


def resolve_mt_batch_size(requested: int | None = None) -> int:
    if requested is not None:
        try:
            return max(1, min(MAX_BATCH_SIZE, int(requested)))
        except (TypeError, ValueError):
            pass
    raw = (os.getenv("VM_MT_BATCH_SIZE") or os.getenv("EDGE_MT_BATCH_SIZE") or "").strip()
    if raw.isdigit():
        return max(1, min(MAX_BATCH_SIZE, int(raw)))
    return DEFAULT_BATCH_SIZE


def resolve_mt_concurrency(*, online: bool, requested: int | None = None) -> int:
    if requested is not None:
        try:
            cap = 4 if online else 2
            return max(1, min(cap, int(requested)))
        except (TypeError, ValueError):
            pass
    env = (os.getenv("VM_MT_CONCURRENCY") or "").strip()
    if env.isdigit():
        cap = 4 if online else 2
        return max(1, min(cap, int(env)))
    return DEFAULT_ONLINE_CONCURRENCY if online else DEFAULT_LOCAL_CONCURRENCY


def _chunks(indices: list[int], size: int) -> list[list[int]]:
    if size <= 0:
        size = 1
    return [indices[i : i + size] for i in range(0, len(indices), size)]


def _try_marian_batch(
    texts: list[str],
    source_lang: str,
    target_lang: str,
    *,
    app_dir: Path,
) -> list[tuple[str, dict[str, Any]]] | None:
    try:
        from engines.mt.stable_translate import translate_batch_marian

        return translate_batch_marian(texts, source_lang, target_lang, app_dir=app_dir)
    except Exception as exc:
        logger.info("mt_batch: Marian batch unavailable (%s) — fallback", exc)
        return None


def _rejoin_by_parent(
    unit_results: list[str],
    parent_indices: list[int],
    n_parents: int,
) -> list[str]:
    buckets: list[list[str]] = [[] for _ in range(n_parents)]
    for ui, parent in enumerate(parent_indices):
        if 0 <= parent < n_parents:
            piece = str(unit_results[ui] or "").strip()
            if piece:
                buckets[parent].append(piece)
    return [" ".join(parts).strip() for parts in buckets]


def _skip_cache_long(text: str) -> bool:
    """Stage 12b: long/oversized segments always Marian+split (default ON)."""
    if (os.getenv("VM_MT_SKIP_CACHE_LONG", "1").strip().lower()
            in ("0", "false", "no", "off")):
        return False
    w = len(str(text or "").split())
    if w > 55:
        return True
    try:
        from engines.mt.oversized_guard import is_oversized_mt_unit

        return is_oversized_mt_unit(text)
    except Exception:
        return w > 55


def _translate_one_traced(
    text: str,
    source_lang: str,
    target_lang: str,
    *,
    app_dir: Path | None,
) -> tuple[str, dict[str, Any]]:
    from engines.translation import translate_text_traced

    out, meta = translate_text_traced(
        text,
        source_lang,
        target_lang,
        app_dir=app_dir,
    )
    return str(out or "").strip(), dict(meta or {})


def translate_segments_batch(
    segments: list[str],
    source_lang: str,
    target_lang: str,
    *,
    batch_size: int | None = None,
    concurrency: int | None = None,
    cache_dir: Path | None = None,
    app_dir: Path | None = None,
    engine_preference: str = "auto",
    prefer_marian: bool = True,
    on_progress: Callable[[int, int], None] | None = None,
) -> tuple[list[str], dict[str, Any]]:
    """Translate segments 1:1 with disk cache + batch Marian / parallel fallback."""
    from engines.mt.glossary_en_uk import finalize_mt_text
    from engines.mt.oversized_guard import guard_segments_before_mt
    from engines.mt_cache import (
        default_cache_dir,
        empty_mt_stats,
        lookup_mt_cache,
        mt_cache_disabled,
        store_mt_cache,
    )

    stats = empty_mt_stats()
    t0 = time.perf_counter()
    src_l = str(source_lang or "en")
    tgt_l = str(target_lang or "uk")
    bsize = resolve_mt_batch_size(batch_size)
    cdir = Path(cache_dir) if cache_dir is not None else default_cache_dir()
    cdir.mkdir(parents=True, exist_ok=True)
    base = Path(app_dir) if app_dir is not None else Path(__file__).resolve().parent.parent
    engine_tag = str(engine_preference or "auto").strip() or "auto"
    stats["mt_cache_bypassed"] = bool(mt_cache_disabled())

    n = len(segments or [])
    out: list[str] = [""] * n
    seg_engines: list[str] = [""] * n
    stats["mt_segments"] = n
    stats["mt_batch_size"] = bsize
    stats["mt_path"] = "stage7_batch_cache"

    miss_indices: list[int] = []
    for i, raw in enumerate(segments or []):
        text = str(raw or "").strip()
        if not text:
            out[i] = ""
            seg_engines[i] = "none"
            continue
        if src_l.lower() == tgt_l.lower():
            out[i] = text
            seg_engines[i] = "none"
            stats["mt_cache_hits"] += 1
            continue
        # Stage 12b: long/oversized → skip cache → Marian+split
        if _skip_cache_long(text):
            miss_indices.append(i)
            stats["mt_cache_misses"] += 1
            stats["mt_long_cache_skips"] = int(stats.get("mt_long_cache_skips") or 0) + 1
            logger.info(
                "[MT] skip_cache_long seg#%d words=%d",
                i + 1,
                len(text.split()),
            )
            continue
        hit = lookup_mt_cache(
            text, src_l, tgt_l, engine=engine_tag, cache_dir=cdir
        )
        if hit is not None:
            # Hit already passed incomplete check inside lookup — safe to finalize.
            finalized = finalize_mt_text(src_l, tgt_l, hit)
            out[i] = finalized
            if src_l.lower() == "en" and tgt_l.lower() == "uk":
                seg_engines[i] = "cache+glossary"
            else:
                seg_engines[i] = "cache"
            stats["mt_cache_hits"] += 1
        else:
            miss_indices.append(i)
            stats["mt_cache_misses"] += 1

    if on_progress:
        try:
            on_progress(stats["mt_cache_hits"], n)
        except Exception:
            pass

    engines_used: set[str] = set()
    used_marian_batch = False
    filled_by_marian: set[int] = set()

    if miss_indices and prefer_marian:
        for group in _chunks(list(miss_indices), bsize):
            texts = [str(segments[i] or "").strip() for i in group]
            guard = guard_segments_before_mt(texts, log=True)
            stats["mt_guard_splits"] = int(stats.get("mt_guard_splits") or 0) + int(
                guard.split_count or 0
            )
            unit_texts = guard.texts
            parents = guard.parent_indices
            batch = _try_marian_batch(unit_texts, src_l, tgt_l, app_dir=base)
            if batch is None or len(batch) != len(unit_texts):
                continue
            unit_trs = [str((batch[j][0] or "")).strip() for j in range(len(unit_texts))]
            group_ok = True
            for ui, ut in enumerate(unit_texts):
                if ut and not unit_trs[ui]:
                    group_ok = False
                    break
            if not group_ok:
                continue
            rejoined = _rejoin_by_parent(unit_trs, parents, len(texts))
            for meta_pair in batch:
                eng = str((meta_pair[1] or {}).get("engine") or "marian")
                engines_used.add(eng)
            used_marian_batch = True
            stats["mt_calls"] += 1
            for j, idx in enumerate(group):
                tr = finalize_mt_text(src_l, tgt_l, rejoined[j])
                out[idx] = tr
                seg_engines[idx] = "marian_batch"
                filled_by_marian.add(idx)
                if tr:
                    store_mt_cache(
                        texts[j],
                        tr,
                        src_l,
                        tgt_l,
                        engine=engine_tag,
                        cache_dir=cdir,
                    )
            if on_progress:
                try:
                    on_progress(n - (len(miss_indices) - len(filled_by_marian)), n)
                except Exception:
                    pass

    still_miss = [
        i
        for i in miss_indices
        if i not in filled_by_marian
        and not out[i]
        and str(segments[i] or "").strip()
    ]
    online = bool(still_miss)
    workers = resolve_mt_concurrency(online=online, requested=concurrency)
    stats["mt_concurrency_used"] = workers if still_miss else 1

    def _one(idx: int) -> tuple[int, str, dict[str, Any], int]:
        text = str(segments[idx] or "").strip()
        retries = 0
        last_err = ""
        for attempt in range(3):
            try:
                tr, meta = _translate_one_traced(text, src_l, tgt_l, app_dir=base)
                if tr:
                    return idx, tr, meta, retries
                last_err = "empty"
            except Exception as exc:
                last_err = str(exc)
                retries += 1
                time.sleep(0.4 * (attempt + 1))
        return idx, "", {"engine": "error", "error": last_err}, retries

    if still_miss:
        if workers <= 1 or len(still_miss) == 1:
            for idx in still_miss:
                i2, tr, meta, retries = _one(idx)
                stats["mt_calls"] += 1
                stats["mt_retries"] += retries
                eng = str(meta.get("engine") or meta.get("route_label") or "marian")
                if "marian" in eng.lower():
                    eng = "marian"
                engines_used.add(eng)
                tr = finalize_mt_text(src_l, tgt_l, tr)
                out[i2] = tr
                seg_engines[i2] = eng if eng else "marian"
                if tr:
                    store_mt_cache(
                        str(segments[i2] or ""),
                        tr,
                        src_l,
                        tgt_l,
                        engine=engine_tag,
                        cache_dir=cdir,
                    )
                if on_progress:
                    try:
                        on_progress(n - sum(1 for k in still_miss if not out[k]), n)
                    except Exception:
                        pass
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futs = {pool.submit(_one, idx): idx for idx in still_miss}
                for fut in as_completed(futs):
                    i2, tr, meta, retries = fut.result()
                    stats["mt_calls"] += 1
                    stats["mt_retries"] += retries
                    eng = str(meta.get("engine") or meta.get("route_label") or "marian")
                    if "marian" in eng.lower():
                        eng = "marian"
                    engines_used.add(eng)
                    tr = finalize_mt_text(src_l, tgt_l, tr)
                    out[i2] = tr
                    seg_engines[i2] = eng if eng else "marian"
                    if tr:
                        store_mt_cache(
                            str(segments[i2] or ""),
                            tr,
                            src_l,
                            tgt_l,
                            engine=engine_tag,
                            cache_dir=cdir,
                        )
                    if on_progress:
                        try:
                            on_progress(
                                sum(1 for x in out if x is not None) - out.count(""),
                                n,
                            )
                        except Exception:
                            pass

    if len(out) != n:
        raise RuntimeError(f"mt_batch parity broken: in={n} out={len(out)}")

    stats["mt_segment_engines"] = list(seg_engines)
    if used_marian_batch and not still_miss and stats["mt_cache_hits"] == 0:
        stats["mt_engine"] = "marian_batch"
    elif used_marian_batch and stats["mt_cache_hits"] > 0:
        stats["mt_engine"] = "marian_batch+cache"
    elif engines_used:
        stats["mt_engine"] = "+".join(sorted(engines_used))
    else:
        # Pure cache path — prefer cache+glossary if any segment used it
        if any(e == "cache+glossary" for e in seg_engines):
            stats["mt_engine"] = "cache+glossary"
        else:
            stats["mt_engine"] = "cache" if stats["mt_cache_hits"] else "none"

    stats["mt_wall_sec"] = round(time.perf_counter() - t0, 3)
    logger.info(
        "mt_batch: segs=%d wall=%.2fs calls=%d hits=%d misses=%d engine=%s conc=%s splits=%s bypass=%s",
        n,
        stats["mt_wall_sec"],
        stats["mt_calls"],
        stats["mt_cache_hits"],
        stats["mt_cache_misses"],
        stats["mt_engine"],
        stats["mt_concurrency_used"],
        stats.get("mt_guard_splits", 0),
        stats.get("mt_cache_bypassed"),
    )
    return out, stats
