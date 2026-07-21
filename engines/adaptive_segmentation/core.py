"""Adaptive Segmentation 2.0 — dub-oriented split/merge/balance (pre-MT).

Whisper remains the source of truth for speech presence; this module reshapes
segment *structure* for translation/TTS fit without rewriting Whisper/TTS/DSAL.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from engines.adaptive_segmentation.config import AdaptiveSegConfig, load_adaptive_seg_config

logger = logging.getLogger("tubedub.adaptive_segmentation")

# Split priority (TZ §3) — never treat Jr./Mr./Dr. periods as sentence ends
_SENTENCE_SPLIT = re.compile(
    r"(?<!\bMr)(?<!\bMrs)(?<!\bMs)(?<!\bDr)(?<!\bProf)"
    r"(?<!\bJr)(?<!\bSr)(?<!\bvs)(?<!\betc)"
    r"(?<=[.!?…])\s+"
)
_SEMI_SPLIT = re.compile(r"(?<=[;:])\s+")
# Coordinating conjunctions only — NEVER relative "that/when/which/while"
# (those produced Whisper-like false cuts: "And at" | "that point …").
_CONJ_SPLIT = re.compile(
    r"\s+(?=(?:and|but|so|because|"
    r"і|але|тож|тому що|тому)\s+)",
    re.I,
)
_WORD_RE = re.compile(r"[\w\u0400-\u04FF'-]+", re.UNICODE)
_ABBREV_TOKEN_RE = re.compile(
    r"(?i)\b(?:mr|mrs|ms|dr|prof|jr|sr)\.$"
)


@dataclass
class SegUnit:
    text: str
    start_ms: int
    end_ms: int
    actions: list[str] = field(default_factory=list)

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)

    def to_timing(self) -> dict[str, int]:
        return {"start": int(self.start_ms), "end": int(self.end_ms)}


@dataclass
class AdaptiveSegResult:
    segments: list[str]
    timing_map: list[dict[str, int]]
    report: dict[str, Any]
    changed: bool


def _t_start(item: Any) -> int:
    if isinstance(item, dict):
        return int(item.get("start", 0))
    if isinstance(item, (list, tuple)) and item:
        return int(item[0])
    return 0


def _t_end(item: Any) -> int:
    if isinstance(item, dict):
        return int(item.get("end", 0))
    if isinstance(item, (list, tuple)) and len(item) >= 2:
        return int(item[1])
    return 0


def _word_count(text: str) -> int:
    return len(_WORD_RE.findall(str(text or "")))


def estimate_expected_tts_ms(
    text: str,
    *,
    src_lang: str = "en",
    tgt_lang: str = "uk",
    slot_ms: int = 0,
    expand: float = 1.18,
) -> int:
    """Pre-MT forecast of dubbed speech duration."""
    t = str(text or "").strip()
    if not t:
        return 0
    try:
        from engines.semantic_adaptation import estimate_tts_duration_ms

        # Source-language estimate × expansion ≈ target TTS length
        base = int(estimate_tts_duration_ms(t, (src_lang or "en").split("-")[0]) or 0)
        if base <= 0:
            base = int(len(t) / 14.5 * 1000)
        return max(1, int(base * float(expand)))
    except Exception:
        cps = 13.5 if (tgt_lang or "").startswith("uk") else 14.5
        return max(1, int(len(t) / cps * 1000 * float(expand)))


def _safe_split_chunks(text: str) -> list[str]:
    """Split by sentence → ;/: → conjunctions; never return empty."""
    t = " ".join(str(text or "").split()).strip()
    if not t:
        return []
    parts = [p.strip() for p in _SENTENCE_SPLIT.split(t) if p.strip()]
    # Re-join false splits after Jr./Mr. (regex lookbehind is fixed-width fragile)
    if len(parts) > 1:
        joined: list[str] = []
        buf = parts[0]
        for nxt in parts[1:]:
            if _ABBREV_TOKEN_RE.search(buf.rstrip()) or (
                nxt and nxt[0:1].islower()
            ):
                buf = f"{buf} {nxt}".strip()
            else:
                joined.append(buf)
                buf = nxt
        joined.append(buf)
        parts = joined
    if len(parts) <= 1:
        parts = [p.strip() for p in _SEMI_SPLIT.split(t) if p.strip()]
    if len(parts) <= 1 and len(t) > 80:
        parts = [p.strip() for p in _CONJ_SPLIT.split(t) if p.strip()]
        # Drop cuts that leave a discourse-opener stub ("And at", "So two …").
        if len(parts) > 1:
            repaired: list[str] = []
            buf = parts[0]
            for nxt in parts[1:]:
                left_words = len(buf.split())
                if left_words <= 3 or (
                    left_words <= 5 and buf[-1:] not in ".!?…"
                    and not re.search(r"[.!?…]\s+\S", buf)
                ):
                    buf = f"{buf} {nxt}".strip()
                else:
                    repaired.append(buf)
                    buf = nxt
            repaired.append(buf)
            parts = repaired
    return parts if parts else [t]


def _must_join(left: str, right: str, *, use_meaning: bool) -> bool:
    """True if cutting between left|right is forbidden (join required).

    Mirrors smart_segmentation.would_break_forbidden — do NOT invert this
    for merge/rebalance (joining is encouraged when True).
    """
    if not use_meaning:
        return False
    try:
        from engines.smart_segmentation import would_break_forbidden

        bad, _ = would_break_forbidden(left, right)
        return bool(bad)
    except Exception:
        # Heuristic fallback
        if re.search(r"\d+\s*$", left) and re.match(r"(?i)(km|kg|%|jr\.?)\b", right):
            return True
        if re.search(r"[A-ZА-ЯІЇЄҐ][a-zа-яіїєґ]+\s*$", left) and re.match(
            r"^[A-ZА-ЯІЇЄҐ][a-zа-яіїєґ]+\b", right
        ):
            return True
        return False


def _forbidden_cut(left: str, right: str, *, use_meaning: bool) -> bool:
    """True if a proposed split between left|right should be rejected."""
    return _must_join(left, right, use_meaning=use_meaning)


def _allocate_times(
    chunks: list[str], start_ms: int, end_ms: int
) -> list[tuple[str, int, int]]:
    """Proportional time allocation by character weight (natural, not fixed ms)."""
    span = max(1, end_ms - start_ms)
    weights = [max(1, len(c)) for c in chunks]
    total_w = sum(weights) or 1
    out: list[tuple[str, int, int]] = []
    cursor = start_ms
    for i, (chunk, w) in enumerate(zip(chunks, weights)):
        if i == len(chunks) - 1:
            piece_end = end_ms
        else:
            piece_end = cursor + max(400, int(span * (w / total_w)))
            piece_end = min(piece_end, end_ms - 200 * (len(chunks) - i - 1))
        out.append((chunk, cursor, max(cursor + 300, piece_end)))
        cursor = out[-1][2]
    if out:
        # Snap last end
        c, s, _e = out[-1]
        out[-1] = (c, s, end_ms)
    return out


def _units_from_pipeline(
    segments: list[str], timing_map: list[Any]
) -> list[SegUnit]:
    n = min(len(segments), len(timing_map)) if timing_map else len(segments)
    units: list[SegUnit] = []
    for i in range(n):
        text = str(segments[i] or "").strip()
        if not text:
            continue
        if timing_map and i < len(timing_map):
            s, e = _t_start(timing_map[i]), _t_end(timing_map[i])
        else:
            s, e = i * 3000, (i + 1) * 3000
        if e <= s:
            e = s + max(1000, len(text) * 60)
        units.append(SegUnit(text=text, start_ms=s, end_ms=e))
    return units


def _split_long_unit(unit: SegUnit, cfg: AdaptiveSegConfig) -> list[SegUnit]:
    if unit.duration_ms <= cfg.max_ms and (
        not cfg.use_tts_forecast
        or estimate_expected_tts_ms(unit.text, expand=cfg.translation_expand)
        <= int(cfg.max_ms * 1.15)
    ):
        # Still split if text is huge relative to preferred even when slot ok
        if unit.duration_ms <= cfg.soft_max_ms or _word_count(unit.text) < 55:
            return [unit]

    chunks = _safe_split_chunks(unit.text)
    if len(chunks) <= 1:
        # Force soft split by length when no punctuation
        words = unit.text.split()
        # Very long slots: allow force-split with fewer words (Whisper monologue)
        min_words = 8 if unit.duration_ms >= cfg.max_ms * 1.5 else 12
        if len(words) < min_words:
            return [unit]
        mid = max(3, len(words) // 2)
        # Prefer break near conjunction
        for off in range(0, min(8, mid)):
            for idx in (mid - off, mid + off):
                if 3 <= idx < len(words) - 3:
                    left = " ".join(words[:idx]).rstrip(",;")
                    right = " ".join(words[idx:])
                    if not _forbidden_cut(left, right, use_meaning=cfg.use_meaning):
                        chunks = [left, right]
                        break
            if len(chunks) > 1:
                break
        if len(chunks) <= 1:
            return [unit]

    # Merge tiny sentence crumbs back before allocating times
    merged_chunks: list[str] = []
    buf = chunks[0]
    for nxt in chunks[1:]:
        if _word_count(buf) < 4 or (
            cfg.use_meaning and _forbidden_cut(buf, nxt, use_meaning=True)
        ):
            buf = f"{buf} {nxt}".strip()
        else:
            merged_chunks.append(buf)
            buf = nxt
    merged_chunks.append(buf)

    # Target ~preferred duration pieces
    target_pieces = max(
        2, min(len(merged_chunks), int(round(unit.duration_ms / max(cfg.preferred_ms, 1))))
    )
    if len(merged_chunks) > target_pieces + 1 and cfg.aggressiveness >= 0.4:
        # Group consecutive chunks into ~target_pieces buckets
        grouped: list[str] = []
        per = max(1, len(merged_chunks) // target_pieces)
        i = 0
        while i < len(merged_chunks):
            grouped.append(" ".join(merged_chunks[i : i + per]).strip())
            i += per
        merged_chunks = [g for g in grouped if g]

    allocated = _allocate_times(merged_chunks, unit.start_ms, unit.end_ms)
    out: list[SegUnit] = []
    for text, s, e in allocated:
        out.append(
            SegUnit(text=text, start_ms=s, end_ms=e, actions=["split_long"])
        )
    return out if out else [unit]


def _merge_short_units(units: list[SegUnit], cfg: AdaptiveSegConfig) -> list[SegUnit]:
    if not units:
        return []
    out: list[SegUnit] = []
    i = 0
    while i < len(units):
        cur = units[i]
        if (
            cur.duration_ms < cfg.min_ms
            and i + 1 < len(units)
            and cfg.aggressiveness >= 0.25
        ):
            nxt = units[i + 1]
            combined_ms = nxt.end_ms - cur.start_ms
            gap = max(0, nxt.start_ms - cur.end_ms)
            forecast = estimate_expected_tts_ms(
                f"{cur.text} {nxt.text}", expand=cfg.translation_expand
            )
            must_join = _must_join(
                cur.text, nxt.text, use_meaning=cfg.use_meaning
            )
            # Duration gates; must-join (Jr./lowercase) may exceed max slightly.
            dur_limit = int(cfg.max_ms * 1.75) if must_join else cfg.max_ms
            forecast_limit = int(dur_limit * 1.15)
            can_merge = (
                combined_ms <= dur_limit
                and gap <= 1800
                and forecast <= forecast_limit
            )
            # Prefer merge when next is also short/medium, or join is required
            if can_merge and (
                must_join
                or nxt.duration_ms < cfg.soft_max_ms
                or cur.duration_ms < cfg.min_ms * 0.7
            ):
                joined = SegUnit(
                    text=f"{cur.text} {nxt.text}".strip(),
                    start_ms=cur.start_ms,
                    end_ms=nxt.end_ms,
                    actions=list(cur.actions)
                    + list(nxt.actions)
                    + (["merge_must_join"] if must_join else ["merge_short"]),
                )
                out.append(joined)
                i += 2
                continue
        out.append(cur)
        i += 1
    return out


def _rebalance_neighbors(units: list[SegUnit], cfg: AdaptiveSegConfig) -> list[SegUnit]:
    """Smooth short–long–short patterns by pulling sentence parts across edges."""
    if len(units) < 2 or cfg.aggressiveness < 0.35:
        return units
    out = list(units)
    changed = True
    guard = 0
    while changed and guard < 6:
        changed = False
        guard += 1
        i = 0
        while i < len(out) - 1:
            a, b = out[i], out[i + 1]
            # Classic Whisper damage: short A + long B (± trailing short later)
            short_long = (
                a.duration_ms < cfg.soft_min_ms
                and b.duration_ms > cfg.soft_max_ms
            )
            # Also: long A + short B → push last sentence of A into B
            long_short = (
                a.duration_ms > cfg.soft_max_ms
                and b.duration_ms < cfg.soft_min_ms
            )
            if short_long:
                parts = _safe_split_chunks(b.text)
                if len(parts) >= 2:
                    move = parts[0]
                    rest = " ".join(parts[1:]).strip()
                    # Sentence-boundary pull is always allowed for dub balance;
                    # must_join only affects split rejection elsewhere.
                    if rest:
                        move_ms = max(
                            800,
                            int(
                                b.duration_ms
                                * (len(move) / max(1, len(b.text)))
                            ),
                        )
                        # Cap so A does not become a new oversized block
                        new_a_end = min(
                            b.end_ms - 800,
                            a.end_ms + move_ms,
                            a.start_ms + cfg.max_ms,
                        )
                        if new_a_end > a.end_ms + 400 and new_a_end < b.end_ms - 400:
                            out[i] = SegUnit(
                                text=f"{a.text} {move}".strip(),
                                start_ms=a.start_ms,
                                end_ms=new_a_end,
                                actions=a.actions + ["rebalance_pull"],
                            )
                            out[i + 1] = SegUnit(
                                text=rest,
                                start_ms=new_a_end,
                                end_ms=b.end_ms,
                                actions=b.actions + ["rebalance_give"],
                            )
                            changed = True
                            continue
            elif long_short:
                parts = _safe_split_chunks(a.text)
                if len(parts) >= 2:
                    keep = " ".join(parts[:-1]).strip()
                    move = parts[-1]
                    if keep and move and not _forbidden_cut(
                        keep, move, use_meaning=cfg.use_meaning
                    ):
                        move_ms = max(
                            800,
                            int(
                                a.duration_ms
                                * (len(move) / max(1, len(a.text)))
                            ),
                        )
                        new_a_end = max(
                            a.start_ms + 800,
                            a.end_ms - move_ms,
                            b.end_ms - cfg.max_ms,
                        )
                        if new_a_end < a.end_ms - 400 and new_a_end > a.start_ms + 400:
                            out[i] = SegUnit(
                                text=keep,
                                start_ms=a.start_ms,
                                end_ms=new_a_end,
                                actions=a.actions + ["rebalance_give"],
                            )
                            out[i + 1] = SegUnit(
                                text=f"{move} {b.text}".strip(),
                                start_ms=new_a_end,
                                end_ms=b.end_ms,
                                actions=b.actions + ["rebalance_pull"],
                            )
                            changed = True
                            continue
            i += 1
    return out


def _stats(units: list[SegUnit]) -> dict[str, Any]:
    durs = [u.duration_ms for u in units if u.duration_ms > 0]
    if not durs:
        return {
            "count": 0,
            "min_ms": 0,
            "max_ms": 0,
            "avg_ms": 0,
            "spread_ratio": 0.0,
        }
    mn, mx = min(durs), max(durs)
    return {
        "count": len(units),
        "min_ms": mn,
        "max_ms": mx,
        "avg_ms": int(sum(durs) / len(durs)),
        "spread_ratio": round(mx / max(mn, 1), 2),
    }


def segment_recommendation(
    *,
    slot_ms: int,
    expected_tts_ms: int,
    cfg: AdaptiveSegConfig | None = None,
) -> dict[str, Any]:
    """UI helper — Split / Merge / Excellent."""
    c = cfg or load_adaptive_seg_config()
    slot = max(0, int(slot_ms or 0))
    exp = max(0, int(expected_tts_ms or 0))
    fill = round((exp / slot) * 100.0, 1) if slot > 0 and exp > 0 else 0.0
    status = "Excellent"
    advice = ""
    if slot >= c.max_ms or exp > int(c.max_ms * 1.1):
        status = "Needs Split"
        advice = "Split Recommended"
    elif slot > 0 and slot < c.min_ms:
        status = "Too Short"
        advice = "Merge Recommended"
    elif fill > 105:
        status = "Needs Split"
        advice = "Split Recommended"
    elif fill > 95:
        status = "Near Limit"
        advice = ""
    return {
        "status": status,
        "advice": advice,
        "fill_pct": fill,
        "slot_ms": slot,
        "expected_tts_ms": exp,
    }


def adapt_source_segments(
    segments: list[str],
    timing_map: list[Any] | None,
    *,
    src_lang: str = "en",
    tgt_lang: str = "uk",
    config: AdaptiveSegConfig | None = None,
    overrides: dict[str, Any] | None = None,
) -> AdaptiveSegResult:
    """
    Main entry: Whisper/merged segments → dub-friendly segments + timing.
    """
    cfg = config or load_adaptive_seg_config(overrides=overrides)
    texts = [str(s or "").strip() for s in (segments or []) if str(s or "").strip()]
    if not texts:
        return AdaptiveSegResult([], [], {"enabled": cfg.enabled, "changed": False}, False)

    if not cfg.enabled:
        tm = [
            {"start": _t_start(timing_map[i]), "end": _t_end(timing_map[i])}
            for i in range(min(len(texts), len(timing_map or [])))
        ] if timing_map else []
        return AdaptiveSegResult(texts, tm, {"enabled": False, "changed": False}, False)

    before = list(texts)
    units = _units_from_pipeline(texts, list(timing_map or []))

    # Pass 1 — split long
    split_units: list[SegUnit] = []
    for u in units:
        split_units.extend(_split_long_unit(u, cfg))

    # Pass 2 — merge short
    merged = _merge_short_units(split_units, cfg)

    # Pass 3 — neighbor balance
    balanced = _rebalance_neighbors(merged, cfg)

    # Pass 4 — if spread still huge, another split pass
    st = _stats(balanced)
    if st.get("spread_ratio", 0) > cfg.max_spread_ratio:
        again: list[SegUnit] = []
        for u in balanced:
            again.extend(_split_long_unit(u, cfg))
        balanced = _merge_short_units(again, cfg)
        st = _stats(balanced)

    out_texts = [u.text for u in balanced]
    out_timing = [u.to_timing() for u in balanced]
    changed = out_texts != before or len(out_texts) != len(before)

    actions: list[str] = []
    for u in balanced:
        actions.extend(u.actions)

    report = {
        "enabled": True,
        "changed": changed,
        "before_count": len(before),
        "after_count": len(out_texts),
        "stats_before": _stats(units),
        "stats_after": st,
        "actions": sorted(set(actions)),
        "config": cfg.to_dict(),
        "src_lang": src_lang,
        "tgt_lang": tgt_lang,
    }
    if changed:
        logger.info(
            "[AdaptiveSeg] %d→%d segs avg=%sms max=%sms spread=%s actions=%s",
            report["before_count"],
            report["after_count"],
            st.get("avg_ms"),
            st.get("max_ms"),
            st.get("spread_ratio"),
            report["actions"],
        )
    return AdaptiveSegResult(out_texts, out_timing, report, changed)
