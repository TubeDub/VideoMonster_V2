"""Master Spec Part 1 Foundations — architecture tests."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_principles_and_invariants_catalog():
    from engines.pipeline_integrity.foundations import (
        INVARIANTS,
        PRINCIPLES,
        SINGLE_OWNERS,
        assert_invariant_catalog_complete,
        foundations_report,
        owner_of_entity,
    )

    assert_invariant_catalog_complete()
    assert len(PRINCIPLES) == 9
    assert len(INVARIANTS) == 8
    assert owner_of_entity("Timing") == "Scheduler"
    assert owner_of_entity("Words") == "Recognition"
    report = foundations_report()
    assert report["version"] == "6.0"
    assert "Meaning" in report["pipeline"]


def test_part1_canonical_fsm_path():
    from engines.pipeline_integrity.pipeline_state import (
        PART1_CANONICAL_PATH,
        PipelineState,
        advance_pipeline_state,
        get_pipeline_state,
    )
    from engines.pipeline_integrity.exceptions import PipelineStateError

    info: dict = {}
    for st in PART1_CANONICAL_PATH[1:]:  # skip NEW
        advance_pipeline_state(info, st)
    assert get_pipeline_state(info) == PipelineState.EXPORTED

    # Rollback forbidden
    with pytest.raises(PipelineStateError):
        advance_pipeline_state(info, PipelineState.TRANSLATED)


def test_legacy_state_aliases_normalize():
    from engines.pipeline_integrity.pipeline_state import (
        PipelineState,
        get_pipeline_state,
        parse_pipeline_state,
    )

    assert parse_pipeline_state("TRANSCRIBED") == PipelineState.RECOGNIZED
    assert parse_pipeline_state("TTS_READY") == PipelineState.SPEECH_READY
    assert parse_pipeline_state("OPTIMIZED") == PipelineState.PLANNED
    assert get_pipeline_state({"pipeline_state": "TRANSCRIBED"}) == PipelineState.RECOGNIZED


def test_part1_contracts_versioned_and_catalogued():
    from engines.pipeline_integrity.contract_versions import (
        CONTRACT_VERSION_KEYS,
        contract_catalog,
        require_contract_versions,
        stamp_contract_versions,
    )
    from engines.pipeline_integrity.exceptions import ContractVersionError

    info: dict = {}
    stamped = stamp_contract_versions(info)
    assert "recognition_contract_version" in stamped
    assert "sentence_contract_version" in stamped
    assert "alignment_contract_version" in stamped
    assert "merge_contract_version" in stamped
    assert set(CONTRACT_VERSION_KEYS) <= set(info)

    require_contract_versions(info, full=True)
    catalog = contract_catalog()
    for key, meta in catalog.items():
        assert meta["version"] == 1
        assert meta["owner"]
        assert meta["description"]
        assert meta["migration"]

    bad = dict(info)
    bad["sentence_contract_version"] = 99
    with pytest.raises(ContractVersionError):
        stamp_contract_versions(bad)


def test_single_owner_registry_matches_spec():
    from engines.pipeline_integrity.foundations import SINGLE_OWNERS
    from engines.pipeline_integrity.translation_lock import (
        FIELD_OWNERS,
        assert_owner_may_write,
    )
    from engines.pipeline_integrity.exceptions import TranslationLockError

    assert SINGLE_OWNERS["Timing"] == "Scheduler"
    assert FIELD_OWNERS["start_ms"] == "Scheduler"
    assert FIELD_OWNERS["translated_text"] == "Translation Engine"

    assert_owner_may_write("start_ms", "Scheduler")
    with pytest.raises(TranslationLockError):
        assert_owner_may_write("start_ms", "Translation Engine")


def test_forbidden_cross_layer_imports():
    """Architecture test: Scheduler must not import Translation; Dub must not import LLM."""
    from engines.pipeline_integrity.foundations import FORBIDDEN_IMPORT_EDGES

    def _imports_of(package: str) -> set[str]:
        root = ROOT / package.replace(".", "/")
        if not root.exists():
            return set()
        found: set[str] = set()
        for path in root.rglob("*.py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        found.add(alias.name)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    found.add(node.module)
        return found

    for src_pkg, forbidden_prefix in FORBIDDEN_IMPORT_EDGES:
        imports = _imports_of(src_pkg)
        offenders = [
            m
            for m in imports
            if m == forbidden_prefix or m.startswith(forbidden_prefix + ".")
        ]
        assert not offenders, f"{src_pkg} imports forbidden {offenders}"


def test_foundations_docs_exist():
    assert (ROOT / "docs" / "FOUNDATIONS_PART1.md").is_file()
    assert (ROOT / "docs" / "adr" / "ADR-012-foundations-part1.md").is_file()
