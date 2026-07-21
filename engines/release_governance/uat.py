"""P17.3 — User Acceptance Test scenarios (synthetic product shapes)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from engines.audio_timing_optimizer import optimize_audio_timing


UAT_SCENARIOS = (
    "feature_film",
    "tv_series",
    "interview",
    "podcast",
    "documentary",
    "youtube",
    "short_video",
)


@dataclass
class UATCase:
    name: str
    ok: bool
    naturalness: float
    no_overlap: bool
    sync_ok: bool
    meaning_preserved: bool
    stability_ok: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "naturalness": self.naturalness,
            "no_overlap": self.no_overlap,
            "sync_ok": self.sync_ok,
            "meaning_preserved": self.meaning_preserved,
            "stability_ok": self.stability_ok,
            "detail": self.detail,
        }


@dataclass
class UATReport:
    ok: bool
    cases: list[UATCase] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "cases": [c.to_dict() for c in self.cases]}


def _scenario_rows(name: str) -> list[dict[str, Any]]:
    sizes = {
        "feature_film": 180,
        "tv_series": 90,
        "interview": 45,
        "podcast": 70,
        "documentary": 100,
        "youtube": 30,
        "short_video": 10,
    }
    n = sizes.get(name, 20)
    rows = []
    t = 0
    for i in range(n):
        dur = 400 + (hash(f"{name}-{i}") % 600)
        text = f"{name} dialogue line {i}"
        rows.append(
            {
                "segment_id": f"uat-{name}-{i:04d}",
                "translated_text": text,
                "text": text,
                "start_ms": t,
                "end_ms": t + dur,
                "slot_ms": dur,
                "playback_duration": dur + (i % 40),
                "translation_locked": True,
            }
        )
        t += dur + 12
    return rows


def run_uat_suite() -> UATReport:
    """
    Evaluate product-facing scenarios.

    Scores are derived from optimizer metrics (lock preserves meaning;
    overlap/overflow/sync map to acceptance criteria).
    """
    cases: list[UATCase] = []
    for name in UAT_SCENARIOS:
        rows = _scenario_rows(name)
        locked_texts = [r["translated_text"] for r in rows]
        result = optimize_audio_timing(rows, settings={"uat": name})
        after_texts = [r.get("translated_text") for r in rows]
        meaning_preserved = after_texts == locked_texts
        no_overlap = result.metrics.overlap_count == 0
        # Allow mild overflow marking (Studio path) but flag heavy pressure.
        sync_ok = result.metrics.overflow_count <= max(3, len(rows) // 20)
        naturalness = max(
            0.0,
            1.0
            - (result.metrics.overflow_count + result.metrics.overlap_count) / max(1, len(rows)),
        )
        stability_ok = bool(result.fingerprint) and result.ok
        ok = meaning_preserved and no_overlap and sync_ok and stability_ok and naturalness >= 0.7
        cases.append(
            UATCase(
                name=name,
                ok=ok,
                naturalness=round(naturalness, 4),
                no_overlap=no_overlap,
                sync_ok=sync_ok,
                meaning_preserved=meaning_preserved,
                stability_ok=stability_ok,
                detail=f"overflow={result.metrics.overflow_count} overlap={result.metrics.overlap_count}",
            )
        )
    return UATReport(ok=all(c.ok for c in cases), cases=cases)
