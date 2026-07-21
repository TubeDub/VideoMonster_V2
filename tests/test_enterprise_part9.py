"""Master Spec Part 9 — Enterprise Architecture tests (Final)."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_bootstrap_enterprise(tmp_path: Path):
    from engines.enterprise import bootstrap_enterprise
    from engines.enterprise.configuration import EnterpriseConfigStore

    # Use isolated config store
    store = EnterpriseConfigStore(root=tmp_path / "cfg")
    assert len(store.list_domains()) >= 10
    store.update("Pipeline", {"mode": "test"}, profile="Movie")
    rec = store.get("Pipeline")
    assert rec.profile == "Movie"
    assert rec.configuration_uuid
    assert rec.rollback_point
    exported = store.export_all(tmp_path / "export.json")
    assert exported.is_file()

    boot = bootstrap_enterprise(run_diagnostics=True)
    assert boot["version"] == "9.0.0"
    assert boot["master_spec_complete_flag"] is True
    assert boot["task_graph_stages"] >= 10


def test_feature_flags_gate_future():
    from engines.enterprise.feature_flags import (
        assert_no_ungated_production_feature,
        list_enterprise_flags,
        require_feature_flag,
    )

    flags = list_enterprise_flags()
    assert flags["Semantic V4"]["enabled"] is False
    with pytest.raises(RuntimeError):
        require_feature_flag("Semantic V4")
    with pytest.raises(RuntimeError):
        assert_no_ungated_production_feature("Semantic V4", production=True)


def test_migration_engine_opens_old_projects():
    from engines.enterprise.migration import MigrationEngine, open_project_compatible

    old = {"segments_data": [{"id": 1}], "pipeline_state": "NEW"}
    migrated = open_project_compatible(old)
    assert migrated["schema_version"] >= 2
    assert "pipeline_version_bundle" in migrated
    eng = MigrationEngine()
    assert eng.needs_migration({"schema_version": 0})


def test_task_orchestration_dry_run():
    from engines.enterprise.distributed import LocalTaskOrchestrator

    orch = LocalTaskOrchestrator()
    orch.plan("proj1")
    result = orch.run(dry_run=True)
    assert result["ok"] is True
    assert len(result["results"]) >= 10
    # Recognition depends on nothing; Export is last
    names = [t["name"] for t in result["results"]]
    assert names[0] == "Recognition"
    assert "Export" in names


def test_self_diagnostics_and_failure_checkpoint(tmp_path: Path):
    from engines.enterprise.diagnostics import (
        ObservabilityPlatform,
        load_failure_checkpoint,
        run_self_diagnostics,
        save_failure_checkpoint,
    )

    diag = run_self_diagnostics()
    assert "checks" in diag

    path = save_failure_checkpoint(
        tmp_path,
        info={"pipeline_state": "PLANNED"},
        meta={"decision_graph": {"records": []}, "timeline": {"units": []}},
        task_graph={"tasks": []},
    )
    assert path.is_file()
    loaded = load_failure_checkpoint(tmp_path)
    assert loaded is not None
    assert loaded.get("decision_graph") is not None

    obs = ObservabilityPlatform()
    obs.record_event("test", {"a": 1})
    obs.record_error("boom", area="test")
    exp = obs.export()
    assert exp["history_len"] == 1


def test_security_privacy(tmp_path: Path):
    from engines.enterprise.security import PrivacyControls, SecretsVault

    vault = SecretsVault(path=tmp_path / "secrets.json")
    vault.put("api", "super-secret-value")
    assert vault.verify("api", "super-secret-value")
    assert not vault.verify("api", "wrong")
    assert vault.assert_no_plaintext_in_repo() == []

    privacy = PrivacyControls(root=tmp_path / "privacy")
    ud = privacy.user_dir("user1")
    proj = ud / "p1"
    proj.mkdir()
    (proj / "x.json").write_text("{}", encoding="utf-8")
    assert privacy.delete_project("user1", "p1") is True
    assert privacy.can_export() is True


def test_knowledge_base_and_evolution():
    from engines.enterprise.knowledge import assert_evolution_rules, build_knowledge_base_index

    kb = build_knowledge_base_index()
    assert kb["adr_count"] >= 12
    assert "Semantic Core" in kb["pipeline"]
    assert "Enterprise Services" in kb["pipeline"]
    assert len(kb["evolution_rules"]) >= 5
    evo = assert_evolution_rules()
    assert evo["knowledge_base"]["adr_count"] >= 12


def test_pipeline_versions_stamp():
    from engines.enterprise.pipeline_versions import collect_pipeline_versions, stamp_project_versions

    v = collect_pipeline_versions().to_dict()
    assert v["enterprise_version"] == "9.0.0"
    info: dict = {}
    stamp_project_versions(info)
    assert "pipeline_version_bundle" in info


def test_final_architecture_acceptance():
    from engines.enterprise import final_architecture_acceptance

    result = final_architecture_acceptance()
    assert result["checks"]["semantic_core_separated"] is True
    assert result["checks"]["translation_core_independent"] is True
    assert result["checks"]["decision_policy_centralized"] is True
    assert result["checks"]["dub_engine_post_lock"] is True
    assert result["checks"]["voice_platform_independent"] is True
    assert result["checks"]["plugin_sdk_extensible"] is True
    assert result["checks"]["contracts_versioned"] is True
    assert result["checks"]["state_machine"] is True
    # Full master_spec_complete depends on all checks including diagnostics
    assert "master_spec_complete" in result
