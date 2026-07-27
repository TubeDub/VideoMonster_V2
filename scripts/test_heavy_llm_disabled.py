"""
Regression: heavy LLM (Qwen / DeepSeek / Ollama) is OFF by default.

Engine-first policy — dubbing must work on MT + rules + Whisper + Demucs + TTS
without ever calling a generative chat model.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Ensure default (no env override) for the test process.
os.environ.pop("VM_ENABLE_HEAVY_LLM", None)
os.environ.pop("VM_DISABLE_HEAVY_LLM", None)
os.environ.pop("FEATURE_HEAVY_LLM", None)


def test_kill_switch_default_off():
    from engines.llm_kill_switch import is_heavy_llm_disabled

    assert is_heavy_llm_disabled() is True
    print("OK test_kill_switch_default_off")


def test_endpoint_unavailable():
    from engines.llm_adaptation_mode import resolve_llm_endpoint, discover_local_llm

    ep = resolve_llm_endpoint()
    assert ep.get("available") is False
    assert ep.get("provider") == "none"
    assert discover_local_llm(force=True) is None
    print("OK test_endpoint_unavailable")


def test_gateway_blocked():
    from engines.ai_core import llm_gateway
    from engines.translation_adapt import llm_rephrase_available
    from engines.llm_callable import is_llm_callable

    assert llm_rephrase_available() is False
    assert is_llm_callable(quick=True) is False
    assert llm_gateway.is_available() is False
    allowed, reason = llm_gateway.can_call_llm("t", 0)
    assert allowed is False
    assert reason == "heavy_llm_disabled"
    assert llm_gateway.chat("hello", task_id="t", segment_idx=0) is None
    print("OK test_gateway_blocked")


def test_enable_override():
    from engines import llm_kill_switch as ks

    os.environ["VM_ENABLE_HEAVY_LLM"] = "1"
    try:
        assert ks.is_heavy_llm_disabled() is False
    finally:
        os.environ.pop("VM_ENABLE_HEAVY_LLM", None)
        assert ks.is_heavy_llm_disabled() is True
    print("OK test_enable_override")


def test_no_deepseek_qwen_preference_first():
    from engines.llm_adaptation_mode import _MODEL_PREFERENCE
    from engines.llm_providers.registry import DEFAULT_FAMILY_ID, FALLBACK_FAMILY_ORDER

    joined = " ".join(_MODEL_PREFERENCE)
    assert "deepseek" not in joined
    assert "qwen" not in joined
    assert DEFAULT_FAMILY_ID != "deepseek"
    assert FALLBACK_FAMILY_ORDER[0] != "deepseek"
    print("OK test_no_deepseek_qwen_preference_first")


def main() -> int:
    tests = [
        test_kill_switch_default_off,
        test_endpoint_unavailable,
        test_gateway_blocked,
        test_enable_override,
        test_no_deepseek_qwen_preference_first,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
