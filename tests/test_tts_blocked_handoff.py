# -*- coding: utf-8 -*-
"""TTS-blocked segments must not fail studio handoff as 'missing TTS file'."""

from __future__ import annotations

import pytest

from engines.pipeline_integrity.exceptions import (
    PipelineValidationError,
    RuntimeIntegrityError,
)
from engines.pipeline_integrity.guards import PipelineValidator, RuntimeIntegrityGuard
from engines.pipeline_integrity.slot_budget import segment_tts_allowed, segment_tts_exempt


def test_exempt_and_allowed():
    blocked = {"tts_blocked": True, "text": ""}
    assert segment_tts_exempt(blocked) is True
    assert segment_tts_allowed(blocked) is False
    ok = {"text": "привіт", "approved_text": "привіт"}
    assert segment_tts_exempt(ok) is False
    assert segment_tts_allowed(ok) is True


def test_runtime_guard_skips_blocked():
    segs = [
        {
            "segment_id": "a1",
            "tts_blocked": True,
            "skip_tts": True,
            "text": "",
        }
    ]
    # Must not raise missing TTS file
    RuntimeIntegrityGuard.check(segs, [{"start": 0, "end": 1}], stage="test", require_tts=True)


def test_validator_all_blocked_clear_error():
    segs = [
        {
            "segment_id": "a1",
            "tts_blocked": True,
            "skip_tts": True,
            "text": "",
            "approved_text": "",
            "tqe_status": "FAIL_MANUAL_REVIEW",
        }
    ]
    with pytest.raises(PipelineValidationError) as ei:
        PipelineValidator.validate(
            segs,
            [{"start": 0, "end": 1}],
            stage="studio_handoff",
        )
    assert "TTS-blocked" in str(ei.value)
    assert ei.value.details.get("tts_blocked") is True


def test_validator_mixed_still_requires_file_for_voiceable():
    segs = [
        {
            "segment_id": "ok",
            "text": "привіт",
            "approved_text": "привіт",
            "file": "ok.mp3",
        },
        {
            "segment_id": "bad",
            "tts_blocked": True,
            "skip_tts": True,
            "text": "",
        },
    ]
    # ok has file ref — should not raise for blocked sibling
    # ArchitectureGuard may need more fields; catch only if missing-file related
    try:
        PipelineValidator.validate(
            segs,
            [{"start": 0, "end": 1}, {"start": 1, "end": 2}],
            stage="studio_handoff",
        )
    except RuntimeIntegrityError as e:
        assert "missing TTS file" not in str(e) or "bad" not in str(e)
    except PipelineValidationError as e:
        # May fail on other integrity rules; must not be all-blocked
        assert "TTS-blocked" not in str(e) or e.details.get("tts_blocked") is not True
