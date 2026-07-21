"""P17.7 — Final Release Certificate."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
from engines.release_governance.versions import collect_version_bundle

ROOT = Path(__file__).resolve().parents[2]

KNOWN_LIMITATIONS = [
    "Golden dataset content fill (20 films / 10k segments) is ongoing; fingerprints scaffolded.",
    "Neural TTS adapters need installed backends on target hosts.",
    "P5 Timeline/Pipeline UI viewers are follow-up (API/data layer ready).",
    "P16 8h/24h lab long-run must pass on release hardware before GA.",
    "StreamDub parallel stack is not fully under Freeze boundary yet.",
    "DSAL avg duration_match >90 is stretch target with LLM; LLM-off gate is ≥85.",
]


@dataclass
class CertificateSection:
    name: str
    ok: bool
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "detail": self.detail,
            "data": self.data,
        }


@dataclass
class ReleaseCertificate:
    approved: bool
    status: str
    system_version: str
    sections: list[CertificateSection] = field(default_factory=list)
    known_limitations: list[str] = field(default_factory=list)
    issued_at: str = ""
    path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "approved": self.approved,
            "status": self.status,
            "system_version": self.system_version,
            "issued_at": self.issued_at,
            "known_limitations": list(self.known_limitations),
            "sections": [s.to_dict() for s in self.sections],
            "path": self.path,
        }


def issue_release_certificate(
    *,
    work_dir: Path | None = None,
    releases_dir: Path | None = None,
    promote_if_approved: bool = False,
    include_p16: bool = True,
    p16_long_run_sec: float = 1.5,
) -> ReleaseCertificate:
    """
    Aggregate P17 gates into a Final Release Certificate.

    Status is ``Release Approved`` only when all mandatory sections pass.
    """
    work_dir = Path(work_dir or (ROOT / "output" / "p17_certification"))
    work_dir.mkdir(parents=True, exist_ok=True)
    versions = collect_version_bundle()
    sections: list[CertificateSection] = []

    # Quality vs golden (bootstrap if missing)
    candidate = measure_candidate_quality()
    golden = load_golden_release(root=releases_dir)
    if golden is None:
        # Bootstrap golden from current candidate so gates have a baseline.
        promote_golden_release(
            label="latest",
            root=releases_dir,
            metrics=candidate,
            regression_report={"status": "bootstrap"},
            architecture_report={"status": "bootstrap"},
        )
        golden = load_golden_release(root=releases_dir)

    gates = evaluate_quality_gates(candidate=candidate, golden=golden, root=releases_dir)
    sections.append(
        CertificateSection(
            "quality_gates",
            ok=gates.ok,
            detail="blocked" if gates.blocked else "pass",
            data=gates.to_dict(),
        )
    )

    uat = run_uat_suite()
    sections.append(
        CertificateSection(
            "user_acceptance_tests",
            ok=uat.ok,
            detail=f"scenarios={len(uat.cases)}",
            data=uat.to_dict(),
        )
    )

    freeze_path = write_config_freeze(work_dir / "config_freeze.json")
    frozen = collect_frozen_config()
    drift = assert_config_matches_freeze(frozen)
    sections.append(
        CertificateSection(
            "configuration_freeze",
            ok=not drift,
            detail=str(freeze_path),
            data={"drift": drift, "freeze": frozen},
        )
    )

    arch = run_architecture_audit()
    sections.append(
        CertificateSection(
            "architecture_audit",
            ok=arch.ok,
            detail=f"items={len(arch.items)}",
            data=arch.to_dict(),
        )
    )

    docs = run_docs_audit()
    sections.append(
        CertificateSection(
            "documentation_audit",
            ok=docs.ok,
            detail=f"docs={len(docs.items)}",
            data=docs.to_dict(),
        )
    )

    # Golden dataset presence
    from engines.pipeline_integrity.golden_dataset import ensure_golden_layout, golden_root

    groot = ensure_golden_layout(golden_root())
    sections.append(
        CertificateSection(
            "golden_dataset",
            ok=(groot / "manifest.json").is_file(),
            detail=str(groot),
            data={"manifest": str(groot / "manifest.json")},
        )
    )

    # Performance budget presence
    sections.append(
        CertificateSection(
            "performance_budget",
            ok=bool(versions.get("performance_budgets_ms")),
            detail="budgets registered",
            data={"budgets_ms": versions.get("performance_budgets_ms")},
        )
    )

    if include_p16:
        try:
            from engines.production_hardening.checklist import run_release_checklist

            p16 = run_release_checklist(
                include_pytest=False,
                long_run_sec=p16_long_run_sec,
                work_dir=work_dir / "p16",
            )
            sections.append(
                CertificateSection(
                    "production_hardening",
                    ok=p16.ok,
                    detail=f"items={len(p16.items)}",
                    data=p16.to_dict(),
                )
            )
        except Exception as exc:
            sections.append(
                CertificateSection(
                    "production_hardening",
                    ok=False,
                    detail=str(exc),
                )
            )

    # P5 / TZ v4.0: DSAL George Lucas benchmark (LLM-off governance baseline)
    try:
        from engines.dsal.benchmark import run_dsal_benchmark

        dsal_report = run_dsal_benchmark(
            out_dir=work_dir / "dsal_benchmark",
            llm_off=True,
            allow_llm=False,
        )
        sections.append(
            CertificateSection(
                "dsal_benchmark",
                ok=dsal_report.ok,
                detail=(
                    f"ok={dsal_report.ok} "
                    f"seg6_delta={dsal_report.metrics.get('seg6_delta_pct')}% "
                    f"adapted={dsal_report.metrics.get('adapted')}"
                ),
                data=dsal_report.to_dict(),
            )
        )
    except Exception as exc:
        sections.append(
            CertificateSection(
                "dsal_benchmark",
                ok=False,
                detail=str(exc),
            )
        )

    approved = all(s.ok for s in sections)
    status = "Release Approved" if approved else "Release Blocked"
    cert = ReleaseCertificate(
        approved=approved,
        status=status,
        system_version=str(versions.get("system_version")),
        sections=sections,
        known_limitations=list(KNOWN_LIMITATIONS),
        issued_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )

    out = work_dir / "release_certificate.json"
    out.write_text(json.dumps(cert.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    cert.path = str(out)

    if approved and promote_if_approved:
        promote_golden_release(
            label="latest",
            root=releases_dir,
            metrics=candidate,
            regression_report={"quality_gates": gates.to_dict(), "uat": uat.to_dict()},
            architecture_report=arch.to_dict(),
        )

    return cert
