"""Tests for AI Core timeout policy."""

from engines.ai_core.timeout_policy import resolve_agent_timeout


def test_resolve_agent_timeout_scales_with_segments():
    base = resolve_agent_timeout("translation", 120, {"segments": []})
    big = resolve_agent_timeout("translation", 120, {"segments": [{}] * 300})
    assert big >= base
    assert big >= 300


def test_resolve_agent_timeout_ignores_mix():
    t = resolve_agent_timeout("mix", 180, {"segments": [{}] * 500})
    assert t >= 180
