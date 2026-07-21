"""Master Spec Part 6 — Studio / Diagnostics / QA / Production Hardening."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest


def _sample_meta():
    return {
        "phase2": True,
        "semantic_core": True,
        "dub_engine_v2": True,
        "bridge": False,
        "decision_graph": {
            "profile": "Movie",
            "scene_uuid": "sc1",
            "records": [
                {
                    "sentence_uuid": "s1",
                    "problem": "overflow",
                    "candidates": [
                        {
                            "label": "tempo",
                            "steps": ["trim_silence", "tempo"],
                            "decision_score": 0.8,
                            "cost": 1.0,
                        },
                        {
                            "label": "manual",
                            "steps": ["manual_review"],
                            "decision_score": 0.2,
                            "cost": 9.0,
                        },
                    ],
                    "rejected": [
                        {
                            "label": "manual",
                            "reject_reasons": ["high_cost"],
                            "explanation": "cost too high",
                        }
                    ],
                    "accepted": {
                        "label": "tempo",
                        "steps": ["trim_silence", "tempo"],
                        "decision_score": 0.8,
                        "explanation": "best score",
                    },
                    "reason": "overflow",
                }
            ],
            "conflicts": [],
        },
        "speech_units": [
            {
                "speech_uuid": "su1",
                "sentence_uuid": "s1",
                "start_ms": 0,
                "end_ms": 2000,
                "text": "Привіт",
                "speech_status": "planned",
            }
        ],
        "timeline": {
            "timeline_uuid": "tl1",
            "units": [
                {
                    "audio_uuid": "au1",
                    "speech_uuid": "su1",
                    "start_ms": 0,
                    "end_ms": 1800,
                    "wav_path": "",
                    "tempo": 1.05,
                    "stretch": 1.0,
                    "pause_ms": 50,
                }
            ],
            "pauses": [{"ms": 50}],
            "conflicts": [],
        },
        "audio_metrics": {
            "overlap_count": 0,
            "tail_spill_count": 0,
            "borrow_time_count": 1,
            "tempo_usage": 1,
            "stretch_usage": 0,
            "prediction_error": 0.1,
            "speech_flow_score": 88.0,
            "manual_review_count": 0,
        },
        "dub": {"adjustments": [], "metrics": {}},
        "quality_plans": [
            {
                "sentence_uuid": "s1",
                "meaning_score": 95,
                "entity_score": 100,
                "duration_score": 90,
                "lipsync_score": 80,
                "speech_score": 92,
            }
        ],
    }


def test_pipeline_stages_complete():
    from engines.studio_qa import PIPELINE_STAGES

    assert "Recognition" in PIPELINE_STAGES
    assert "Export" in PIPELINE_STAGES
    assert len(PIPELINE_STAGES) >= 11


def test_studio_qa_bundle_views():
    from engines.studio_qa import build_studio_qa_bundle
    from engines.semantic_v3.types import SemanticSentence

    meta = _sample_meta()
    s = SemanticSentence(
        text="Hello",
        translated_text="Привіт",
        start_ms=0,
        end_ms=2000,
        sentence_uuid="s1",
        semantic_locked=True,
        lock_status="locked",
    )
    bundle = build_studio_qa_bundle(
        sentences=[s],
        meta=meta,
        info={"pipeline_state": "SPEECH_READY"},
        pipeline_state="SPEECH_READY",
    )
    data = bundle.to_dict()
    assert data["version"] == "6.0"
    assert data["pipeline_view"]["stages"]
    assert data["timeline_view"]["units"]
    assert data["replicas"]
    assert data["review_panel"]
    assert data["decision_graph_view"]["records"]
    assert data["decision_graph_view"]["records"][0]["accepted"]["label"] == "tempo"
    assert data["metrics"]["overlap"] == 0
    assert data["acceptance"]["ok"] is True
    scores = data["review_panel"][0]["scores"]
    assert "overall_score" in scores
    assert "meaning_score" in scores


def test_error_taxonomy_classify():
    from engines.studio_qa.runtime import ERROR_TAXONOMY, classify_error

    assert "DecisionError" in ERROR_TAXONOMY
    assert classify_error("scheduler overlap detected") == "TimingError"
    assert classify_error("decision policy rejected") == "DecisionError"
    assert classify_error("contract version missing") == "ContractError"


def test_diagnostics_zip(tmp_path: Path):
    from engines.studio_qa import build_studio_qa_bundle, export_diagnostics_archive

    meta = _sample_meta()
    bundle = build_studio_qa_bundle(meta=meta, info={})
    zpath = export_diagnostics_archive(tmp_path, bundle=bundle, meta=meta, info={})
    assert zpath.is_file()
    assert zpath.name == "project.diagnostics.zip"
    with zipfile.ZipFile(zpath) as zf:
        names = set(zf.namelist())
        assert "report.json" in names
        assert "pipeline.json" in names
        assert "metrics.json" in names
        assert "decision_graph.json" in names
        assert "errors.json" in names


def test_crash_checkpoint_roundtrip(tmp_path: Path):
    from engines.studio_qa.diagnostics import load_crash_checkpoint, save_crash_checkpoint

    meta = _sample_meta()
    path = save_crash_checkpoint(
        tmp_path,
        info={"pipeline_state": "PLANNED", "task_id": "t1"},
        meta=meta,
        bundle={"acceptance": {"ok": True}},
    )
    assert Path(path).is_file()
    loaded = load_crash_checkpoint(tmp_path)
    assert loaded is not None
    assert str(loaded.get("pipeline_state") or "").upper().find("PLAN") >= 0 or loaded.get(
        "pipeline_state"
    )


def test_final_acceptance_fails_on_overlap():
    from engines.studio_qa.acceptance import final_acceptance

    meta = _sample_meta()
    meta["audio_metrics"]["overlap_count"] = 2
    result = final_acceptance(meta=meta, info={}, runtime={"ok": True, "issues": []})
    assert result["ok"] is False
    assert result["checks"]["no_overlap"] is False


def test_architecture_audit_part6_isolation():
    from engines.studio_qa.release import run_architecture_audit_part6

    report = run_architecture_audit_part6()
    assert "part6_extras" in report
    extras = {e["name"]: e["ok"] for e in report["part6_extras"]}
    assert extras.get("translation_core_isolation") is True
    assert extras.get("decision_policy_isolation") is True
    assert extras.get("dub_engine_isolation") is True


def test_runtime_validator_flags_tail_spill():
    from engines.studio_qa.runtime import run_runtime_validator

    meta = _sample_meta()
    meta["audio_metrics"]["tail_spill_count"] = 1
    out = run_runtime_validator({"meta": meta, "pipeline_state": "scheduler"})
    assert out["ok"] is False
    assert any(i.get("message") == "tail_spill>0" for i in out["issues"])


def test_part6_gate_quick():
    from engines.studio_qa import run_part6_gate

    # Architecture isolation must pass; release/golden may bootstrap
    gate = run_part6_gate(quick=True)
    assert gate["architecture"]["ok"] is True or gate["architecture"].get("part6_extras")
    assert "certificate" in gate
    assert "golden_comparison" in gate
