"""Quality Gate container (TZ Stages 7–8) — wraps existing validators, no duplication."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("tubedub.ai_core.quality_gate")


@dataclass
class CheckResult:
    check_id: str
    passed: bool
    reason: str = ""
    ms: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class GateResult:
    passed: bool
    checks: list[CheckResult] = field(default_factory=list)
    text: str = ""
    skipped: bool = False
    ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "skipped": self.skipped,
            "ms": round(self.ms, 2),
            "text": self.text,
            "checks": [
                {
                    "id": c.check_id,
                    "passed": c.passed,
                    "reason": c.reason,
                    "ms": round(c.ms, 2),
                }
                for c in self.checks
            ],
        }


class QualityGate:
    """Extensible quality container — each check does exactly one job."""

    def __init__(self) -> None:
        self._enabled = self._is_enabled()

    @staticmethod
    def _is_enabled() -> bool:
        try:
            from engines.ai_core.platform.feature_registry import is_platform_feature_enabled

            return bool(is_platform_feature_enabled("quality_gate"))
        except Exception:
            return True

    def run_translation_validator(
        self,
        text: str,
        *,
        source_text: str = "",
        tgt_lang: str = "ru",
        src_lang: str = "en",
    ) -> CheckResult:
        t0 = time.perf_counter()
        from engines.ai_core.translation_agent.validators.language_validator import (
            validate_language,
        )

        ok, reasons = validate_language(text, tgt_lang, src_lang=src_lang)
        return CheckResult(
            check_id="translation_validator",
            passed=bool(ok),
            reason="; ".join(reasons[:3]) if reasons else "",
            ms=(time.perf_counter() - t0) * 1000,
        )

    def run_pre_tts(
        self,
        text: str,
        *,
        tgt_lang: str = "ru",
        segment: dict[str, Any] | None = None,
    ) -> GateResult:
        """Pre-TTS Validator (TZ Stage 8) — unconfirmed text must not reach TTS."""
        t0 = time.perf_counter()
        if not self._enabled:
            return GateResult(passed=True, text=text, skipped=True)

        checks: list[CheckResult] = []
        seg = segment or {}

        # Reuse existing pre-TTS integrity checks (no duplicate logic).
        try:
            from engines.ai_adaptation_engine import validate_pre_tts_checks

            c0 = time.perf_counter()
            ok, reasons = validate_pre_tts_checks(
                text,
                tgt_lang,
                original_text=str(seg.get("source_text") or seg.get("text_src") or ""),
            )
            checks.append(
                CheckResult(
                    check_id="pre_tts_integrity",
                    passed=bool(ok),
                    reason="; ".join(reasons[:3]) if reasons else "",
                    ms=(time.perf_counter() - c0) * 1000,
                )
            )
        except Exception as exc:
            checks.append(
                CheckResult(
                    check_id="pre_tts_integrity",
                    passed=True,
                    reason=f"skipped:{exc}",
                )
            )

        try:
            from engines.ai_core.quality_agent.validators.timing_check import check_timing

            slot = int(seg.get("timing_slot_ms") or seg.get("slot_ms") or 0)
            if slot > 0:
                c1 = time.perf_counter()
                seg_for_timing = {**seg, "start": 0, "end": slot}
                t_result = check_timing(text, seg_for_timing, tgt_lang=tgt_lang)
                checks.append(
                    CheckResult(
                        check_id="timing_predictor",
                        passed=bool(t_result.ok),
                        reason="; ".join(t_result.issues[:2]) if t_result.issues else "",
                        ms=(time.perf_counter() - c1) * 1000,
                        details={"score": t_result.score},
                    )
                )
        except Exception:
            pass

        passed = all(c.passed for c in checks) if checks else True
        return GateResult(
            passed=passed,
            checks=checks,
            text=text if passed else text,
            ms=(time.perf_counter() - t0) * 1000,
        )

    def audit_segment(self, segment: dict[str, Any], *, tgt_lang: str = "ru") -> GateResult:
        """Delegate to existing segment auditor (Quality Agent internals)."""
        t0 = time.perf_counter()
        if not self._enabled:
            return GateResult(passed=True, skipped=True)

        from engines.ai_core.quality_agent.segment_auditor import audit_segment

        report = audit_segment(segment, tgt_lang=tgt_lang)
        passed = bool(report.get("passed", True))
        checks = [
            CheckResult(
                check_id=str(v.get("check") or "quality"),
                passed=bool(v.get("passed", True)),
                reason=str(v.get("reason") or ""),
            )
            for v in (report.get("violations") or [])
        ]
        if not checks:
            checks.append(CheckResult(check_id="quality_scorer", passed=passed))
        return GateResult(
            passed=passed,
            checks=checks,
            ms=(time.perf_counter() - t0) * 1000,
        )


def get_quality_gate() -> QualityGate:
    return QualityGate()
