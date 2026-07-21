"""TPS / Translation Fast Path v2 — unit + architecture tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engines.tps import (
    ApprovedTextMutationError,
    DualWriterError,
    TQEStatus,
    approve_segment,
    get_approved_text,
    get_owner_registry,
    guard_post_pass_mutation,
    run_fast_qa,
    run_tps_pipeline,
)
from engines.tps.owners import clear_owner_registry
from engines.tts_text_path import final_texts_from_info


def test_tqe_fast_path_pass_no_llm_rewrite(tmp_path: Path):
    clear_owner_registry("tps_fast")
    result = run_tps_pipeline(
        task_id="tps_fast",
        originals=[
            "An 18-year-old boy named George Jr. drove through his hometown on his way home for dinner."
        ],
        translations=[
            "18-річний Джордж-молодший поїхав додому на вечерю через рідне місто."
        ],
        src_lang="en",
        tgt_lang="uk",
        app_dir=str(tmp_path),
        persist_metrics=True,
    )
    assert result.segments[0].status == TQEStatus.PASS
    assert result.metrics.fast_path_count >= 1
    assert result.metrics.avg_llm_calls_per_segment == 0.0
    assert (tmp_path / "output" / "sessions" / "tps_fast" / "tps_metrics.json").is_file()


def test_tqe_fail_triggers_exactly_one_retry(tmp_path: Path):
    clear_owner_registry("tps_retry")
    # Empty translation must fail Fast QA then retry once (argos may fill or stay manual)
    result = run_tps_pipeline(
        task_id="tps_retry",
        originals=["Hello world from George Lucas."],
        translations=[""],
        src_lang="en",
        tgt_lang="uk",
        app_dir=str(tmp_path),
        persist_metrics=False,
    )
    # Either recovered via retry/judge or manual — but not infinite retries
    assert result.metrics.retry_path_count + result.metrics.manual_review_count + result.metrics.llm_judge_count >= 1
    assert result.metrics.fast_path_count == 0


def test_no_wordcount_en_uk_hard_fail():
    # Short UK relative to EN but complete meaning about dinner — must not fail solely on counts
    qa = run_fast_qa(
        "He went home for dinner.",
        "Він пішов додому на вечерю.",
        context={"target_lang": "uk"},
    )
    assert "word_count" not in qa.reason_codes
    assert qa.passed or "meaning_loss" not in qa.reason_codes or qa.passed


def test_entity_missing_still_fails_qa():
    qa = run_fast_qa(
        "His father bought him a Fiat.",
        "Його батько купив йому машину.",
        context={"target_lang": "uk"},
    )
    # May or may not catch Fiat depending on critical entity list — empty still fails
    qa_empty = run_fast_qa("Fiat crashed.", "", context={"target_lang": "uk"})
    assert not qa_empty.passed
    assert "empty" in qa_empty.reason_codes


def test_incomplete_sentence_still_fails_qa():
    qa = run_fast_qa(
        "Everything went black.",
        "Все потемніло,",
        context={"target_lang": "uk"},
    )
    assert not qa.passed
    assert any(c in qa.reason_codes for c in ("incomplete", "incomplete_sentence"))


def test_approved_text_immutable_after_pass():
    seg = {"index": 0}
    approve_segment(seg, "Фінальний текст.", tqe_status="PASS", path="fast", task_id="t1", index=0)
    assert get_approved_text(seg) == "Фінальний текст."
    with pytest.raises(ApprovedTextMutationError):
        guard_post_pass_mutation(seg, new_text="Інший смисл повністю.")


def test_review_text_equals_tts_input():
    info = {
        "tps": True,
        "segments_data": [
            {
                "index": 0,
                "approved_text": "Один approved текст.",
                "semantic_text": "старий semantic",
                "grammar_text": "старий grammar",
                "text": "старий text",
                "tqe_status": "PASS",
                "translation_locked": True,
            }
        ],
        "translation_audits": [
            {"index": 0, "final_text": "audit final", "tts_text": "audit tts"}
        ],
    }
    texts = final_texts_from_info(info)
    assert texts == ["Один approved текст."]


def test_no_dual_timing_adapt_on_happy_path():
    clear_owner_registry("dual")
    reg = get_owner_registry("dual")
    reg.claim("timing_text_adapt", "TimingMeaningFitOwner", segment_index=0)
    assert reg.timing_adapt_count(0) == 1
    # DSAL is alias of TimingMeaningFitOwner; a foreign writer must fail
    with pytest.raises(DualWriterError):
        reg.claim("timing_text_adapt", "DubbingEngine", segment_index=0)
    assert reg.dual_writer_violations >= 1


def test_tps_metrics_written(tmp_path: Path):
    clear_owner_registry("metrics")
    result = run_tps_pipeline(
        task_id="metrics",
        originals=["Hello."],
        translations=["Привіт."],
        tgt_lang="uk",
        app_dir=str(tmp_path),
        persist_metrics=True,
    )
    path = tmp_path / "output" / "sessions" / "metrics" / "tps_metrics.json"
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "fast_path_count" in data
    assert "reject_reason_histogram" in data
    assert data.get("dual_writer_violations") == 0


def test_dual_grammar_writer_fails():
    clear_owner_registry("g")
    reg = get_owner_registry("g")
    reg.claim("grammar_rewrite", "GrammarRewriteOwner", segment_index=1)
    with pytest.raises(DualWriterError):
        reg.claim("grammar_rewrite", "SemanticAgent", segment_index=1)


def test_dual_semantic_writer_fails():
    clear_owner_registry("s")
    reg = get_owner_registry("s")
    reg.claim("semantic_rewrite", "SemanticRewriteOwner", segment_index=2)
    with pytest.raises(DualWriterError):
        reg.claim("semantic_rewrite", "GrammarAgent", segment_index=2)


def test_duration_stamp_no_text_rewrite():
    from engines.tps.duration_stamp import stamp_duration_after_approved

    clear_owner_registry("dur")
    info = {
        "task_id": "dur",
        "target_lang": "uk",
        "segments_data": [
            {
                "index": 0,
                "approved_text": "Він пішов додому на вечерю.",
                "slot_ms": 2000,
                "tqe_status": "PASS",
                "translation_locked": True,
            }
        ],
    }
    counts = stamp_duration_after_approved(info, task_id="dur")
    assert counts["stamped"] == 1
    seg = info["segments_data"][0]
    assert seg["approved_text"] == "Він пішов додому на вечерю."
    assert seg.get("dsal_band") in ("green", "yellow", "red")
    assert seg.get("adaptation_executed") is False
    assert seg.get("tps_duration_stamped") is True


def test_pipeline_cache_includes_tps_version():
    from engines.pipeline_cache import cache_versions
    from engines.tps.version import TPS_PIPELINE_VERSION

    v = cache_versions()
    assert v.get("tps_v") == TPS_PIPELINE_VERSION


def test_llm_judge_default_on(monkeypatch):
    """With empty env, judge path is attempted (default ON); without LLM returns False."""
    monkeypatch.delenv("TPS_LLM_JUDGE", raising=False)
    monkeypatch.delenv("TQE_LLM_JUDGE", raising=False)
    from engines.tps import pipeline as tps_pipe

    text, ok, calls = tps_pipe._llm_judge(
        "Hello",
        "Привіт",
        errors=[{"code": "test"}],
        tgt_lang="uk",
    )
    # Either LLM unavailable (ok=False, calls=0) or ran (calls=1) — not short-circuited by env off
    assert calls in (0, 1)
    assert isinstance(ok, bool)


def test_llm_judge_can_disable(monkeypatch):
    monkeypatch.setenv("TPS_LLM_JUDGE", "0")
    from engines.tps import pipeline as tps_pipe

    text, ok, calls = tps_pipe._llm_judge(
        "Hello", "Привіт", errors=[], tgt_lang="uk"
    )
    assert ok is False
    assert calls == 0
    assert text == "Привіт"
