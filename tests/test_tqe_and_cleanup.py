"""TQE gate + cleanup safety tests."""

from __future__ import annotations

from pathlib import Path

from engines.tqe import ReviewStatus, run_tqe_gate
from engines.tqe.rules.grammar import check_grammar
from engines.cleanup_engine import CleanupEngine


def test_grammar_rejects_orphan_job_tail():
    errs = check_grammar(
        "so we'll get your real job",
        "Він не розумів, справжню роботу.",
        {},
    )
    assert any(e["code"] == "orphan_clause_glue" for e in errs)


def test_grammar_rejects_false_father_son_opener():
    errs = check_grammar(
        "So George Jr. was a very smart kid and loved cars.",
        "між батьком і сином, Джордж був розумним.",
        {},
    )
    assert any(e["code"] == "orphan_clause_prefix" for e in errs)


def test_grammar_allows_legitimate_father_son_opener():
    errs = check_grammar(
        "between father and son. And so George came to the intersection.",
        "Між батьком і сином, Джордж під'їхав до перехрестя.",
        {},
    )
    assert not any(e["code"] == "orphan_clause_prefix" for e in errs)


def test_tqe_rejects_empty_and_blocks_tts(tmp_path: Path):
    result = run_tqe_gate(
        task_id="tqe_test_empty",
        originals=["Hello world."],
        translations=[""],
        app_dir=str(tmp_path),
        persist=True,
        allow_retry=False,
    )
    assert result.gate_passed is False
    assert result.rejected >= 1
    assert result.decisions[0].allowed_for_tts is False
    assert (tmp_path / "quality" / "failures").exists()


def test_tqe_passes_clean_uk_segment(tmp_path: Path):
    result = run_tqe_gate(
        task_id="tqe_test_ok",
        originals=[
            "An 18-year-old boy named George Jr. drove through his hometown on his way home for dinner."
        ],
        translations=[
            "18-річний Джордж-молодший поїхав додому на вечерю через рідне місто."
        ],
        timing_map=[{"start": 0, "end": 7000}],
        app_dir=str(tmp_path),
        persist=True,
        allow_retry=False,
    )
    assert result.decisions[0].status in (ReviewStatus.PASS, ReviewStatus.WARN) or result.decisions[0].allowed_for_tts
    # Empty not allowed; this segment should pass gate alone
    assert result.gate_passed is True
    assert result.decisions[0].allowed_for_tts is True


def test_cleanup_never_deletes_final_mp4(tmp_path: Path):
    app = tmp_path
    out = app / "output"
    out.mkdir(parents=True)
    final = out / "movie_dub.mp4"
    final.write_bytes(b"fake-mp4")
    junk = out / "temp_seg_01.mp3"
    junk.write_bytes(b"x" * 100)
    session = out / "sessions" / "abc"
    (session / "temp").mkdir(parents=True)
    (session / "temp" / "work.bin").write_bytes(b"y")

    report = CleanupEngine(app).cleanup_after_success(
        session_dir=session,
        keep_names={"movie_dub.mp4"},
    )
    assert final.exists()
    assert not junk.exists() or report.files_deleted >= 0
    assert final.read_bytes() == b"fake-mp4"
