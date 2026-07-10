"""Tests — SemanticValidationError OpenDDF integration."""

from __future__ import annotations

import json
from pathlib import Path

from engines.pipeline_integrity.passive_openddf import PassiveOpenDDFSession
from engines.pipeline_integrity.semantic_validation_openddf import (
    build_semantic_failure_payload,
    build_semantic_snapshot_diff,
)
from engines.semantic_meaning import SemanticValidationError


def test_semantic_validation_error_rich_fields():
    exc = SemanticValidationError(
        "Segment #2: semantic validation failed (entity_loss)",
        details={
            "failures": [
                {
                    "index": 2,
                    "reason": "entity_loss",
                    "errors": [
                        {
                            "code": "entity_loss",
                            "entity_name": "George Jr.",
                            "entity_type": "named_entity",
                            "cause": "entity_not_present_in_semantic_output",
                            "segment_index": 2,
                            "original_text": "George Jr. was driving.",
                            "changed_text": "Він їхав.",
                            "final_text": "Він їхав.",
                            "suspected_module": "timing_aware_translation",
                            "location": {"char_start": 0},
                        }
                    ],
                    "transformation_chain": {
                        "original": "George Jr. was driving.",
                        "raw_mt": "Джордж-молодший їхав.",
                        "semantic": "Він їхав.",
                        "final": "Він їхав.",
                    },
                    "entity_loss_reports": [
                        {
                            "lost_entity": "George Jr.",
                            "entity_type": "named_entity",
                            "suspected_module": "timing_aware_translation",
                        }
                    ],
                    "word_diff": {
                        "baseline_to_final": {
                            "removed_words": ["джордж", "молодший"],
                            "added_words": ["він"],
                            "replaced": [],
                        }
                    },
                }
            ],
            "problem_segment_indices": [2],
            "snapshot_before": [
                {
                    "index": 2,
                    "original": "George Jr. was driving.",
                    "raw_mt": "Джордж-молодший їхав.",
                    "semantic_output": "Джордж-молодший їхав.",
                    "final_output": "Джордж-молодший їхав.",
                }
            ],
            "snapshot_after": [
                {
                    "index": 2,
                    "original": "George Jr. was driving.",
                    "raw_mt": "Джордж-молодший їхав.",
                    "semantic_output": "Він їхав.",
                    "final_output": "Він їхав.",
                }
            ],
        },
    )
    assert exc.entity_name == "George Jr."
    assert exc.segment_index == 2
    assert "George Jr." in exc.format_diagnostic_block()
    info = exc.to_openddf_exception_info()
    assert info["entity_name"] == "George Jr."
    assert info["code"] == "SEMANTIC_VALIDATION"


def test_build_semantic_snapshot_diff_word_and_entity():
    payload = {
        "failures": [
            {
                "index": 0,
                "reason": "entity_loss",
                "transformation_chain": {
                    "original": "George Jr. drove.",
                    "raw_mt": "Джордж їхав.",
                    "semantic": "Він їхав.",
                    "final": "Він їхав.",
                },
                "word_diff": {
                    "baseline_to_final": {
                        "removed_words": ["джордж"],
                        "added_words": ["він"],
                        "replaced": [{"from": "джордж", "to": "він"}],
                    }
                },
                "entity_loss_reports": [
                    {"lost_entity": "George Jr.", "entity_type": "named_entity"}
                ],
                "errors": [],
            }
        ],
        "problem_segment_indices": [0],
    }
    diff = build_semantic_snapshot_diff(payload)
    assert diff["error_type"] == "SemanticValidationError"
    assert diff["segments"][0]["removed_words"] == ["джордж"]
    assert diff["entity_loss_reports"][0]["lost_entity"] == "George Jr."


def test_persist_semantic_validation_bundle_writes_files(tmp_path):
    payload = build_semantic_failure_payload(
        [
            {
                "index": 0,
                "reason": "entity_loss",
                "details": {
                    "entity_errors": [
                        {"value": "George Jr.", "category": "named_entity"}
                    ],
                },
                "qa": {},
            }
        ],
        segments=["George Jr. drove there."],
        raw_by_index=["Джордж-молодший їхав туди."],
        post_naturalizer=["Джордж-молодший їхав туди."],
        naturalized=["Він їхав туди."],
        source_lang="en",
        target_lang="uk",
    )
    exc = SemanticValidationError("fail", details=payload)
    session = PassiveOpenDDFSession("task-sv-1", tmp_path)
    arts = session.persist_semantic_validation_bundle(
        exc,
        task_info={"target_lang": "uk", "voice": "uk-UA-OstapNeural", "model_size": "medium"},
    )
    assert Path(arts["diagnostic_zip"]).is_file()
    before = json.loads(Path(arts["snapshot_before"]).read_text(encoding="utf-8"))
    after = json.loads(Path(arts["snapshot_after"]).read_text(encoding="utf-8"))
    diff = json.loads(Path(arts["snapshot_diff"]).read_text(encoding="utf-8"))
    report = json.loads(Path(arts["report"]).read_text(encoding="utf-8"))
    runtime = json.loads(Path(arts["runtime_pipeline"]).read_text(encoding="utf-8"))
    assert len(before) >= 1
    assert before[0]["original"]
    assert after[0]["semantic_output"]
    assert diff["segments"]
    assert report.get("semantic_validation")
    assert runtime.get("whisper")
    assert runtime.get("tts_engine")
    assert runtime.get("semantic_engine")
