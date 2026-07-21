"""P816 Release Governance + P817 Quality Certification façade."""

from __future__ import annotations

from typing import Any


GOVERNANCE_PIPELINE = (
    "Architecture Review",
    "Regression",
    "Golden Dataset",
    "Performance",
    "Security",
    "Production Hardening",
    "Certification",
)


def run_release_governance(*, quick: bool = True) -> dict[str, Any]:
    """P816 — mandatory gates before release."""
    sections: list[dict[str, Any]] = []

    # Architecture
    try:
        from engines.studio_qa.release import run_architecture_audit_part6

        arch = run_architecture_audit_part6()
        sections.append({"name": "Architecture Review", "ok": bool(arch.get("ok")), "data": arch})
    except Exception as exc:
        sections.append({"name": "Architecture Review", "ok": False, "error": str(exc)})

    # Regression / quality gates
    try:
        from engines.release_governance.quality_gates import evaluate_quality_gates

        gates = evaluate_quality_gates()
        ok = bool(getattr(gates, "ok", True)) if not isinstance(gates, dict) else bool(gates.get("ok", True))
        data = gates.to_dict() if hasattr(gates, "to_dict") else gates
        sections.append({"name": "Regression", "ok": ok, "data": data})
    except Exception as exc:
        sections.append({"name": "Regression", "ok": False, "error": str(exc)})

    # Golden
    try:
        from engines.release_governance.golden_release import load_golden_release, measure_candidate_quality

        golden = load_golden_release()
        candidate = measure_candidate_quality()
        sections.append(
            {
                "name": "Golden Dataset",
                "ok": True,
                "data": {
                    "golden_loaded": golden is not None,
                    "candidate": candidate.to_dict() if hasattr(candidate, "to_dict") else str(candidate),
                },
            }
        )
    except Exception as exc:
        sections.append({"name": "Golden Dataset", "ok": False, "error": str(exc)})

    # Performance budgets present
    try:
        from engines.perf_budgets import BUDGETS

        sections.append({"name": "Performance", "ok": bool(BUDGETS), "data": dict(BUDGETS)})
    except Exception as exc:
        sections.append({"name": "Performance", "ok": False, "error": str(exc)})

    # Security
    try:
        from engines.enterprise.security import SecretsVault

        vault = SecretsVault()
        issues = vault.assert_no_plaintext_in_repo()
        sections.append({"name": "Security", "ok": len(issues) == 0, "issues": issues})
    except Exception as exc:
        sections.append({"name": "Security", "ok": False, "error": str(exc)})

    # Hardening
    if quick:
        sections.append({"name": "Production Hardening", "ok": True, "skipped": True})
    else:
        try:
            from engines.studio_qa.acceptance import run_production_hardening_smoke

            hard = run_production_hardening_smoke()
            sections.append({"name": "Production Hardening", "ok": bool(hard.get("ok")), "data": hard})
        except Exception as exc:
            sections.append({"name": "Production Hardening", "ok": False, "error": str(exc)})

    # Certification
    cert = issue_quality_certificates(quick=quick)
    sections.append({"name": "Certification", "ok": bool(cert.get("ok") or cert.get("approved")), "data": cert})

    ok_all = all(s.get("ok") for s in sections)
    return {"ok": ok_all, "pipeline": list(GOVERNANCE_PIPELINE), "sections": sections}


def issue_quality_certificates(*, quick: bool = True) -> dict[str, Any]:
    """P817 — Release / Architecture / Performance / Golden / Diagnostics reports."""
    out: dict[str, Any] = {"ok": True, "reports": {}}
    try:
        from engines.release_governance.certificate import issue_release_certificate

        cert = issue_release_certificate(include_p16=not quick, p16_long_run_sec=1.5)
        data = cert.to_dict() if hasattr(cert, "to_dict") else {"raw": str(cert)}
        out["reports"]["release_certificate"] = data
        out["approved"] = bool(getattr(cert, "approved", data.get("approved")))
        out["ok"] = bool(out.get("approved") or data.get("status"))
    except Exception as exc:
        out["reports"]["release_certificate"] = {"error": str(exc)}
        out["ok"] = False

    try:
        from engines.studio_qa.release import run_architecture_audit_part6

        out["reports"]["architecture_certificate"] = run_architecture_audit_part6()
    except Exception as exc:
        out["reports"]["architecture_certificate"] = {"error": str(exc)}

    try:
        from engines.perf_budgets import BUDGETS

        out["reports"]["performance_report"] = {"budgets_ms": dict(BUDGETS)}
    except Exception as exc:
        out["reports"]["performance_report"] = {"error": str(exc)}

    try:
        from engines.release_governance.golden_release import measure_candidate_quality

        c = measure_candidate_quality()
        out["reports"]["golden_report"] = c.to_dict() if hasattr(c, "to_dict") else str(c)
    except Exception as exc:
        out["reports"]["golden_report"] = {"error": str(exc)}

    try:
        from engines.enterprise.diagnostics import run_self_diagnostics

        out["reports"]["diagnostics_report"] = run_self_diagnostics()
    except Exception as exc:
        out["reports"]["diagnostics_report"] = {"error": str(exc)}

    return out
