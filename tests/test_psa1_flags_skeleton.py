"""PSA1 — flags default OFF + skeleton no-op; legacy smoke OK."""

from __future__ import annotations

import os

import pytest

from engines.pipeline_integrity.psa_flags import (
    VM_FLAG_IDENTITY_GUARD,
    VM_FLAG_REVISION_MANAGER,
    VM_FLAG_SEGMENT_NORMALIZER,
    VM_FLAG_SLOT_BUDGET,
    identity_guard_flag,
    list_psa1_flags,
    revision_manager_flag,
    segment_normalizer_flag,
    slot_budget_flag,
)
from engines.pipeline_integrity.psa_skeleton import (
    RevisionInvariantError,
    SegmentNormalizerInvariantError,
    SlotBudgetInvariantError,
    skeleton_identity_guard,
    skeleton_revision_manager,
    skeleton_segment_normalizer,
    skeleton_slot_budget,
)
from engines.pipeline_integrity.exceptions import IdentityMismatchError

_PSA1_ENVS = (
    VM_FLAG_IDENTITY_GUARD,
    VM_FLAG_SEGMENT_NORMALIZER,
    VM_FLAG_SLOT_BUDGET,
    VM_FLAG_REVISION_MANAGER,
)


@pytest.fixture
def clear_psa1_env(monkeypatch):
    for key in _PSA1_ENVS:
        monkeypatch.delenv(key, raising=False)
    # Legacy aliases must not force ON
    for key in (
        "VM_IDENTITY_GUARD",
        "VM_SEGMENT_NORMALIZER",
        "VM_SLOT_BUDGET",
        "VM_REVISION_MANAGER",
    ):
        monkeypatch.delenv(key, raising=False)
    yield


def test_psa1_flags_default_off(clear_psa1_env):
    flags = list_psa1_flags()
    assert flags["identity_guard"] is False
    assert flags["segment_normalizer"] is False
    assert flags["slot_budget"] is False
    assert flags["revision_manager"] is False
    assert identity_guard_flag() is False
    assert segment_normalizer_flag() is False
    assert slot_budget_flag() is False
    assert revision_manager_flag() is False


def test_psa1_flag_env_on(monkeypatch, clear_psa1_env):
    monkeypatch.setenv(VM_FLAG_IDENTITY_GUARD, "1")
    assert identity_guard_flag() is True
    assert segment_normalizer_flag() is False


def test_psa1_skeleton_noop_when_flags_off(clear_psa1_env):
    segs = [{"segment_id": "a" * 32, "plain_text": "x"}]
    ig = skeleton_identity_guard(segs, stage="legacy")
    assert ig["enabled"] is False and ig["noop"] is True

    sn = skeleton_segment_normalizer(["Hello"], [{"start": 0, "end": 1000}])
    assert sn["enabled"] is False and sn["noop"] is True
    assert sn["segments"] == ["Hello"]

    sb = skeleton_slot_budget(segs)
    assert sb["enabled"] is False and sb["tts_allowed"] is True

    rm = skeleton_revision_manager(segs[0])
    assert rm["enabled"] is False and rm["noop"] is True


def test_psa1_skeleton_flag_on_still_stub(monkeypatch, clear_psa1_env):
    """PSA1: even when ON, skeleton does not enforce (noop stub)."""
    monkeypatch.setenv(VM_FLAG_IDENTITY_GUARD, "1")
    monkeypatch.setenv(VM_FLAG_SLOT_BUDGET, "true")
    ig = skeleton_identity_guard([{"segment_id": "b" * 32}], stage="t")
    assert ig["enabled"] is True and ig.get("skeleton") is True and ig["noop"] is True
    sb = skeleton_slot_budget([])
    assert sb["enabled"] is True and sb["noop"] is True


def test_psa1_invariant_error_types_exist():
    assert issubclass(IdentityMismatchError, Exception)
    assert issubclass(SegmentNormalizerInvariantError, Exception)
    assert issubclass(SlotBudgetInvariantError, Exception)
    assert issubclass(RevisionInvariantError, Exception)


def test_psa1_legacy_smoke_flags_off(clear_psa1_env):
    """Legacy integrity helpers still work with PSA flags OFF."""
    from engines.pipeline_integrity.segment import ensure_segment_ids, new_segment_id
    from engines.pipeline_integrity.guards import ArchitectureGuard

    rows = [
        {"segment_id": new_segment_id(), "text": "one"},
        {"segment_id": new_segment_id(), "text": "two"},
    ]
    ensure_segment_ids(rows)
    ArchitectureGuard.check(rows, stage="psa1_legacy_smoke")
    from engines.pipeline_integrity.identity_guard import verify_identity_chain
    from engines.pipeline_integrity.slot_budget import compute_slot_budgets
    from engines.pipeline_integrity.segment_normalizer import normalize_segments

    report = verify_identity_chain(rows, stage="psa1_legacy_smoke")
    assert report.get("ok") is True
    assert report.get("enabled") is False

    budget = compute_slot_budgets(rows)
    assert budget.tts_allowed is True

    texts, tm, sn_report = normalize_segments(
        ["Hello"], [{"start": 0, "end": 500}], src_lang="en", tgt_lang="uk"
    )
    assert sn_report.get("enabled") is False
    assert texts == ["Hello"]
