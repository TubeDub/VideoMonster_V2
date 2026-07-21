"""Enterprise Architecture orchestrator — Master Spec Part 9 (Final)."""

from __future__ import annotations

import logging
from typing import Any

from engines.enterprise.acceptance import final_architecture_acceptance
from engines.enterprise.configuration import get_config_store
from engines.enterprise.diagnostics import run_self_diagnostics
from engines.enterprise.distributed import LocalTaskOrchestrator
from engines.enterprise.feature_flags import list_enterprise_flags
from engines.enterprise.governance import run_release_governance
from engines.enterprise.knowledge import build_knowledge_base_index
from engines.enterprise.pipeline_versions import collect_pipeline_versions, stamp_project_versions
from engines.enterprise.types import ENTERPRISE_VERSION, MASTER_SPEC_COMPLETE

logger = logging.getLogger("tubedub.enterprise")


def bootstrap_enterprise(*, run_diagnostics: bool = True) -> dict[str, Any]:
    """Initialize enterprise layer without modifying Core engines."""
    store = get_config_store()
    versions = collect_pipeline_versions().to_dict()
    diag = run_self_diagnostics() if run_diagnostics else {"ok": True, "skipped": True}
    orch = LocalTaskOrchestrator()
    graph = orch.plan("bootstrap")
    logger.info(
        "Enterprise %s bootstrapped domains=%d stages=%d",
        ENTERPRISE_VERSION,
        len(store.list_domains()),
        len(graph.tasks),
    )
    return {
        "version": ENTERPRISE_VERSION,
        "master_spec_complete_flag": MASTER_SPEC_COMPLETE,
        "domains": store.list_domains(),
        "versions": versions,
        "feature_flags": list_enterprise_flags(),
        "diagnostics": diag,
        "task_graph_stages": len(graph.tasks),
        "knowledge_base": {
            "adr_count": build_knowledge_base_index()["adr_count"],
            "pipeline": build_knowledge_base_index()["pipeline"],
        },
    }


def enterprise_status() -> dict[str, Any]:
    acceptance = final_architecture_acceptance()
    return {
        "version": ENTERPRISE_VERSION,
        "bootstrap": bootstrap_enterprise(run_diagnostics=False),
        "acceptance": acceptance,
        "governance_quick": run_release_governance(quick=True),
    }


def prepare_project_info(info: dict[str, Any]) -> dict[str, Any]:
    """Stamp versions + migrate schema for enterprise-ready project open."""
    from engines.enterprise.migration import open_project_compatible

    migrated = open_project_compatible(info)
    stamp_project_versions(migrated)
    return migrated
