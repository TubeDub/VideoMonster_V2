"""Per-segment confidence scoring for Translation Agent."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SegmentConfidence:
    translation: float = 0.0
    entity: float = 1.0
    terminology: float = 1.0
    language: float = 1.0

    @property
    def overall(self) -> float:
        weights = (0.35, 0.25, 0.20, 0.20)
        scores = (self.translation, self.entity, self.terminology, self.language)
        return round(sum(w * s for w, s in zip(weights, scores)), 4)


def translation_confidence(
    *,
    translator_name: str,
    success: bool,
    attempt: int,
    source: str,
    translated: str,
) -> float:
    if not success or not str(translated or "").strip():
        return 0.0
    # Severe coverage collapse must NEVER look like a confident success
    # (Argos often returns a short fragment of a long paragraph).
    try:
        from engines.mt.sentence_split import is_severe_mt_collapse

        if is_severe_mt_collapse(source, translated):
            return 0.05
    except Exception:
        pass
    src_w = len(str(source or "").split())
    tr_w = len(str(translated or "").split())
    base = 0.85 if translator_name == "cloud" else 0.75
    if translator_name == "deep-translator":
        base = 0.72
    if str(translated).strip() == str(source or "").strip():
        base *= 0.5
    if src_w >= 15 and tr_w > 0:
        ratio = tr_w / max(src_w, 1)
        if ratio < 0.45:
            base *= 0.35
        elif ratio < 0.60:
            base *= 0.7
    attempt_penalty = max(0.0, (attempt - 1) * 0.05)
    return round(max(0.0, min(1.0, base - attempt_penalty)), 4)


def aggregate_confidence(segments: list[dict]) -> dict[str, float]:
    if not segments:
        return {
            "avg_overall": 0.0,
            "avg_translation": 0.0,
            "avg_entity": 0.0,
            "avg_terminology": 0.0,
            "avg_language": 0.0,
        }
    keys = ("confidence", "entity_confidence", "terminology_confidence", "language_confidence")
    sums = [0.0, 0.0, 0.0, 0.0]
    for seg in segments:
        conf = seg.get("confidence") or {}
        if isinstance(conf, dict):
            sums[0] += float(conf.get("overall") or 0)
            sums[1] += float(conf.get("entity") or 1)
            sums[2] += float(conf.get("terminology") or 1)
            sums[3] += float(conf.get("language") or 1)
        else:
            sums[0] += float(seg.get("confidence_overall") or conf or 0)
            sums[1] += float(seg.get("entity_confidence") or 1)
            sums[2] += float(seg.get("terminology_confidence") or 1)
            sums[3] += float(seg.get("language_confidence") or 1)
    n = len(segments)
    return {
        "avg_overall": round(sums[0] / n, 4),
        "avg_translation": round(sums[0] / n, 4),
        "avg_entity": round(sums[1] / n, 4),
        "avg_terminology": round(sums[2] / n, 4),
        "avg_language": round(sums[3] / n, 4),
    }
