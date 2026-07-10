"""Shared pytest fixtures for TubeDub."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session", autouse=True)
def _test_env() -> None:
    os.environ.setdefault("VM_DEV_MODE", "1")
    os.environ.setdefault("VM_PREPARE_WARMUP", "0")


@pytest.fixture(autouse=True)
def _reset_llm_budget() -> None:
    """Isolate the global LLM circuit-breaker state between tests.

    A real local LLM running on the dev box can otherwise trip the breaker in
    one test and leak the open state into unrelated tests.
    """
    try:
        from engines.translation_adapt import reset_circuit_breaker, reset_llm_budget

        reset_llm_budget()
        reset_circuit_breaker()
    except Exception:
        pass
    try:
        from engines.ai_core import llm_gateway

        llm_gateway._circuit_opened_at = None  # type: ignore[attr-defined]
        llm_gateway._llm_decisions.clear()  # type: ignore[attr-defined]
    except Exception:
        pass
    yield
    try:
        from engines.translation_adapt import reset_circuit_breaker, reset_llm_budget

        reset_llm_budget()
        reset_circuit_breaker()
    except Exception:
        pass
    try:
        from engines.ai_core import llm_gateway

        llm_gateway._circuit_opened_at = None  # type: ignore[attr-defined]
        llm_gateway._llm_decisions.clear()  # type: ignore[attr-defined]
    except Exception:
        pass


@pytest.fixture
def app_dir() -> Path:
    return ROOT
