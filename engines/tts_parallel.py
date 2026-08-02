# -*- coding: utf-8 -*-
"""Parallel Edge-TTS for Simple — warmup + pool + cache + per-segment retry.

API:
  synthesize_segments_parallel(items, concurrency=6, cache_dir=...)
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("tubedub.tts_parallel")

DEFAULT_CONCURRENCY = 6
MAX_CONCURRENCY = 8
WARMUP_DEFAULT = 2
RETRIES_DEFAULT = 3


def resolve_edge_tts_concurrency(requested: int | None = None) -> int:
    """EDGE_TTS_CONCURRENCY (preferred) or VM_TTS_PARALLEL; default 6, cap 8."""
    if requested is not None:
        try:
            return max(1, min(MAX_CONCURRENCY, int(requested)))
        except (TypeError, ValueError):
            pass
    for env in ("EDGE_TTS_CONCURRENCY", "VM_TTS_PARALLEL"):
        raw = (os.getenv(env) or "").strip()
        if not raw:
            continue
        try:
            return max(1, min(MAX_CONCURRENCY, int(raw)))
        except ValueError:
            continue
    return DEFAULT_CONCURRENCY


def _is_rate_limit_error(exc: BaseException | None) -> bool:
    if exc is None:
        return False
    msg = str(exc).lower()
    return any(
        tok in msg
        for tok in (
            "429",
            "rate limit",
            "ratelimit",
            "too many requests",
            "throttl",
            "503",
            "capacity",
        )
    )


def _copy_valid(src: Path, dest: Path) -> bool:
    try:
        from engines.tts_cache import is_valid_tts_file

        if not is_valid_tts_file(src):
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.resolve() != src.resolve():
            shutil.copy2(str(src), str(dest))
        return is_valid_tts_file(dest)
    except OSError:
        return False


def _synthesize_one_edge(
    text: str,
    voice: str,
    out_path: Path,
    *,
    rate: str = "",
    pitch: str = "",
    engine_id: str = "edge-offline",
    timeout_sec: float = 120.0,
) -> None:
    """Blocking Edge-TTS synthesize into out_path (runs in worker thread).

    Single attempt — outer synthesize_one_with_cache owns retries.
    """
    import edge_tts

    from engines.tts import _sanitize_pitch, _tts_rate, sanitize_tts_text
    from engines.tts_cache import is_valid_tts_file

    text0 = sanitize_tts_text(text)
    if text0.lstrip().startswith("<speak"):
        import re as _re

        text0 = _re.sub(r"<[^>]+>", " ", text0)
        text0 = _re.sub(r"[ \t]+", " ", text0).strip()
    if not text0:
        raise RuntimeError("PIPELINE_LANG_MIX: empty TTS text after sanitize")

    # Stage 18: same voice lock as serial Edge path (no bypass).
    from engines.tts import _detect_lang_from_voice
    from engines.tts_lang_lock import assert_voice_matches_target, is_uk_tts_text_ok

    _lang = _detect_lang_from_voice(voice) or "uk"
    assert_voice_matches_target(voice, _lang, raise_error=True)
    if str(_lang).split("-")[0].lower() == "uk" and not is_uk_tts_text_ok(text0):
        raise RuntimeError(
            f"PIPELINE_LANG_MIX: cyrillic_ratio < 0.55 parallel TTS text={text0[:80]!r}"
        )

    try:
        from engines.stress_marks import add_stress_marks

        text0 = add_stress_marks(text0, lang=_detect_lang_from_voice(voice))
    except Exception:
        pass

    out_path.parent.mkdir(parents=True, exist_ok=True)
    effective_rate = (rate or _tts_rate()).strip() or "-5%"
    effective_pitch = _sanitize_pitch(pitch) if pitch else None
    kwargs: dict = {"text": text0, "voice": voice, "rate": effective_rate}
    if effective_pitch:
        kwargs["pitch"] = effective_pitch

    # Non-edge engines via Stage 20 factory (Edge fallback if tts_uk/piper missing).
    eid = (engine_id or "edge-offline").strip() or "edge-offline"
    if eid not in ("edge-offline", "edge", "edge-tts", "edge_tts"):
        from engines.tts_backends import synthesize_with_backend

        result = synthesize_with_backend(
            text0,
            voice,
            str(out_path),
            engine_id=eid,
            rate=effective_rate,
            pitch=effective_pitch,
        )
        if not result.ok:
            raise RuntimeError(result.error or "TTS failed")
        if not is_valid_tts_file(out_path):
            raise RuntimeError(f"TTS produced empty/invalid file: {out_path}")
        return

    async def _run() -> None:
        communicate = edge_tts.Communicate(**kwargs)
        await asyncio.wait_for(communicate.save(str(out_path)), timeout=timeout_sec)

    asyncio.run(_run())
    if not is_valid_tts_file(out_path):
        raise RuntimeError(f"TTS produced empty/invalid file: {out_path}")


def synthesize_one_with_cache(
    *,
    index: int,
    text: str,
    voice: str,
    out_path: str | Path,
    rate: str = "",
    pitch: str = "",
    engine_id: str = "edge-offline",
    cache_dir: Path | None = None,
    retries: int = RETRIES_DEFAULT,
    skip_existing: bool = True,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Synthesize one segment with skip-existing + disk cache + retries."""
    from engines.tts_cache import (
        is_valid_tts_file,
        lookup_tts_cache,
        materialize_cached,
        store_tts_cache,
    )

    dest = Path(out_path)
    text0 = str(text or "").strip()
    voice0 = str(voice or "").strip()
    result: dict[str, Any] = {
        "index": int(index),
        "path": str(dest),
        "cache_hit": False,
        "skipped_existing": False,
        "error": None,
        "retries": 0,
    }
    from engines.tts import _detect_lang_from_voice
    from engines.tts_cache import tts_cache_disabled
    from engines.tts_lang_lock import assert_voice_matches_target

    _tgt_lang = _detect_lang_from_voice(voice0) or "uk"
    if not text0:
        result["error"] = "empty_text"
        # Stage 18: empty text must not silently become silence on uk.
        if str(_tgt_lang).split("-")[0].lower() == "uk":
            raise RuntimeError(
                f"PIPELINE_LANG_MIX: empty TTS text idx={index} — refuse skip→silence"
            )
        return result

    # Stage 18 voice lock before cache / Edge (same as serial path).
    assert_voice_matches_target(voice0, _tgt_lang, raise_error=True)

    if skip_existing and is_valid_tts_file(dest):
        result["skipped_existing"] = True
        result["path"] = str(dest)
        logger.info("tts_skip_existing idx=%s path=%s", index, dest.name)
        return result

    _use_cache = bool(use_cache) and not tts_cache_disabled()
    _lang = str(_tgt_lang).split("-")[0].lower()
    if _use_cache:
        cached = lookup_tts_cache(
            text0,
            voice0,
            rate=rate,
            pitch=pitch,
            engine_id=engine_id,
            cache_dir=cache_dir,
            ext=dest.suffix or ".mp3",
            lang=_lang,
        )
        if cached is not None and materialize_cached(cached, dest):
            result["cache_hit"] = True
            result["path"] = str(dest)
            return result

    result["cache_miss"] = True
    last_err: Exception | None = None
    attempts = max(1, int(retries))
    for attempt in range(attempts):
        try:
            _synthesize_one_edge(
                text0,
                voice0,
                dest,
                rate=rate,
                pitch=pitch,
                engine_id=engine_id,
            )
            if _use_cache:
                store_tts_cache(
                    dest,
                    text0,
                    voice0,
                    rate=rate,
                    pitch=pitch,
                    engine_id=engine_id,
                    lang=_lang,
                    cache_dir=cache_dir,
                )
            result["retries"] = attempt
            result["path"] = str(dest)
            return result
        except Exception as exc:
            last_err = exc
            result["retries"] = attempt + 1
            logger.warning(
                "tts_parallel idx=%s attempt=%d/%d failed: %s",
                index,
                attempt + 1,
                attempts,
                exc,
            )
            if attempt < attempts - 1:
                # Back off harder on rate-limit.
                delay = 1.5 * (attempt + 1)
                if _is_rate_limit_error(exc):
                    delay = 3.0 * (attempt + 1)
                time.sleep(delay)
    result["error"] = str(last_err or "tts_failed")
    return result


