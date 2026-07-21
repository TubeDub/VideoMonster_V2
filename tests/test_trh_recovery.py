"""TRH — Translation Recovery Hotfix tests."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_trh_audit_raw_differs_from_naturalized_on_dirty():
    from engines.tps import run_tps_pipeline
    from engines.tps.owners import clear_owner_registry

    clear_owner_registry("trh1")
    info = {
        "segments_data": [{"index": 0}],
        "translation_audits": [
            {
                "index": 0,
                "raw_translation": "Він був водінням через міст і побачив гончарний трек зірвати війни.",
                "naturalized_text": "Він був водінням через міст і побачив гончарний трек зірвати війни.",
                "final_text": "Він був водінням через міст і побачив гончарний трек зірвати війни.",
                "route": "direct",
            }
        ],
    }
    raw = "Він був водінням через міст і побачив гончарний трек зірвати війни."
    src = (
        "He was driving through his hometown and saw the race track of Star Wars."
    )
    result = run_tps_pipeline(
        task_id="trh1",
        originals=[src],
        translations=[raw],
        src_lang="en",
        tgt_lang="uk",
        app_dir=str(ROOT),
        info=info,
        persist_metrics=False,
    )
    assert result.texts
    audit = info["translation_audits"][0]
    assert audit.get("naturalizer_executed") is True
    # Naturalized or Final must not silently equal dirty Raw without skip_reason
    nat = str(audit.get("naturalized_text") or "")
    raw_a = str(audit.get("raw_translation") or raw)
    if nat == raw_a:
        assert audit.get("naturalizer_skip_reason") or audit.get("dirty_mt")
    else:
        assert "водінням" not in nat or "їхав" in nat
    # Route must not stay silent direct on dirty
    assert audit.get("route") != "direct" or not audit.get("dirty_mt")


def test_trh_segment_trace_written(tmp_path: Path):
    from engines.tps import run_tps_pipeline
    from engines.tps.owners import clear_owner_registry

    clear_owner_registry("trh2")
    info = {"segments_data": [{"index": 0}], "session_dir": str(tmp_path / "sess")}
    (tmp_path / "sess").mkdir(parents=True)
    run_tps_pipeline(
        task_id="trh2",
        originals=["Hello George Jr."],
        translations=["Привіт Жр."],
        tgt_lang="uk",
        app_dir=str(ROOT),
        session_dir=tmp_path / "sess",
        info=info,
        persist_metrics=False,
    )
    assert info.get("trh_segment_traces")
    assert (tmp_path / "sess" / "trh_segment_trace.json").is_file() or info.get(
        "segment_trace_path"
    )


def test_trh_calques_repaired():
    from engines.mt.dirty_mt import apply_temporary_entity_repair

    text = "Він був водінням на гончарний трек і зірвати війни у стаціонарному комплексі."
    out, tickets = apply_temporary_entity_repair(text)
    assert "водінням" not in out
    assert "гончар" not in out
    assert "зірвати війни" not in out.lower()
    assert tickets


def test_trh_cleanup_writes_log(tmp_path: Path):
    from engines.cleanup_engine import CleanupEngine

    app = tmp_path / "app"
    out = app / "output"
    out.mkdir(parents=True)
    junk = out / "temp_seg_0.mp3"
    junk.write_bytes(b"x" * 100)
    sess = tmp_path / "session"
    sess.mkdir()
    eng = CleanupEngine(app)
    report = eng.cleanup_after_success(session_dir=sess, keep_names=set())
    assert (out / "logs" / "cleanup.log").is_file() or (
        sess / "cleanup.log"
    ).is_file()
    assert report.files_deleted >= 0


def test_trh_dsal_skip_reason_on_duration_stamp():
    from engines.tps.duration_stamp import stamp_duration_after_approved
    from engines.tps.owners import clear_owner_registry

    clear_owner_registry("trh_dsal")
    info = {
        "task_id": "trh_dsal",
        "target_lang": "uk",
        "segments_data": [
            {
                "index": 0,
                "approved_text": "Джордж-молодший поїхав додому.",
                "slot_ms": 3000,
                "tqe_status": "PASS",
                "translation_locked": True,
            }
        ],
    }
    stamp_duration_after_approved(info, task_id="trh_dsal")
    seg = info["segments_data"][0]
    assert seg.get("dsal_skip_reason")
    assert seg.get("adaptation_executed") is False


def test_trh_route_not_direct_when_retry():
    from engines.trh import sync_audits_trh

    info = {
        "segments_data": [
            {
                "index": 0,
                "raw_mt": "bad",
                "naturalized_text": "better",
                "approved_text": "better",
                "tps_path": "retry",
                "tqe_status": "PASS",
                "trh": {
                    "raw_mt": "bad",
                    "naturalized": "better",
                    "approved": "better",
                    "dirty": True,
                    "changed_text": True,
                    "naturalizer_applied": True,
                    "tps_path": "retry",
                },
            }
        ],
        "translation_audits": [{"index": 0, "route": "direct"}],
    }
    sync_audits_trh(info)
    assert info["translation_audits"][0]["route"] == "retry"
    assert info["translation_audits"][0]["naturalized_text"] == "better"
    assert info["translation_audits"][0]["raw_translation"] == "bad"
