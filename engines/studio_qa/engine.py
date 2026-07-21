"""Studio QA orchestrator — Master Spec Part 6."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from engines.studio_qa.acceptance import final_acceptance, run_production_hardening_smoke
from engines.studio_qa.diagnostics import (
    observability_event,
    save_crash_checkpoint,
    write_project_diagnostics_zip,
)
from engines.studio_qa.release import (
    golden_comparison,
    issue_quality_certificate,
    run_architecture_audit_part6,
    run_release_validator,
)
from engines.studio_qa.runtime import (
    collect_metrics,
    run_runtime_validator,
    take_health_snapshot,
)
from engines.studio_qa.types import StudioQABundle
from engines.studio_qa.views import (
    build_decision_graph_view,
    build_pipeline_view,
    build_replicas,
    build_review_panel,
    build_timeline_view,
)

logger = logging.getLogger("tubedub.studio_qa")


def build_studio_qa_bundle(
    *,
    sentences: list[Any] | None = None,
    meta: dict[str, Any] | None = None,
    info: dict[str, Any] | None = None,
    pipeline_state: str = "",
) -> StudioQABundle:
    """Build full Studio 2.0 + QA payload from Semantic/Dub meta."""
    meta = dict(meta or {})
    info = dict(info or {})
    runtime = run_runtime_validator({**info, "meta": meta, "semantic_v3": {"meta": meta}})
    metrics = collect_metrics(meta, info)
    health = take_health_snapshot()
    acceptance = final_acceptance(meta=meta, info=info, runtime=runtime)

    errors = list(runtime.get("issues") or [])
    warnings: list[str] = []
    if metrics.get("prediction_error", 0) > 0.5:
        warnings.append("high_prediction_error")

    bundle = StudioQABundle(
        pipeline_view=build_pipeline_view(
            pipeline_state=pipeline_state or str(info.get("pipeline_state") or ""),
            meta=meta,
        ),
        timeline_view=build_timeline_view(meta),
        replicas=build_replicas(sentences, meta),
        review_panel=build_review_panel(sentences, meta),
        decision_graph_view=build_decision_graph_view(meta),
        metrics=metrics,
        health=health,
        acceptance=acceptance,
        errors=errors,
        warnings=warnings,
    )
    observability_event("studio_qa_bundle_built", details={"ok": acceptance.get("ok")})
    logger.info(
        "StudioQA: replicas=%d acceptance=%s errors=%d",
        len(bundle.replicas),
        acceptance.get("ok"),
        len(errors),
    )
    return bundle


def export_diagnostics_archive(
    out_dir: Path | str,
    *,
    bundle: StudioQABundle | dict[str, Any],
    meta: dict[str, Any] | None = None,
    info: dict[str, Any] | None = None,
    name: str = "project.diagnostics.zip",
) -> Path:
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    data = bundle.to_dict() if hasattr(bundle, "to_dict") else dict(bundle)
    return write_project_diagnostics_zip(
        root / name,
        bundle=data,
        meta=meta,
        info=info,
    )


def run_part6_gate(*, quick: bool = True) -> dict[str, Any]:
    """Aggregate Part 6 release-facing checks."""
    arch = run_architecture_audit_part6()
    release = run_release_validator(quick=quick)
    hardening = run_production_hardening_smoke() if not quick else {"ok": True, "skipped": True}
    cert = issue_quality_certificate(quick=True)
    golden = golden_comparison()
    ok = bool(arch.get("ok")) and bool(release.get("ok")) and bool(hardening.get("ok"))
    return {
        "ok": ok,
        "architecture": arch,
        "release": release,
        "hardening": hardening,
        "certificate": cert,
        "golden_comparison": golden,
    }
