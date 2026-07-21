"""P17 Quality Certification & Release Governance tests."""

from __future__ import annotations

from pathlib import Path

from engines.release_governance.architecture_audit import run_architecture_audit
from engines.release_governance.config_freeze import (
    assert_config_matches_freeze,
    collect_frozen_config,
    write_config_freeze,
)
from engines.release_governance.docs_audit import run_docs_audit
from engines.release_governance.golden_release import (
    load_golden_release,
    measure_candidate_quality,
    promote_golden_release,
)
from engines.release_governance.quality_gates import evaluate_quality_gates
from engines.release_governance.uat import run_uat_suite
from engines.release_governance.certificate import issue_release_certificate


def test_config_freeze_roundtrip(tmp_path: Path):
    path = write_config_freeze(tmp_path / "freeze.json")
    assert path.is_file()
    frozen = collect_frozen_config()
    assert assert_config_matches_freeze(frozen) == []


def test_promote_and_load_golden(tmp_path: Path):
    metrics = measure_candidate_quality()
    base = promote_golden_release(label="test", root=tmp_path, metrics=metrics)
    assert (base / "golden_release.json").is_file()
    assert (base / "config_freeze.json").is_file()
    loaded = load_golden_release(label="test", root=tmp_path)
    assert loaded is not None
    assert loaded.metrics.segment_count == metrics.segment_count


def test_quality_gates_pass_against_self(tmp_path: Path):
    metrics = measure_candidate_quality()
    promote_golden_release(label="latest", root=tmp_path, metrics=metrics)
    report = evaluate_quality_gates(
        candidate=metrics,
        golden=load_golden_release(label="latest", root=tmp_path),
        max_processing_ms=60_000,
    )
    assert report.ok
    assert not report.blocked


def test_quality_gates_block_on_overflow_regression(tmp_path: Path):
    base = measure_candidate_quality()
    promote_golden_release(label="latest", root=tmp_path, metrics=base)
    worse = measure_candidate_quality()
    worse.overflow_count = base.overflow_count + 5
    report = evaluate_quality_gates(
        candidate=worse,
        golden=load_golden_release(label="latest", root=tmp_path),
        max_processing_ms=60_000,
    )
    assert not report.ok
    assert report.blocked
    names = {g.name: g.ok for g in report.gates}
    assert names.get("overflow") is False


def test_uat_suite_runs():
    report = run_uat_suite()
    assert len(report.cases) == 7
    assert all(c.meaning_preserved for c in report.cases)


def test_architecture_and_docs_audit():
    arch = run_architecture_audit()
    assert arch.ok, [i.to_dict() for i in arch.items if not i.ok]
    docs = run_docs_audit()
    assert docs.ok, [i.to_dict() for i in docs.items if not i.ok]


def test_release_certificate(tmp_path: Path):
    cert = issue_release_certificate(
        work_dir=tmp_path / "cert",
        releases_dir=tmp_path / "releases",
        promote_if_approved=False,
        include_p16=False,
    )
    assert cert.path
    assert Path(cert.path).is_file()
    assert cert.status in ("Release Approved", "Release Blocked")
    names = {s.name for s in cert.sections}
    assert "quality_gates" in names
    assert "user_acceptance_tests" in names
    assert "architecture_audit" in names
    assert cert.approved, [(s.name, s.detail) for s in cert.sections if not s.ok]
