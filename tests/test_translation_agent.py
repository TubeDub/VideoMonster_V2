"""Tests for TubeDub Translation Agent v1.0."""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engines.ai_core.translation_agent.agent import TranslationAgent, load_manifest
from engines.ai_core.translation_agent.confidence import SegmentConfidence
from engines.ai_core.translation_agent.retry_policy import translate_with_fallback
from engines.ai_core.translation_agent.translator_interface import (
    BaseTranslator,
    TranslatorRegistry,
)
from engines.ai_core.translation_agent.validators.entity_validator import (
    extract_entities,
    validate_entities,
)


class _FakeTranslator(BaseTranslator):
    def __init__(self, name: str, output_suffix: str = "", available: bool = True):
        self.name = name
        self._suffix = output_suffix
        self._available = available

    def is_available(self) -> bool:
        return self._available

    def translate(self, text: str, source: str, target: str) -> str:
        return f"{text}{self._suffix}"


def _manifest(tmp_path: Path) -> dict:
    project_uuid = str(uuid.uuid4())
    manifest_dir = tmp_path / "manifests" / project_uuid
    manifest_dir.mkdir(parents=True)
    manifest = {
        "project_uuid": project_uuid,
        "planner_version": "3.0",
        "source_lang": "en",
        "target_lang": "uk",
        "capability_matrix": {"llm": False, "ffmpeg": True},
        "success_criteria": {"translate": {"segments_min": 1}},
        "agent_dependencies": {"translate": ["stt"]},
    }
    path = manifest_dir / "project_manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def _segments(texts: list[str]) -> list[dict]:
    return [
        {"index": i, "text": t, "start": i * 1000, "end": (i + 1) * 1000}
        for i, t in enumerate(texts)
    ]


@pytest.fixture
def agent(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "engines.ai_core.translation_agent.agent._MANIFESTS_DIR",
        tmp_path / "manifests",
    )
    monkeypatch.setattr(
        "engines.ai_core.translation_agent.agent._OUTPUT_DIR",
        tmp_path,
    )
    return TranslationAgent(output_dir=tmp_path)


def test_raw_translation_only_no_shortening(agent, tmp_path):
    manifest = _manifest(tmp_path)
    state = {"segments": _segments(["Hello world", "John Smith went home on 2024-01-15."])}

    def _fake(text, source, target, registry, **kwargs):
        from engines.ai_core.translation_agent.retry_policy import TranslateAttemptResult

        return TranslateAttemptResult(
            translated=f"[uk]{text}",
            translator_name="argos",
            success=True,
            attempt=1,
            confidence=0.9,
        )

    with patch(
        "engines.ai_core.translation_agent.agent.translate_with_fallback",
        side_effect=_fake,
    ):
        result = agent.run(manifest, state, "t-raw")

    for seg in result.updated_state["segments"]:
        assert "[uk]" in seg["translated_text"]
        assert len(seg["translated_text"]) >= len(seg["text"]) - 2


def test_segment_count_unchanged(agent, tmp_path):
    manifest = _manifest(tmp_path)
    segments = _segments(["One", "Two", "Three"])
    state = {"segments": segments}

    with patch(
        "engines.ai_core.translation_agent.agent.translate_with_fallback",
        side_effect=lambda text, *a, **k: __import__(
            "engines.ai_core.translation_agent.retry_policy",
            fromlist=["TranslateAttemptResult"],
        ).TranslateAttemptResult(
            translated=text,
            translator_name="argos",
            success=True,
            attempt=1,
            confidence=0.85,
        ),
    ):
        result = agent.run(manifest, state, "t-count")

    assert len(result.updated_state["segments"]) == 3


def test_timing_unchanged(agent, tmp_path):
    manifest = _manifest(tmp_path)
    state = {"segments": _segments(["Alpha", "Beta"])}

    with patch(
        "engines.ai_core.translation_agent.agent.translate_with_fallback",
        side_effect=lambda text, *a, **k: __import__(
            "engines.ai_core.translation_agent.retry_policy",
            fromlist=["TranslateAttemptResult"],
        ).TranslateAttemptResult(
            translated=f"T:{text}",
            translator_name="argos",
            success=True,
            attempt=1,
            confidence=0.88,
        ),
    ):
        result = agent.run(manifest, state, "t-timing")

    for i, seg in enumerate(result.updated_state["segments"]):
        assert seg["start"] == i * 1000
        assert seg["end"] == (i + 1) * 1000


def test_entity_validation_detects_lost_name():
    source = "John Smith arrived on 15.03.2024 with 42 items."
    bad = "Прибув на 15.03.2024 з 42 items."
    result = validate_entities(source, bad)
    assert not result.ok
    assert "John Smith" in result.missing or any(
        "John" in m or "Smith" in m for m in result.missing
    )
    assert 0.0 < result.confidence < 1.0


