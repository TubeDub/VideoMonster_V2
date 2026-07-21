"""HTTP surface for Enterprise Architecture (Part 9)."""

from __future__ import annotations

from flask import Blueprint, jsonify

bp = Blueprint("enterprise_api", __name__, url_prefix="/api/enterprise")


@bp.get("/status")
def api_status():
    from engines.enterprise import bootstrap_enterprise, final_architecture_acceptance

    boot = bootstrap_enterprise(run_diagnostics=True)
    accept = final_architecture_acceptance()
    return jsonify({"bootstrap": boot, "acceptance": accept})


@bp.get("/diagnostics")
def api_diagnostics():
    from engines.enterprise.diagnostics import run_self_diagnostics

    return jsonify(run_self_diagnostics())


@bp.get("/knowledge")
def api_knowledge():
    from engines.enterprise.knowledge import build_knowledge_base_index

    return jsonify(build_knowledge_base_index())


@bp.get("/versions")
def api_versions():
    from engines.enterprise.pipeline_versions import collect_pipeline_versions

    return jsonify(collect_pipeline_versions().to_dict())
