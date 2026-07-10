"""OpenDDF SnapshotGuard unit tests (spec §8.1)."""

from __future__ import annotations

import pytest

from openddf import SnapshotGuard, StageSnapshotIntegrityError, TimelineTracker


def test_allowed_mutation_succeeds():
    target = {"status": "pending", "count": 1}
    tracker = TimelineTracker()
    with SnapshotGuard(target, allowed_mutations={"status", "count"}, context_tracker=tracker):
        target["status"] = "ready"
        target["count"] = 2
    events = tracker.get_events()
    assert any(e["event_name"] == "snapshot_guard_exit" for e in events)


def test_disallowed_mutation_raises():
    target = {"status": "pending", "secret_token": "abc"}
    with pytest.raises(StageSnapshotIntegrityError) as exc_info:
        with SnapshotGuard(target, allowed_mutations={"status"}):
            target["secret_token"] = "def"
    err = exc_info.value
    assert err.field_name == "secret_token"
    assert err.old_value == "abc"
    assert err.new_value == "def"
    assert "status" in err.allowed_mutations
    assert err.location_info.get("file", "unknown") != "unknown"


def test_exception_includes_caller_location():
    target = {"value": 1}

    def mutate() -> None:
        with SnapshotGuard(target, allowed_mutations=set()):
            target["value"] = 2

    with pytest.raises(StageSnapshotIntegrityError) as exc_info:
        mutate()
    loc = exc_info.value.location_info
    assert loc.get("file", "").endswith(".py")
    assert loc.get("line", 0) > 0
    assert loc.get("function") == "mutate"
