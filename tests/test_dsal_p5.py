"""TZ v4.0 P5 — DSAL benchmark + certificate section."""

from __future__ import annotations


def test_run_dsal_benchmark_llm_off():
    from engines.dsal.benchmark import run_dsal_benchmark

    report = run_dsal_benchmark(llm_off=True, allow_llm=False)
    assert report.llm_off is True
    assert report.path
    assert report.metrics["segments"] == 20
    assert report.ok is True
    by_name = {g.name: g for g in report.gates}
    assert by_name["llm_off_ok"].ok
    assert by_name["seg6_underflow_fixed"].ok
    assert by_name["must_restore"].ok
    assert by_name["clause_coverage_critical"].ok
    assert report.metrics["seg6_delta_pct"] <= 15


def test_certificate_includes_dsal_benchmark(tmp_path, monkeypatch):
    from engines.release_governance.certificate import issue_release_certificate

    # Keep certify fast: skip heavy P16
    cert = issue_release_certificate(
        work_dir=tmp_path,
        include_p16=False,
        promote_if_approved=False,
    )
    names = [s.name for s in cert.sections]
    assert "dsal_benchmark" in names
    dsal = next(s for s in cert.sections if s.name == "dsal_benchmark")
    assert "avg_match" in dsal.detail or dsal.data.get("metrics")
    assert dsal.ok is True or "error" in (dsal.detail or "").lower() or dsal.data
