"""Tests for Peer Validation / Contract Validation Pipeline."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engines.ai_core.peer_validation import (
    PEER_UPSTREAM,
    validate_segment_peer_input,
    validate_upstream_batch,
)


def test_semantic_rejects_empty_translation():
    seg = {"index": 0, "text": "Hello.", "translated_text": ""}
    returns = validate_segment_peer_input("semantic", seg, target_lang="uk")
    assert returns
    assert returns[0].error_code == "missing_translation"
    assert returns[0].receiver_agent == "translation"


def test_semantic_accepts_valid_translation():
    seg = {"index": 0, "text": "Hello.", "translated_text": "Привіт."}
    returns = validate_segment_peer_input("semantic", seg, target_lang="uk")
    assert not returns


def test_timing_requires_semantic_text():
    seg = {"index": 1, "semantic_text": ""}
    returns = validate_segment_peer_input("timing", seg, target_lang="uk")
    assert returns
    assert returns[0].error_code == "missing_semantic_text"
    assert returns[0].receiver_agent == "semantic"


def test_grammar_requires_timing_text():
    seg = {"index": 2, "timing_text": "", "semantic_text": "Текст."}
    returns = validate_segment_peer_input("grammar", seg, target_lang="uk")
    assert returns
    assert returns[0].error_code == "missing_timing_text"


def test_batch_fails_on_upstream_error():
    result = validate_upstream_batch(
        "semantic",
        [{"index": 0, "text": "Hi", "translated_text": "Привіт."}],
        target_lang="uk",
        upstream_status="error",
    )
    assert not result.ok
    assert result.returns[0].error_code == "upstream_agent_failed"


def test_peer_upstream_chain():
    assert PEER_UPSTREAM["semantic"] == "translation"
    assert PEER_UPSTREAM["grammar"] == "timing"
    assert PEER_UPSTREAM["mix"] == "voice_verification"
