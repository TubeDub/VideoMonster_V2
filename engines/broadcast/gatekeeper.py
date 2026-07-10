"""PipelineGateKeeper — token invariant validation between stages."""

from __future__ import annotations

import re
import time
from typing import Any

from engines.broadcast.config import TOKEN_PREFIX, TOKEN_SUFFIX, strict_gate
from engines.broadcast.exceptions import DataCorruptionException

# Canonical broadcast tokens [##123##]
_TOKEN_CANON = re.compile(
    re.escape(TOKEN_PREFIX) + r"(\d+)" + re.escape(TOKEN_SUFFIX)
)
# Damaged variants: [## 123 ##], [##123 ##], etc.
_TOKEN_LOOSE = re.compile(
    r"\[\s*#+\s*(\d+)\s*#+\s*\]",
    re.IGNORECASE,
)


class PipelineGateKeeper:
    """Customs gate — all inter-module data passes through integrity checks."""

    @staticmethod
    def extract_token_ids(text: str, *, loose: bool = False) -> set[int]:
        t = str(text or "")
        ids: set[int] = set()
        for m in _TOKEN_CANON.finditer(t):
            ids.add(int(m.group(1)))
        if loose:
            for m in _TOKEN_LOOSE.finditer(t):
                ids.add(int(m.group(1)))
        return ids

    @staticmethod
    def assert_integrity(
        original_text: str,
        processed_text: str,
        *,
        stage: str = "",
        engine: str = "",
        allow_fuzzy: bool = True,
    ) -> dict[str, Any]:
        """
        Compare token ID sets. Raises DataCorruptionException if strict and mismatch.
        Returns diagnostics; may note fuzzy-correctable damage.
        """
        before = PipelineGateKeeper.extract_token_ids(original_text)
        after = PipelineGateKeeper.extract_token_ids(processed_text)
        after_loose = PipelineGateKeeper.extract_token_ids(processed_text, loose=True) if allow_fuzzy else after

        missing = before - after
        extra = after - before
        fuzzy_fixable = missing - (before - after_loose) if allow_fuzzy else set()

        ok = not missing and not extra
        diag = {
            "stage": stage,
            "engine": engine,
            "ok": ok,
            "ids_before": sorted(before),
            "ids_after": sorted(after),
            "missing": sorted(missing),
            "extra": sorted(extra),
            "fuzzy_fixable": sorted(fuzzy_fixable),
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        if not ok and strict_gate():
            if missing and fuzzy_fixable == missing and allow_fuzzy:
                diag["ok"] = True
                diag["needs_fuzzy_restore"] = True
                return diag
            raise DataCorruptionException(
                f"Token integrity failed at {stage or 'gate'}: "
                f"missing={sorted(missing)} extra={sorted(extra)}",
                stage=stage,
                missing=missing,
                extra=extra,
                engine=engine,
            )
        return diag

    @staticmethod
    def validation_gate(
        masked_input: str,
        engine_output: str,
        *,
        engine_id: str,
        segment_index: int = -1,
    ) -> dict[str, Any]:
        """
        Per-engine gate after MT. Fatal if token count/identity violated.
        Returns gate result with fatal flag.
        """
        t0 = time.perf_counter()
        try:
            diag = PipelineGateKeeper.assert_integrity(
                masked_input,
                engine_output,
                stage="validation_gate",
                engine=engine_id,
                allow_fuzzy=True,
            )
            elapsed_us = (time.perf_counter() - t0) * 1_000_000
            diag["gate_ms"] = round(elapsed_us / 1000.0, 3)
            diag["fatal"] = not diag.get("ok", False)
            diag["segment_index"] = segment_index
            return diag
        except DataCorruptionException as exc:
            elapsed_us = (time.perf_counter() - t0) * 1_000_000
            return {
                "ok": False,
                "fatal": True,
                "stage": "validation_gate",
                "engine": engine_id,
                "segment_index": segment_index,
                "missing": sorted(exc.missing),
                "extra": sorted(exc.extra),
                "error": str(exc),
                "gate_ms": round(elapsed_us / 1000.0, 3),
            }
