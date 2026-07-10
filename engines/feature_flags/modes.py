"""User modes: Basic / Pro / Developer."""

from __future__ import annotations

from typing import Literal

UserMode = Literal["basic", "pro", "developer"]

READY_STATUSES = frozenset({"READY", "stable"})
BETA_STATUSES = frozenset({"BETA", "beta", "ALPHA", "alpha"})
DEV_STATUSES = frozenset(
    {
        "DEVELOPMENT",
        "development",
        "EXPERIMENTAL",
        "experimental",
        "NOT_IMPLEMENTED",
        "DISABLED",
        "disabled",
    }
)


def normalize_mode(raw: str | None) -> UserMode:
    v = (raw or "basic").strip().lower()
    if v in ("dev", "developer", "development"):
        return "developer"
    if v in ("pro", "professional", "advanced"):
        return "pro"
    if v in ("simple", "basic", "user"):
        return "basic"
    return "basic"


def visible_for_mode(
    *,
    status: str,
    enabled: bool,
    feature_modes: list[str],
    user_mode: UserMode,
    developer_session: bool,
    show_beta: bool = False,
) -> bool:
    if not enabled:
        return developer_session and user_mode == "developer"
    st = (status or "").upper()
    fm = [m.lower() for m in (feature_modes or ["basic", "pro", "developer"])]

    if user_mode == "developer" and developer_session:
        return True

    if user_mode == "developer" and not developer_session:
        user_mode = "pro"

    if user_mode == "pro":
        if st in READY_STATUSES or st == "READY":
            return "pro" in fm or "basic" in fm
        if st in BETA_STATUSES and show_beta:
            return "pro" in fm
        return False

    # basic
    if st in READY_STATUSES or st == "READY":
        return "basic" in fm
    return False
