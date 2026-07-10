"""Tests for agent quality fixes (LLM bootstrap, semantic, grammar, timing)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engines.ai_core.semantic_agent.rule_engine import preserve_dub_entities
from engines.ai_core.grammar_agent.rule_engine import fix_grammar
from engines.ai_core.timing_agent.retry_policy import apply_retry_policy
from engines.ai_core.timing_agent.duration_predictor import predict_duration_ms


def test_preserve_dub_entities_george_jr_not_starshiy():
    out = preserve_dub_entities(
        "Джордж-старший їхав додому.",
        source="George Jr. drove home.",
        tgt_lang="uk",
    )
    assert "старш" not in out.lower()
    assert "молодш" in out


def test_preserve_dub_entities_winning_driver():
    out = preserve_dub_entities(
        "фото переможної швидкості",
        source="photos of the winning driver",
        tgt_lang="uk",
    )
    assert "переможець" in out or "перемож" in out
    assert "швидкість" not in out


def test_grammar_fixes_uk_mt_typos():
    text = "Він ехав і не мог, але уvivi."
    fixed = fix_grammar(text, "uk")
    assert "їхав" in fixed
    assert "мог" not in fixed or "міг" in fixed


def test_timing_aggressive_shorten_after_retries():
    long_text = (
        "Вісімнадцятирічний хлопець на ім'я Джордж-молодший їхав через "
        "рідне місто додому на вечерю з батьками, як завжди."
    )
    slot_ms = 1200
    predicted = predict_duration_ms(long_text, "uk")
    assert predicted > slot_ms

    result = apply_retry_policy(
        long_text,
        source="George Jr. drove home.",
        slot_ms=slot_ms,
        tgt_lang="uk",
        use_llm=False,
        max_attempts=3,
    )
    assert result.attempts == 3
    assert len(result.text) <= len(long_text)
    assert "final_aggressive_shorten" in " ".join(result.decision_log)


def test_llm_bootstrap_calls_begin_run():
    from engines.ai_core.llm_bootstrap import prepare_llm_for_pipeline

    with patch("engines.ai_core.llm_gateway.begin_run") as mock_begin, patch(
        "engines.ai_core.llm_gateway.is_available", return_value=False
    ), patch("engines.ai_core.llm_gateway.active_model", return_value=""), patch(
        "engines.ai_core.llm_gateway.status", return_value={}
    ), patch(
        "engines.translation_adapt.reset_endpoint_cache"
    ):
        status = prepare_llm_for_pipeline("task-xyz", {"target_lang": "uk"})
        mock_begin.assert_called_once()
        assert status["task_id"] == "task-xyz"
