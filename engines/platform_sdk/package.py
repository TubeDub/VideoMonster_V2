"""P712 Plugin Store Format — .vmplugin packages."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

from engines.platform_sdk.types import PluginDescriptor
from engines.platform_sdk.validator import sign_payload, validate_plugin


MANIFEST_NAME = "manifest.json"


def build_vmplugin(
    out_path: Path | str,
    *,
    descriptor: PluginDescriptor,
    code_dir: Path | str | None = None,
    assets: dict[str, bytes] | None = None,
    documentation: str = "",
    secret: str | None = None,
) -> Path:
    """
    Create plugin.vmplugin containing:
    Manifest, Version, Signature, Assets, Code, Documentation.
    """
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = descriptor.to_dict()
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    signature = sign_payload(manifest_bytes, secret) if secret else ""

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(MANIFEST_NAME, manifest_bytes)
        zf.writestr("VERSION", descriptor.version.encode("utf-8"))
        zf.writestr("SIGNATURE", signature.encode("utf-8"))
        zf.writestr("Documentation.md", (documentation or descriptor.description or "").encode("utf-8"))
        if assets:
            for name, blob in assets.items():
                zf.writestr(f"Assets/{name}", blob)
        if code_dir:
            root = Path(code_dir)
            if root.is_dir():
                for f in root.rglob("*"):
                    if f.is_file():
                        zf.write(f, arcname=f"Code/{f.relative_to(root).as_posix()}")
            elif root.is_file():
                zf.write(root, arcname=f"Code/{root.name}")
    return path


def read_vmplugin(path: Path | str, *, secret: str | None = None) -> dict[str, Any]:
    zpath = Path(path)
    with zipfile.ZipFile(zpath, "r") as zf:
        names = zf.namelist()
        manifest_bytes = zf.read(MANIFEST_NAME)
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        version = zf.read("VERSION").decode("utf-8").strip() if "VERSION" in names else ""
        signature = zf.read("SIGNATURE").decode("utf-8").strip() if "SIGNATURE" in names else ""
        docs = zf.read("Documentation.md").decode("utf-8") if "Documentation.md" in names else ""
        code_files = [n for n in names if n.startswith("Code/")]
        asset_files = [n for n in names if n.startswith("Assets/")]

    desc = PluginDescriptor.from_dict(manifest)
    validation = validate_plugin(
        desc,
        signature=signature or None,
        public_key_hmac=secret,
        payload_bytes=manifest_bytes if secret else None,
    )
    return {
        "descriptor": desc.to_dict(),
        "version": version or desc.version,
        "signature": signature,
        "documentation": docs,
        "code_files": code_files,
        "asset_files": asset_files,
        "validation": validation,
        "trust": validation.get("trust"),
    }


def extract_vmplugin(path: Path | str, dest: Path | str) -> Path:
    dest_p = Path(dest)
    dest_p.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "r") as zf:
        zf.extractall(dest_p)
    return dest_p
