"""P710–P713 Validator + Digital Signature trust."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

from engines.platform_sdk.types import PluginDescriptor, TrustLevel


def parse_semver(v: str) -> tuple[int, int, int]:
    parts = str(v or "0").split(".")
    nums = []
    for p in parts[:3]:
        try:
            nums.append(int("".join(c for c in p if c.isdigit()) or "0"))
        except ValueError:
            nums.append(0)
    while len(nums) < 3:
        nums.append(0)
    return nums[0], nums[1], nums[2]


def version_in_range(version: str, min_v: str, max_v: str) -> bool:
    v = parse_semver(version)
    return parse_semver(min_v) <= v <= parse_semver(max_v)


def validate_plugin(
    descriptor: PluginDescriptor,
    *,
    core_version: str = "6.0.0",
    required_contracts: list[str] | None = None,
    signature: str | None = None,
    public_key_hmac: str | None = None,
    payload_bytes: bytes | None = None,
) -> dict[str, Any]:
    """P711 — contracts, signature, compatibility, permissions, dependencies."""
    issues: list[str] = []
    if not descriptor.plugin_id:
        issues.append("missing_plugin_id")
    if not descriptor.version:
        issues.append("missing_version")
    if not version_in_range(core_version, descriptor.min_core_version, descriptor.max_core_version):
        issues.append("core_version_incompatible")
    if required_contracts:
        missing = [c for c in required_contracts if c not in (descriptor.contracts or [])]
        if missing:
            issues.append(f"missing_contracts:{','.join(missing)}")
    if not descriptor.permissions:
        issues.append("permissions_not_declared")
    # Dependencies presence is checked by manager; here only format
    for dep in descriptor.dependencies:
        if not str(dep).strip():
            issues.append("empty_dependency")

    trust = TrustLevel.UNKNOWN
    if signature and public_key_hmac and payload_bytes is not None:
        expected = hmac.new(
            public_key_hmac.encode("utf-8"),
            payload_bytes,
            hashlib.sha256,
        ).hexdigest()
        if hmac.compare_digest(expected, signature):
            trust = TrustLevel.VERIFIED
        else:
            trust = TrustLevel.BLOCKED
            issues.append("bad_signature")
    elif signature:
        trust = TrustLevel.UNKNOWN

    ok = trust != TrustLevel.BLOCKED and not any(
        i.startswith("core_version") or i == "missing_plugin_id" or i == "bad_signature"
        for i in issues
    )
    # Soft warnings (permissions) don't block if id/version ok
    hard = [i for i in issues if i in {"missing_plugin_id", "core_version_incompatible", "bad_signature"}]
    return {
        "ok": len(hard) == 0,
        "issues": issues,
        "trust": trust.value,
        "warnings": [i for i in issues if i not in hard],
    }


def sign_payload(payload: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def trust_from_manifest(path: Path) -> TrustLevel:
    """Infer trust for installed plugin folder."""
    sig = path / "SIGNATURE"
    if not sig.is_file():
        return TrustLevel.UNKNOWN
    # Presence of signature file → Verified only after validate_plugin
    return TrustLevel.UNKNOWN
