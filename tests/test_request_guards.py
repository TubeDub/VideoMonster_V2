"""Destructive storage / prepare request guards."""

from __future__ import annotations

from engines.request_guards import _truthy, destructive_confirm_error, is_local_request


class _FakeReq:
    def __init__(
        self,
        *,
        remote_addr: str = "127.0.0.1",
        headers: dict | None = None,
        json_body: dict | None = None,
        args: dict | None = None,
    ):
        self.remote_addr = remote_addr
        self.headers = headers or {}
        self._json = json_body
        self.args = args or {}

    def get_json(self, silent=True):
        return self._json


def test_is_local_request():
    assert is_local_request(_FakeReq(remote_addr="127.0.0.1"))
    assert is_local_request(_FakeReq(remote_addr="::1"))
    assert not is_local_request(_FakeReq(remote_addr="192.168.1.10"))


def test_require_destructive_confirm_needs_header_and_body():
    denied = destructive_confirm_error(
        _FakeReq(json_body={"confirm": True}),
        {"confirm": True},
    )
    assert denied is not None
    body, code = denied
    assert code == 403
    assert body["error"] == "confirm_header_required"

    ok = destructive_confirm_error(
        _FakeReq(
            json_body={"confirm": True},
            headers={"X-VM-Destructive-Confirm": "1"},
        ),
        {"confirm": True},
    )
    assert ok is None


def test_require_destructive_confirm_rejects_lan():
    denied = destructive_confirm_error(
        _FakeReq(
            remote_addr="10.0.0.5",
            json_body={"confirm": True},
            headers={"X-VM-Destructive-Confirm": "1"},
        ),
        {"confirm": True},
    )
    assert denied is not None
    body, code = denied
    assert code == 403
    assert body["error"] == "localhost_only"


def test_truthy_helpers():
    assert _truthy(True)
    assert _truthy("yes")
    assert not _truthy(False)
    assert not _truthy("")
