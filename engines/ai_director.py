"""AI Director — post-pipeline validation checklist (TZ §3)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Sequence

_PLACEHOLDER_RE = re.compile(
    r"\{\d+\}|\[PLACEHOLDER\]|<\w+>|TODO|TBD|___+",
    re.IGNORECASE,
)


@dataclass
class QualityIssue:
    code: str
    message: str
    severity: str = "warning"
    segment_index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
        }
        if self.segment_index is not None:
            d["segment_index"] = self.segment_index
        return d


@dataclass
class QualityScore:
    score: float
    block_export: bool = False
    issues: list[QualityIssue] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 2),
            "block_export": self.block_export,
            "issues": [i.to_dict() for i in self.issues],
            "checks": dict(self.checks),
            "issue_count": len(self.issues),
        }


def _timing_start(item: Any) -> int:
    if isinstance(item, dict):
        return int(item.get("start", item.get("start_ms", 0)))
    if isinstance(item, (list, tuple)) and item:
        return int(item[0])
    return 0


def _timing_end(item: Any) -> int:
    if isinstance(item, dict):
        return int(item.get("end", item.get("end_ms", 0)))
    if isinstance(item, (list, tuple)) and len(item) >= 2:
        return int(item[1])
    return 0


def validate_pipeline(
    *,
    source_segments: Sequence[str],
    translated_segments: Sequence[str],
    timing_map: Sequence[Any],
    word_maps: Sequence[dict[str, Any]] | None = None,
    timing_warnings: Sequence[str] | None = None,
    min_score_to_export: float = 0.55,
) -> QualityScore:
    issues: list[QualityIssue] = []
    checks: dict[str, bool] = {}
    n = max(len(source_segments), len(translated_segments), len(timing_map))

    # Meaning length ratio
    length_ok = True
    for i in range(min(len(source_segments), len(translated_segments))):
        src = str(source_segments[i] or "").strip()
        tgt = str(translated_segments[i] or "").strip()
        if not src or not tgt:
            continue
        ratio = len(tgt) / max(len(src), 1)
        if ratio < 0.25 or ratio > 3.5:
            length_ok = False
            issues.append(
                QualityIssue(
                    code="length_ratio",
                    message=f"Длина перевода {ratio:.2f}× оригинала (сегмент {i})",
                    severity="warning",
                    segment_index=i,
                )
            )
    checks["meaning_length"] = length_ok

    # Overlaps and gaps in timing map
    overlap_ok = True
    gap_issues = 0
    prev_end = 0
    for i, slot in enumerate(timing_map):
        start, end = _timing_start(slot), _timing_end(slot)
        if end < start:
            overlap_ok = False
            issues.append(
                QualityIssue(
                    code="invalid_slot",
                    message=f"Некорректный слот: end < start ({i})",
                    severity="error",
                    segment_index=i,
                )
            )
        if start < prev_end:
            overlap_ok = False
            issues.append(
                QualityIssue(
                    code="overlap",
                    message=f"Пересечение слотов на {prev_end - start}ms (сегмент {i})",
                    severity="warning",
                    segment_index=i,
                )
            )
        gap = start - prev_end
        if gap > 8000:
            gap_issues += 1
            issues.append(
                QualityIssue(
                    code="large_gap",
                    message=f"Большая пауза {gap}ms перед сегментом {i}",
                    severity="info",
                    segment_index=i,
                )
            )
        prev_end = max(prev_end, end)
    checks["no_overlap"] = overlap_ok
    checks["gaps_ok"] = gap_issues < 3

    # Placeholders
    placeholder_ok = True
    for i, tgt in enumerate(translated_segments):
        t = str(tgt or "")
        if _PLACEHOLDER_RE.search(t):
            placeholder_ok = False
            issues.append(
                QualityIssue(
                    code="placeholder",
                    message=f"Placeholder в переводе сегмента {i}",
                    severity="error",
                    segment_index=i,
                )
            )
    checks["no_placeholders"] = placeholder_ok

    # Word map coverage (when provided)
    word_ok = True
    if word_maps:
        for wm in word_maps:
            words = wm.get("words") or []
            if not words:
                word_ok = False
                issues.append(
                    QualityIssue(
                        code="empty_word_map",
                        message=f"Пустая word map сегмента {wm.get('segment_index', '?')}",
                        severity="info",
                    )
                )
    checks["word_maps"] = word_ok

    if timing_warnings:
        for w in timing_warnings[:10]:
            issues.append(
                QualityIssue(code="timing_warning", message=str(w), severity="warning")
            )

    errors = sum(1 for i in issues if i.severity == "error")
    warnings = sum(1 for i in issues if i.severity == "warning")
    base = 1.0
    base -= errors * 0.15
    base -= warnings * 0.05
    base -= gap_issues * 0.02
    score = max(0.0, min(1.0, base))
    block = errors > 0 and not placeholder_ok or score < min_score_to_export and errors > 0

    return QualityScore(
        score=score,
        block_export=block,
        issues=issues,
        checks=checks,
    )


def format_report(report: QualityScore) -> str:
    lines = [
        f"AI Director — Quality Score: {report.score:.0%}",
        f"Block export: {report.block_export}",
        "",
        "Checks:",
    ]
    for k, v in report.checks.items():
        lines.append(f"  [{'OK' if v else 'FAIL'}] {k}")
    if report.issues:
        lines.append("")
        lines.append("Issues:")
        for iss in report.issues[:50]:
            loc = f" seg={iss.segment_index}" if iss.segment_index is not None else ""
            lines.append(f"  [{iss.severity}] {iss.code}{loc}: {iss.message}")
    return "\n".join(lines)


def is_ai_director_enabled() -> bool:
    from engines.core.feature_flags import is_enabled

    return is_enabled("ai_director", developer_session=True)
