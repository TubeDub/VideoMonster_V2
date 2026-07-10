"""Micro Validator — per-stage output checks (TZ #5 §2–§3, §9).

Runs after every agent stage and before the result is passed downstream.
Catches damaged lines early so only the broken part is retried — never the
whole chunk or the whole film.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

# Patterns that indicate LLM pollution (§3).
_LLM_BOILERPLATE = re.compile(
    r"(?i)"
    r"(here\s+is\s+the\s+translation|"
    r"here'?s\s+the\s+translation|"
    r"sure[,!]?\s+here|"
    r"certainly[,!]?|"
    r"as\s+requested|"
    r"the\s+translated\s+text\s+is|"
    r"translation\s*:|"
    r"note\s*:|"
    r"explanation\s*:)"
)
_MARKDOWN_FENCE = re.compile(r"```")
_MARKDOWN_BOLD = re.compile(r"\*\*[^*]+\*\*")


@dataclass
class LineIssue:
    """One damaged line inside a chunk."""

    line_index: int
    reason: str
    severity: str = "error"  # error | warning

    def to_dict(self) -> dict[str, Any]:
        return {
            "line_index": self.line_index,
            "reason": self.reason,
            "severity": self.severity,
        }


@dataclass
class ValidationResult:
    """Outcome of a micro-validation pass."""

    ok: bool
    stage: str = ""
    chunk_id: int = -1
    issues: list[LineIssue] = field(default_factory=list)

    @property
    def failed_lines(self) -> list[int]:
        return [i.line_index for i in self.issues if i.severity == "error"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "stage": self.stage,
            "chunk_id": self.chunk_id,
            "issues": [i.to_dict() for i in self.issues],
            "failed_lines": self.failed_lines,
        }


class MicroValidator:
    """Validates chunk output after each pipeline stage."""

    # Stages that require post-check before forwarding (§9).
    VALIDATED_STAGES: tuple[str, ...] = (
        "translator",
        "review",
        "timing",
        "voice",
        "mix",
        "export",
    )

    def validate_stage(self, stage: str, chunk: Any) -> ValidationResult:
        """Run stage-specific validation on a chunk."""
        chunk_id = getattr(chunk, "chunk_id", -1)
        if stage not in self.VALIDATED_STAGES:
            return ValidationResult(ok=True, stage=stage, chunk_id=chunk_id)

        validators = {
            "translator": self._validate_translator,
            "review": self._validate_review,
            "timing": self._validate_timing,
            "voice": self._validate_voice,
            "mix": self._validate_mix,
            "export": self._validate_export,
        }
        fn = validators.get(stage, lambda _c: ValidationResult(ok=True, stage=stage))
        result = fn(chunk)
        result.stage = stage
        result.chunk_id = chunk_id
        return result

    # ── LLM output checks (§3) ───────────────────────────────────────

    def validate_llm_output(
        self,
        text: str,
        *,
        expected_lines: int | None = None,
        expect_json: bool = False,
        line_index: int = -1,
    ) -> ValidationResult:
        """Check a single LLM response before accepting it."""
        issues: list[LineIssue] = []

        if not text or not str(text).strip():
            issues.append(LineIssue(line_index, "empty_response"))
            return ValidationResult(ok=False, issues=issues)

        t = str(text).strip()

        if expect_json:
            try:
                json.loads(t)
            except json.JSONDecodeError as exc:
                issues.append(LineIssue(line_index, f"invalid_json:{exc}"))

        if _LLM_BOILERPLATE.search(t):
            issues.append(LineIssue(line_index, "llm_boilerplate"))

        if _MARKDOWN_FENCE.search(t):
            issues.append(LineIssue(line_index, "markdown_fence"))

        if _MARKDOWN_BOLD.search(t):
            issues.append(LineIssue(line_index, "markdown_bold", severity="warning"))

        # Truncation heuristics.
        if t.endswith("...") or t.endswith("…"):
            issues.append(LineIssue(line_index, "truncated_response"))

        if expected_lines is not None:
            actual = [ln for ln in t.splitlines() if ln.strip()]
            if len(actual) != expected_lines:
                issues.append(
                    LineIssue(
                        line_index,
                        f"line_count_mismatch:expected={expected_lines},got={len(actual)}",
                    )
                )

        errors = [i for i in issues if i.severity == "error"]
        return ValidationResult(ok=len(errors) == 0, issues=issues)

    # ── Per-stage validators (§9) ────────────────────────────────────

    def _validate_translator(self, chunk: Any) -> ValidationResult:
        issues: list[LineIssue] = []
        segs = list(chunk.payload.get("segments") or chunk.source_segments or [])
        expected = len(chunk.source_segments)
        if len(segs) != expected:
            issues.append(
                LineIssue(-1, f"segment_count_mismatch:expected={expected},got={len(segs)}")
            )
        for i, seg in enumerate(segs):
            r = self.validate_llm_output(str(seg), line_index=i)
            issues.extend(r.issues)
        return ValidationResult(ok=not any(i.severity == "error" for i in issues), issues=issues)

    def _validate_review(self, chunk: Any) -> ValidationResult:
        return self._validate_translator(chunk)

    def _validate_timing(self, chunk: Any) -> ValidationResult:
        issues: list[LineIssue] = []
        segs = list(chunk.payload.get("segments") or [])
        tm = list(chunk.payload.get("timing_map") or chunk.timing_map or [])
        if len(segs) != len(tm):
            issues.append(
                LineIssue(-1, f"timing_segment_mismatch:{len(segs)}vs{len(tm)}")
            )
        for i, seg in enumerate(segs):
            if not str(seg).strip():
                issues.append(LineIssue(i, "empty_segment_after_timing"))
        return ValidationResult(ok=not issues, issues=issues)

    def _validate_voice(self, chunk: Any) -> ValidationResult:
        issues: list[LineIssue] = []
        files = list(chunk.payload.get("tts_files") or [])
        segs = list(chunk.payload.get("segments") or chunk.source_segments or [])
        if files and len(files) != len(segs):
            issues.append(
                LineIssue(-1, f"tts_file_count_mismatch:{len(files)}vs{len(segs)}")
            )
        for i, f in enumerate(files):
            if not f:
                issues.append(LineIssue(i, "missing_tts_file"))
        return ValidationResult(ok=not issues, issues=issues)

    def _validate_mix(self, chunk: Any) -> ValidationResult:
        return ValidationResult(ok=True)

    def _validate_export(self, chunk: Any) -> ValidationResult:
        return ValidationResult(ok=True)

    # ── Final integrity check (§12) ──────────────────────────────────

    def verify_integrity(
        self,
        chunks: list[Any],
        *,
        expected_segment_count: int,
        tts_files: list[str] | None = None,
    ) -> ValidationResult:
        """Post-film integrity: all chunks present, no gaps, counts match."""
        issues: list[LineIssue] = []
        if not chunks:
            issues.append(LineIssue(-1, "no_chunks"))
            return ValidationResult(ok=False, stage="integrity", issues=issues)

        all_indices: list[int] = []
        for c in sorted(chunks, key=lambda x: x.chunk_id):
            all_indices.extend(c.segment_indices)

        expected_set = set(range(expected_segment_count))
        actual_set = set(all_indices)
        missing = expected_set - actual_set
        extra = actual_set - expected_set

        if missing:
            issues.append(LineIssue(-1, f"missing_segments:{sorted(missing)}"))
        if extra:
            issues.append(LineIssue(-1, f"extra_segments:{sorted(extra)}"))
        if all_indices != sorted(all_indices):
            issues.append(LineIssue(-1, "segment_order_violation"))

        if tts_files is not None and len(tts_files) != expected_segment_count:
            issues.append(
                LineIssue(
                    -1,
                    f"tts_total_mismatch:expected={expected_segment_count},got={len(tts_files)}",
                )
            )

        return ValidationResult(ok=not issues, stage="integrity", issues=issues)


_validator: MicroValidator | None = None


def get_validator() -> MicroValidator:
    global _validator
    if _validator is None:
        _validator = MicroValidator()
    return _validator
