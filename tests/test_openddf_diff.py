"""OpenDDF DiffAnalyzer unit tests (spec §8.1)."""

from __future__ import annotations

from openddf import DiffAnalyzer


def test_identical_dicts_empty_diff():
    data = {"a": 1, "b": "x"}
    diff = DiffAnalyzer.compute_diff(data, dict(data))
    assert diff["change_count"] == 0
    assert diff["changes"] == []
    assert diff["added"] == {}
    assert diff["removed"] == {}


def test_modified_values():
    before = {"status": "pending", "count": 1}
    after = {"status": "ready", "count": 1}
    diff = DiffAnalyzer.compute_diff(before, after)
    assert diff["change_count"] == 1
    assert diff["changes"][0]["field"] == "status"
    assert diff["changes"][0]["old_value"] == "pending"
    assert diff["changes"][0]["new_value"] == "ready"
    assert diff["changes"][0]["change_type"] == "modified"


def test_added_and_removed_keys():
    before = {"keep": 1, "drop": 2}
    after = {"keep": 1, "new_key": 3}
    diff = DiffAnalyzer.compute_diff(before, after)
    fields = {c["field"]: c["change_type"] for c in diff["changes"]}
    assert fields["drop"] == "removed"
    assert fields["new_key"] == "added"
    assert diff["removed"]["drop"] == 2
    assert diff["added"]["new_key"] == 3
