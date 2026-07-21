"""TPS / Translation Fast Path v2 — statuses and public enums."""

from __future__ import annotations

from enum import Enum


class TQEStatus(str, Enum):
    PASS = "PASS"
    FAIL_RETRY_MEANING_GRAMMAR = "FAIL_RETRY_MEANING_GRAMMAR"
    FAIL_LLM_JUDGE = "FAIL_LLM_JUDGE"
    FAIL_MANUAL_REVIEW = "FAIL_MANUAL_REVIEW"
    PENDING = "PENDING"


class TPSPath(str, Enum):
    FAST = "fast"
    RETRY = "retry"
    LLM_JUDGE = "llm_judge"
    MANUAL = "manual"
