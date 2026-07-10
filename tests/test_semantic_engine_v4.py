"""Tests for Translation + Semantic Engine v4.0."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engines.ai_core.entity_dictionary import EntityDictionary
from engines.ai_core.semantic_engine.context_bundle import build_dialogue_context
from engines.ai_core.semantic_engine.quality_audit import (
    SEMANTIC_SCORE_MIN,
    audit_semantic_output,
)
from engines.ai_core.semantic_agent.agent import SemanticAgent


GEORGE_GOLDEN = [
    {
        "text": "An 18-year-old boy named George Jr. drove through his hometown on his way home for dinner.",
        "translated_text": "18-річний Джордж молодший поїхав додому на вечерю через рідне місто.",
    },
    {
        "text": "But, as he was driving, George Jr. could not help but feel like he was really dreading actually getting there.",
        "translated_text": "Але коли Джордж ехав за кермом, він не мог не відчувати, що справді боявся там брати.",
    },
    {
        "text": "he said, George, I know people at USC. Let me make some calls.",
        "translated_text": "Джордж, я знаю людей в Університеті, дозвольте мені зробити кілька дзвінків.",
    },
    {
        "text": "George Jr. would receive an acceptance letter from USC's film school.",
        "translated_text": "Джордж молодший отримає листа від компанії з фільму \"Скарб США.\"",
    },
]


def test_entity_dictionary_fixes_george_jr_and_usc():
    segments = [{"text": row["text"], "index": i} for i, row in enumerate(GEORGE_GOLDEN)]
    ed = EntityDictionary.from_segments(segments, target_lang="uk")
    out = ed.apply(GEORGE_GOLDEN[3]["translated_text"], source=GEORGE_GOLDEN[3]["text"])
    assert "Скарб США" not in out
    assert "USC" in out
    assert ed.accuracy(out, source=GEORGE_GOLDEN[3]["text"]) >= 0.85


def test_dialogue_context_includes_neighbors():
    segments = [
        {"index": 0, "text": "Hello.", "translated_text": "Привіт."},
        {"index": 1, "text": "George Jr. drove home.", "translated_text": "Джордж поїхав."},
        {"index": 2, "text": "He was tired.", "translated_text": "Він був втомлений."},
    ]
    ctx = build_dialogue_context(segments, 1, {"content_type": "documentary"})
    assert "George" in ctx.source
    assert ctx.prev_sources
    assert ctx.next_sources
    assert ctx.topic_hint


def test_quality_audit_flags_literal_mt():
    segments = [{"text": GEORGE_GOLDEN[1]["text"]}]
    ed = EntityDictionary.from_segments(segments, target_lang="uk")
    ctx = build_dialogue_context(
        [{"text": GEORGE_GOLDEN[1]["text"], "translated_text": GEORGE_GOLDEN[1]["translated_text"]}],
        0,
    )
    audit = audit_semantic_output(
        source=GEORGE_GOLDEN[1]["text"],
        machine_translation=GEORGE_GOLDEN[1]["translated_text"],
        semantic_text=GEORGE_GOLDEN[1]["translated_text"],
        dialogue=ctx,
        entity_dict=ed,
        target_lang="uk",
    )
    assert audit.semantic_score < SEMANTIC_SCORE_MIN
    assert audit.issues


def test_semantic_agent_v4_george_golden(tmp_path):
    manifest = {
        "project_uuid": "golden-v4",
        "planner_version": "3.0",
        "source_lang": "en",
        "target_lang": "uk",
    }
    segments = [
        {
            "index": i,
            "text": row["text"],
            "translated_text": row["translated_text"],
            "start": i * 3000,
            "end": (i + 1) * 3000,
        }
        for i, row in enumerate(GEORGE_GOLDEN)
    ]
    agent = SemanticAgent(output_dir=tmp_path / "output")
    with patch("engines.ai_core.llm_gateway.is_available", return_value=False):
        result = agent.run(
            manifest,
            {"segments": segments, "translation_agent_status": "success"},
            "golden-semantic-v4",
        )

    assert result.status in ("success", "warning")
    texts = [s.get("semantic_text", "") for s in result.updated_state["segments"]]
    assert any("USC" in t for t in texts)
    assert not any("Скарб США" in t for t in texts)
    assert not any(" не мог " in t for t in texts)
    report_path = Path(result.updated_state["semantic_quality_report_path"])
    assert report_path.is_file()
