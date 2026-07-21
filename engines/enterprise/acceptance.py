"""P820 Final Definition of Done — architectural completion check."""

from __future__ import annotations

from typing import Any

from engines.enterprise.diagnostics import run_self_diagnostics
from engines.enterprise.knowledge import assert_evolution_rules, build_knowledge_base_index
from engines.enterprise.pipeline_versions import collect_pipeline_versions
from engines.enterprise.types import MASTER_SPEC_COMPLETE


def final_architecture_acceptance() -> dict[str, Any]:
    """
    VideoMonster_V2 is architecturally complete only if all checks pass.
    """
    checks: dict[str, bool] = {}
    details: dict[str, Any] = {}

    # Semantic Core separated from ASR
    try:
        from engines.semantic_v3 import semantic_core  # noqa: F401

        checks["semantic_core_separated"] = True
    except Exception:
        checks["semantic_core_separated"] = False

    # Translation Core independent of Dub
    try:
        from engines.translation_core.invariants import assert_translation_core_isolated

        assert_translation_core_isolated()
        checks["translation_core_independent"] = True
    except Exception as exc:
        checks["translation_core_independent"] = False
        details["translation"] = str(exc)

    # Decision Policy centralized
    try:
        from engines.decision_policy.invariants import assert_decision_policy_isolated

        assert_decision_policy_isolated()
        checks["decision_policy_centralized"] = True
    except Exception as exc:
        checks["decision_policy_centralized"] = False
        details["decision"] = str(exc)

    # Dub Engine post-lock audio/time
    try:
        from engines.dub_engine_v2.invariants import assert_dub_engine_isolated

        assert_dub_engine_isolated()
        checks["dub_engine_post_lock"] = True
    except Exception as exc:
        checks["dub_engine_post_lock"] = False
        details["dub"] = str(exc)

    # Voice Platform provider-independent
    try:
        from engines.voice_platform.invariants import assert_voice_platform_isolated

        assert_voice_platform_isolated()
        checks["voice_platform_independent"] = True
    except Exception as exc:
        checks["voice_platform_independent"] = False
        details["voice"] = str(exc)

    # Scheduler sole time owner (module present)
    try:
        import engines.scheduler.api as _sched  # noqa: F401

        checks["scheduler_time_owner"] = True
    except Exception:
        try:
            from engines.dub_engine_v2 import scheduler  # noqa: F401

            checks["scheduler_time_owner"] = True
        except Exception:
            checks["scheduler_time_owner"] = False

    # Studio observability
    try:
        from engines.studio_qa import PIPELINE_STAGES, build_studio_qa_bundle

        checks["studio_observability"] = len(PIPELINE_STAGES) >= 11
        details["studio_stages"] = list(PIPELINE_STAGES)
    except Exception:
        checks["studio_observability"] = False

    # Diagnostics reproducibility
    diag = run_self_diagnostics()
    checks["diagnostics_ready"] = bool(diag.get("ok"))
    details["diagnostics"] = diag

    # Plugin SDK extensibility
    try:
        from engines.platform_sdk import PLATFORM_SDK_VERSION, get_public_api

        api = get_public_api()
        checks["plugin_sdk_extensible"] = len(api.list_extension_points()) >= 8
        details["platform_sdk"] = PLATFORM_SDK_VERSION
    except Exception:
        checks["plugin_sdk_extensible"] = False

    # Cloud / enterprise do not break invariants
    try:
        from engines.platform_sdk.security import assert_core_protected

        assert_core_protected()
        checks["cloud_enterprise_safe"] = True
    except Exception:
        checks["cloud_enterprise_safe"] = False

    # Versioned contracts
    try:
        from engines.pipeline_integrity.contract_versions import CONTRACT_VERSIONS

        checks["contracts_versioned"] = len(CONTRACT_VERSIONS) >= 5
    except Exception:
        checks["contracts_versioned"] = False

    # State machine present
    try:
        from engines.pipeline_integrity.pipeline_state import PipelineState

        checks["state_machine"] = len(list(PipelineState)) >= 5
    except Exception:
        checks["state_machine"] = False

    # ADR knowledge base
    evo = assert_evolution_rules()
    checks["adr_documented"] = bool(evo.get("ok")) or evo["knowledge_base"]["adr_count"] >= 12
    details["knowledge"] = evo

    # Pipeline versions stampable
    versions = collect_pipeline_versions().to_dict()
    checks["pipeline_versions"] = bool(versions.get("enterprise_version"))
    details["versions"] = versions

    ok = all(checks.values())
    return {
        "ok": ok,
        "master_spec_complete": MASTER_SPEC_COMPLETE and ok,
        "checks": checks,
        "details": details,
        "knowledge_base": build_knowledge_base_index(),
    }