def test_fallback_chain_argos_to_deep():
    cloud = _FakeTranslator("cloud", available=False)

    class _FailingArgos(BaseTranslator):
        name = "argos"

        def is_available(self) -> bool:
            return True

        def translate(self, text: str, source: str, target: str) -> str:
            raise RuntimeError("argos unavailable")

    argos = _FailingArgos()
    deep = _FakeTranslator("deep-translator", "_d")

    registry = TranslatorRegistry({})
    registry._translators = [cloud, argos, deep]
    registry._loaded = True

    result = translate_with_fallback("Hello", "en", "uk", registry, threshold=0.6)
    assert result.translator_name == "deep-translator"
    assert result.translated == "Hello_d"
    assert result.fallback_used
    assert result.success


def test_confidence_scores_range():
    conf = SegmentConfidence(translation=0.8, entity=0.9, terminology=0.85, language=0.75)
    assert 0.0 <= conf.overall <= 1.0
    assert conf.overall == round(0.35 * 0.8 + 0.25 * 0.9 + 0.20 * 0.85 + 0.20 * 0.75, 4)


def test_retry_on_low_confidence():
    call_count = {"n": 0}

    class _LowThenOk(BaseTranslator):
        name = "argos"

        def is_available(self) -> bool:
            return True

        def translate(self, text: str, source: str, target: str) -> str:
            call_count["n"] += 1
            if call_count["n"] == 1:
                return text
            return f"OK {text}"

    registry = TranslatorRegistry({})
    registry._translators = [_LowThenOk()]
    registry._loaded = True

    result = translate_with_fallback(
        "Hello",
        "en",
        "uk",
        registry,
        threshold=0.65,
        max_retries=3,
    )
    assert call_count["n"] >= 2
    assert result.success
    assert "OK" in result.translated


def test_translation_report_json(agent, tmp_path):
    manifest = _manifest(tmp_path)
    state = {"segments": _segments(["Report test"])}

    with patch(
        "engines.ai_core.translation_agent.agent.translate_with_fallback",
        side_effect=lambda text, *a, **k: __import__(
            "engines.ai_core.translation_agent.retry_policy",
            fromlist=["TranslateAttemptResult"],
        ).TranslateAttemptResult(
            translated=f"R:{text}",
            translator_name="argos",
            success=True,
            attempt=1,
            confidence=0.91,
        ),
    ):
        result = agent.run(manifest, state, "t-report")

    report_path = Path(result.updated_state["translation_report_path"])
    assert report_path.is_file()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["segment_count"] == 1
    assert report["translator_used"] == "argos"
    assert "avg_confidence" in report
    assert "per_segment_confidence" in report
    assert report["execution_time_ms"] >= 0


def test_decision_log_present(agent, tmp_path):
    manifest = _manifest(tmp_path)
    state = {"segments": _segments(["Log me"])}

    with patch(
        "engines.ai_core.translation_agent.agent.translate_with_fallback",
        side_effect=lambda text, *a, **k: __import__(
            "engines.ai_core.translation_agent.retry_policy",
            fromlist=["TranslateAttemptResult"],
        ).TranslateAttemptResult(
            translated=text,
            translator_name="argos",
            success=True,
            attempt=1,
            confidence=0.8,
            decision_log=["mock_decision"],
        ),
    ):
        result = agent.run(manifest, state, "t-log")

    assert result.decision_log
    assert any("segment_count=" in d for d in result.decision_log)


def test_integration_with_manifest(agent, tmp_path):
    manifest = _manifest(tmp_path)
    manifest_path = tmp_path / "manifests" / manifest["project_uuid"] / "project_manifest.json"
    loaded = load_manifest(manifest_path)
    assert loaded["project_uuid"] == manifest["project_uuid"]

    state = {"segments": _segments(["Integration segment"])}

    with patch(
        "engines.ai_core.translation_agent.agent.translate_with_fallback",
        side_effect=lambda text, *a, **k: __import__(
            "engines.ai_core.translation_agent.retry_policy",
            fromlist=["TranslateAttemptResult"],
        ).TranslateAttemptResult(
            translated=f"INT:{text}",
            translator_name="cloud",
            success=True,
            attempt=1,
            confidence=0.92,
        ),
    ):
        result = agent.run(loaded, state, "t-integration")

    assert result.status in ("success", "warning")
    assert result.updated_state["segments"][0]["translated_text"].startswith("INT:")


def test_extract_entities_finds_name_and_date():
    entities = extract_entities("Meet Alice Brown on 01/02/2023.")
    assert any("Alice" in e for e in entities)
    assert any("2023" in e for e in entities)
