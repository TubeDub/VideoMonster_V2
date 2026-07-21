"""Quality Analyzer — GOOD / MEDIUM / BAD routing for LLM Refiner."""

from __future__ import annotations

import re
from typing import Any

from engines.streamdub.base import ModuleCapabilities, StreamModule
from engines.streamdub.types import QualityGrade, StreamSegment


class QualityAnalyzer(StreamModule):
    module_id = "quality_analyzer"

    def _on_initialize(self, *, app_dir: Any = None, config: dict[str, Any] | None = None) -> None:
        self._app_dir = app_dir

    def _on_health_check(self) -> tuple[bool, str, dict[str, Any] | None]:
        return True, "ready", None

    def capabilities(self) -> ModuleCapabilities:
        return ModuleCapabilities(
            module_id=self.module_id,
            features=[
                "meaning_loss",
                "entity_check",
                "numbers",
                "abbreviations",
                "suspicious_phrases",
            ],
        )

    def _grade(self, score: float, issues: list[str]) -> QualityGrade:
        high = any(
            "entity" in i.lower() or "meaning" in i.lower() or "critical" in i.lower()
            for i in issues
        )
        if score >= 82 and not high:
            return QualityGrade.GOOD
        if score >= 65 and len(issues) <= 2:
            return QualityGrade.MEDIUM
        return QualityGrade.BAD

    def _analyze_segment(self, seg: StreamSegment, src: str, tgt: str) -> None:
        issues: list[str] = []
        score = 75.0

        try:
            from engines.translation_quality_score import compute_quality_score

            score, qd = compute_quality_score(
                seg.text,
                seg.translated or seg.text,
                source_lang=src,
                target_lang=tgt,
            )
            issues.extend(str(w) for w in (qd.get("warnings") or [])[:5])
        except Exception:
            pass

        try:
            from engines.semantic_meaning import verify_meaning_preserved

            ok, reason, _ = verify_meaning_preserved(
                seg.text, seg.text, seg.translated or seg.text, target_lang=tgt
            )
            if not ok and reason:
                issues.append(f"meaning:{reason}")
        except Exception:
            pass

        if re.search(r"\b[A-Z]{2,}\b", seg.text) and not re.search(
            r"\b[A-Z]{2,}\b", seg.translated or ""
        ):
            issues.append("entity:abbreviation_missing")

        if re.search(r"\d+", seg.text) and not re.search(r"\d+", seg.translated or ""):
            issues.append("numbers:missing")

        suspicious = (
            r"ближнього\s+бою|Джер\.|зірвати\s+війни|вигнали\s+з|не\s+отримав\s+одержимості"
        )
        if re.search(suspicious, seg.translated or "", re.IGNORECASE):
            issues.append("suspicious:calque")

        seg.quality_score = round(float(score), 2)
        seg.quality_issues = issues
        seg.quality = self._grade(seg.quality_score, issues)

    def process(self, payload: dict[str, Any]) -> dict[str, Any]:
        segments: list[StreamSegment] = list(payload.get("segments") or [])
        src = str(payload.get("source_lang") or "en")
        tgt = str(payload.get("target_lang") or "uk")

        counts = {g.value: 0 for g in QualityGrade}
        for seg in segments:
            self._analyze_segment(seg, src, tgt)
            if seg.quality:
                counts[seg.quality.value] += 1

        total = max(1, len(segments))
        return {
            "segments": segments,
            "quality_counts": counts,
            "llm_candidates": counts.get("MEDIUM", 0) + counts.get("BAD", 0),
            "fast_only_pct": round(100.0 * counts.get("GOOD", 0) / total, 1),
        }