def synthesize_segments_parallel(
    items: list[dict[str, Any]],
    *,
    concurrency: int | None = None,
    cache_dir: Path | None = None,
    warmup: int | None = None,
    retries: int = RETRIES_DEFAULT,
    skip_existing: bool = True,
    use_cache: bool = True,
    on_done: Callable[[int, int], None] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Parallel Edge-TTS with warmup, cache, skip-existing, per-segment retry.

    Each item: {index|g_idx, text, voice, out_path|path, rate?, pitch?, engine_id?}
    Returns (results, stats).
    """
    from engines.tts_cache import default_cache_dir, empty_stats

    stats = empty_stats()
    t0 = time.perf_counter()
    workers = resolve_edge_tts_concurrency(concurrency)
    warm_n = WARMUP_DEFAULT if warmup is None else max(0, int(warmup))
    cdir = Path(cache_dir) if cache_dir is not None else default_cache_dir()
    cdir.mkdir(parents=True, exist_ok=True)

    normalized: list[dict[str, Any]] = []
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        idx = int(raw.get("index", raw.get("g_idx", len(normalized))))
        out = raw.get("out_path") or raw.get("path") or raw.get("tts_file_path")
        if not out:
            continue
        normalized.append(
            {
                "index": idx,
                "text": str(raw.get("text") or ""),
                "voice": str(raw.get("voice") or ""),
                "out_path": str(out),
                "rate": str(raw.get("rate") or ""),
                "pitch": str(raw.get("pitch") or ""),
                "engine_id": str(raw.get("engine_id") or "edge-offline"),
                "_raw": raw,
            }
        )

    stats["tts_segments_total"] = len(normalized)
    stats["tts_concurrency_used"] = workers if len(normalized) > warm_n else 1
    results_by_idx: dict[int, dict[str, Any]] = {}
    done = 0
    rate_limit_backs = 0

    def _finish(one: dict[str, Any]) -> None:
        nonlocal done
        idx = int(one["index"])
        raw = next((n["_raw"] for n in normalized if n["index"] == idx), {})
        merged = {**raw, **one, "file": Path(one["path"]).name if one.get("path") and not one.get("error") else None}
        if one.get("cache_hit"):
            stats["tts_cache_hits"] += 1
            merged["tts_ok"] = True
            merged["cache_hit"] = True
        elif one.get("skipped_existing"):
            stats["tts_skips_existing"] += 1
            merged["tts_ok"] = True
            merged["skipped_existing"] = True
        elif one.get("error"):
            if one.get("cache_miss"):
                stats["tts_cache_misses"] += 1
            merged["tts_ok"] = False
            merged["tts_failure"] = {"error_message": one["error"]}
            merged["file"] = None
        else:
            if one.get("cache_miss"):
                stats["tts_cache_misses"] += 1
            merged["tts_ok"] = True
        # Downstream expects basename relative to artifacts/output.
        if merged.get("tts_ok") and one.get("path"):
            merged["file"] = Path(one["path"]).name
            merged["g_idx"] = idx
        stats["tts_retries"] += int(one.get("retries") or 0)
        results_by_idx[idx] = merged
        done += 1
        if on_done:
            try:
                on_done(idx, done)
            except Exception:
                pass

    # A) Warmup — first N sequential (Microsoft connection settle).
    warm_items = normalized[:warm_n]
    pool_items = normalized[warm_n:]
    for it in warm_items:
        one = synthesize_one_with_cache(
            index=it["index"],
            text=it["text"],
            voice=it["voice"],
            out_path=it["out_path"],
            rate=it["rate"],
            pitch=it["pitch"],
            engine_id=it["engine_id"],
            cache_dir=cdir,
            retries=retries,
            skip_existing=skip_existing,
            use_cache=use_cache,
        )
        if _is_rate_limit_error(
            Exception(one["error"]) if one.get("error") else None
        ):
            rate_limit_backs += 1
            workers = max(2, workers - 1)
            stats["tts_concurrency_used"] = workers
            logger.warning(
                "tts_parallel: rate-limit during warmup → concurrency=%d", workers
            )
        _finish(one)

    # B) Remaining via ThreadPoolExecutor.
    if pool_items:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            futs = {
                ex.submit(
                    synthesize_one_with_cache,
                    index=it["index"],
                    text=it["text"],
                    voice=it["voice"],
                    out_path=it["out_path"],
                    rate=it["rate"],
                    pitch=it["pitch"],
                    engine_id=it["engine_id"],
                    cache_dir=cdir,
                    retries=retries,
                    skip_existing=skip_existing,
                    use_cache=use_cache,
                ): it
                for it in pool_items
            }
            for fut in as_completed(futs):
                try:
                    one = fut.result()
                except Exception as exc:
                    it = futs[fut]
                    one = {
                        "index": it["index"],
                        "path": it["out_path"],
                        "cache_hit": False,
                        "skipped_existing": False,
                        "error": str(exc),
                        "retries": retries,
                    }
                if one.get("error") and _is_rate_limit_error(Exception(one["error"])):
                    rate_limit_backs += 1
                _finish(one)

    stats["tts_rate_limit_backs"] = rate_limit_backs
    stats["tts_wall_sec"] = round(time.perf_counter() - t0, 3)
    ordered = [results_by_idx[i] for i in sorted(results_by_idx.keys())]
    logger.info(
        "tts_parallel done n=%d wall=%.2fs concurrency=%d hits=%d misses=%d skips=%d retries=%d",
        stats["tts_segments_total"],
        stats["tts_wall_sec"],
        stats["tts_concurrency_used"],
        stats["tts_cache_hits"],
        stats["tts_cache_misses"],
        stats["tts_skips_existing"],
        stats["tts_retries"],
    )
    return ordered, stats
