"""P701 Core Protection + P723 Security + P706 Sandbox helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from engines.platform_sdk.types import CORE_PROTECTED, Permission

# Paths that plugins must never mutate (P701 / P723)
PROTECTED_CORE_ROOTS = (
    "engines/semantic_v3",
    "engines/translation_core",
    "engines/decision_policy",
    "engines/dub_engine_v2",
    "engines/scheduler",
    "engines/studio_qa",
)


class SandboxViolation(PermissionError):
    pass


class PluginSandbox:
    """P706 — plugins interact only through SDK; no direct core mutation."""

    def __init__(self, plugin_id: str, granted: set[str] | None = None) -> None:
        self.plugin_id = plugin_id
        self.granted = set(granted or [])

    def require(self, permission: Permission | str) -> None:
        name = permission.value if isinstance(permission, Permission) else str(permission)
        if name not in self.granted and name not in {p.value for p in Permission if p.value in self.granted}:
            # Also accept enum-name style
            alt = name.replace(" ", "_").upper()
            granted_alts = {g.replace(" ", "_").upper() for g in self.granted}
            if alt not in granted_alts and name not in self.granted:
                raise SandboxViolation(
                    f"Plugin {self.plugin_id} missing permission: {name}"
                )

    def assert_not_core_path(self, path: Path | str) -> None:
        p = Path(path).resolve()
        text = str(p).replace("\\", "/")
        for root in PROTECTED_CORE_ROOTS:
            if root in text:
                raise SandboxViolation(
                    f"Plugin {self.plugin_id} cannot write protected core path: {root}"
                )

    def call_sdk(self, api: Any, method: str, *args: Any, **kwargs: Any) -> Any:
        """Only allowed interaction surface."""
        fn = getattr(api, method, None)
        if not callable(fn):
            raise SandboxViolation(f"Unknown SDK method: {method}")
        return fn(*args, **kwargs)


def assert_core_protected() -> None:
    """P701 — core is declared protected."""
    if not CORE_PROTECTED:
        raise AssertionError("Core protection flag disabled")


def assert_no_core_bypass(import_name: str) -> None:
    """P723 — plugins must not import protected core internals for mutation."""
    forbidden_prefixes = (
        "engines.semantic_v3.phase2",
        "engines.dub_engine_v2.engine",
        "engines.decision_policy.engine",
        "engines.translation_core.lock",
    )
    # Soft: reading types is OK; this guards obvious engine entrypoints in plugin code scans
    for pref in forbidden_prefixes:
        if import_name.startswith(pref):
            raise SandboxViolation(f"Forbidden core import from plugin: {import_name}")
