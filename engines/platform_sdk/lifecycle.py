"""P704 Plugin Lifecycle state machine."""

from __future__ import annotations

from engines.platform_sdk.types import LIFECYCLE_ORDER, PluginLifecycle


class LifecycleError(ValueError):
    pass


# Allowed transitions (forward + pause/stop/remove branches)
_ALLOWED: dict[PluginLifecycle, set[PluginLifecycle]] = {
    PluginLifecycle.INSTALLED: {PluginLifecycle.VERIFIED, PluginLifecycle.REMOVED},
    PluginLifecycle.VERIFIED: {PluginLifecycle.LOADED, PluginLifecycle.REMOVED},
    PluginLifecycle.LOADED: {PluginLifecycle.INITIALIZED, PluginLifecycle.STOPPED, PluginLifecycle.REMOVED},
    PluginLifecycle.INITIALIZED: {PluginLifecycle.RUNNING, PluginLifecycle.STOPPED, PluginLifecycle.REMOVED},
    PluginLifecycle.RUNNING: {PluginLifecycle.PAUSED, PluginLifecycle.STOPPED, PluginLifecycle.REMOVED},
    PluginLifecycle.PAUSED: {PluginLifecycle.RUNNING, PluginLifecycle.STOPPED, PluginLifecycle.REMOVED},
    PluginLifecycle.STOPPED: {PluginLifecycle.LOADED, PluginLifecycle.REMOVED, PluginLifecycle.INITIALIZED},
    PluginLifecycle.REMOVED: set(),
}


def parse_lifecycle(value: str | PluginLifecycle) -> PluginLifecycle:
    if isinstance(value, PluginLifecycle):
        return value
    for s in PluginLifecycle:
        if s.value == value or s.name == value:
            return s
    raise LifecycleError(f"Unknown lifecycle state: {value}")


def can_transition(current: PluginLifecycle | str, target: PluginLifecycle | str) -> bool:
    cur = parse_lifecycle(current)
    tgt = parse_lifecycle(target)
    return tgt in _ALLOWED.get(cur, set())


def transition(current: PluginLifecycle | str, target: PluginLifecycle | str) -> PluginLifecycle:
    cur = parse_lifecycle(current)
    tgt = parse_lifecycle(target)
    if not can_transition(cur, tgt):
        raise LifecycleError(f"Illegal lifecycle transition: {cur.value} → {tgt.value}")
    return tgt


def advance(current: PluginLifecycle | str) -> PluginLifecycle:
    """Move to the next linear stage when possible."""
    cur = parse_lifecycle(current)
    idx = LIFECYCLE_ORDER.index(cur)
    for nxt in LIFECYCLE_ORDER[idx + 1 :]:
        if can_transition(cur, nxt):
            return nxt
    raise LifecycleError(f"No forward transition from {cur.value}")
