"""Disk cache for Whisper STT and translation results (same video re-dub)."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CACHE_VERSION = 2

try:
    from engines.mt.registry import MT_ROUTER_VERSION as ROUTER_VERSION
except Exception:
    ROUTER_VERSION = 3

NATURALIZER_VERSION = 2

try:
    from engines.tps.version import TPS_PIPELINE_VERSION
except Exception:
    TPS_PIPELINE_VERSION = 1


def cache_versions() -> dict[str, int | str]:
    return {
        "v": CACHE_VERSION,
        "router_v": ROUTER_VERSION,
        "naturalizer_v": NATURALIZER_VERSION,
        "tps_v": TPS_PIPELINE_VERSION,
    }


def _cache_root(app_dir: Path) -> Path:
    root = app_dir / "output" / "cache" / "pipeline"
    root.mkdir(parents=True, exist_ok=True)
    return root


def file_fingerprint(path: str, extra: str = "") -> str:
    p = Path(path)
    st = p.stat()
    h = hashlib.sha256()
    h.update(extra.encode("utf-8"))
    h.update(str(st.st_size).encode())
    h.update(str(int(st.st_mtime)).encode())
    with p.open("rb") as f:
        h.update(f.read(2 * 1024 * 1024))
    return h.hexdigest()[:32]


def segments_fingerprint(
    segments: list[str],
    src: str,
    tgt: str,
    *,
    route_label: str = "",
    engine: str = "",
) -> str:
    payload = {
        **cache_versions(),
        "s": segments,
        "src": src,
        "tgt": tgt,
        "route": route_label or "",
        "engine": engine or "",
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if int(data.get("v", 0)) != CACHE_VERSION:
            return None
        if int(data.get("router_v", 0)) != ROUTER_VERSION:
            return None
        if int(data.get("naturalizer_v", 0)) != NATURALIZER_VERSION:
            return None
        if int(data.get("tps_v", 0)) != int(TPS_PIPELINE_VERSION):
            return None
        return data
    except Exception as e:
        logger.debug("cache read failed %s: %s", path, e)
        return None


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def load_whisper_cache(
    app_dir: Path,
    video_path: str,
    *,
    model_size: str,
    source_lang: str | None,
    beam_size: int | None = None,
    compute_type: str | None = None,
    device: str | None = None,
) -> dict[str, Any] | None:
    extra = (
        f"whisper|{model_size}|{source_lang or 'auto'}"
        f"|beam={beam_size or ''}|dev={device or ''}|ct={compute_type or ''}"
    )
    key = file_fingerprint(video_path, extra=extra)
    path = _cache_root(app_dir) / "whisper" / f"{key}.json"
    hit = _read_json(path)
    if hit:
        # Reject sparse caches that leave most of the video undubbed
        timing = hit.get("timing_map") or []
        if timing and not _timing_coverage_ok(timing):
            logger.warning(
                "[Cache] Whisper HIT rejected — sparse coverage key=%s segs=%d",
                key,
                len(timing),
            )
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
            return None
        logger.info("[Cache] Whisper HIT key=%s", key)
    return hit


def _timing_coverage_ok(timing_map: list, *, min_span_ratio: float = 0.35) -> bool:
    """True when STT slots span a meaningful portion of the detected speech window."""
    if not timing_map:
        return False
    starts, ends = [], []
    for t in timing_map:
        if isinstance(t, dict):
            s, e = int(t.get("start", 0) or 0), int(t.get("end", 0) or 0)
        elif isinstance(t, (list, tuple)) and len(t) >= 2:
            s, e = int(t[0]), int(t[1])
        else:
            continue
        if e > s:
            starts.append(s)
            ends.append(e)
    if not starts:
        return False
    # Single tiny island on a long video is never OK
    if len(starts) == 1 and (ends[0] - starts[0]) < 8000:
        return False
    span = max(ends) - min(starts)
    # Prefer multi-segment; allow single long block covering >= 20s
    if len(starts) >= 3:
        return True
    return span >= 20000


def save_whisper_cache(
    app_dir: Path,
    video_path: str,
    *,
    model_size: str,
    source_lang: str | None,
    source_text: str,
    timing_map: list,
    detected_lang: str,
    beam_size: int | None = None,
    compute_type: str | None = None,
    device: str | None = None,
) -> None:
    extra = (
        f"whisper|{model_size}|{source_lang or 'auto'}"
        f"|beam={beam_size or ''}|dev={device or ''}|ct={compute_type or ''}"
    )
    key = file_fingerprint(video_path, extra=extra)
    path = _cache_root(app_dir) / "whisper" / f"{key}.json"
    _write_json(
        path,
        {
            **cache_versions(),
            "source_text": source_text,
            "timing_map": timing_map,
            "detected_lang": detected_lang,
            "model_size": model_size,
            "beam_size": beam_size,
            "device": device,
            "compute_type": compute_type,
        },
    )
    logger.info("[Cache] Whisper SAVE key=%s", key)


def load_translate_cache(
    app_dir: Path,
    segments: list[str],
    src_lang: str,
    tgt_lang: str,
) -> list[str] | None:
    key = segments_fingerprint(segments, src_lang, tgt_lang)
    path = _cache_root(app_dir) / "translate" / f"{key}.json"
    hit = _read_json(path)
    if hit and isinstance(hit.get("segments"), list):
        logger.info(
            "[Cache] Translation HIT key=%s route=%s",
            key,
            hit.get("route_label", "?"),
        )
        return [str(s) for s in hit["segments"]]
    return None


def save_translate_cache(
    app_dir: Path,
    segments: list[str],
    src_lang: str,
    tgt_lang: str,
    translated: list[str],
    *,
    route_label: str = "",
    engine: str = "",
    quality_score: float = 0.0,
) -> None:
    # Key must match load_translate_cache (src+tgt+segments only) or warm hits never fire.
    key = segments_fingerprint(segments, src_lang, tgt_lang)
    path = _cache_root(app_dir) / "translate" / f"{key}.json"
    _write_json(
        path,
        {
            **cache_versions(),
            "segments": translated,
            "route_label": route_label,
            "engine": engine,
            "quality_score": quality_score,
            "src": src_lang,
            "tgt": tgt_lang,
        },
    )
    logger.info("[Cache] Translation SAVE key=%s route=%s", key, route_label or "direct")
