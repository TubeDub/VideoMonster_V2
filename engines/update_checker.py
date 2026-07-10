"""Проверка и загрузка обновлений TubeDub."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from engines.app_version import APP_VERSION

_MANIFEST_ENV = "VM_UPDATE_MANIFEST_URL"
_DEFAULT_MANIFEST = "https://raw.githubusercontent.com/tubedub/releases/main/update_manifest.json"
_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def _parse_version(v: str) -> tuple[int, int, int]:
    m = _VERSION_RE.search(v or "")
    if not m:
        return (0, 0, 0)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def compare_versions(current: str, remote: str) -> int:
    """-1 если current < remote, 0 если равны, 1 если current > remote."""
    a, b = _parse_version(current), _parse_version(remote)
    if a < b:
        return -1
    if a > b:
        return 1
    return 0


def _load_local_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _fetch_json(url: str, timeout: float = 12.0) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": f"TubeDub/{APP_VERSION}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check_for_update(app_dir: Path) -> dict[str, Any]:
    current = APP_VERSION
    local = app_dir / "data" / "update_manifest.json"
    env_url = os.environ.get(_MANIFEST_ENV, "").strip()
    try:
        if env_url:
            manifest = _fetch_json(env_url)
            url = env_url
        elif local.is_file():
            manifest = _load_local_manifest(local)
            url = str(local)
        else:
            url = _DEFAULT_MANIFEST
            manifest = _fetch_json(url)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        return {
            "ok": False,
            "current": current,
            "error": str(e),
            "manifest_url": url,
        }

    remote = str(manifest.get("version", "")).strip()
    if not remote:
        return {"ok": False, "current": current, "error": "manifest missing version"}

    newer = compare_versions(current, remote) < 0
    return {
        "ok": True,
        "current": current,
        "latest": remote,
        "update_available": newer,
        "download_url": manifest.get("download_url") or manifest.get("setup_url") or "",
        "notes": manifest.get("notes") or manifest.get("changelog") or "",
        "mandatory": bool(manifest.get("mandatory")),
        "manifest_url": url,
    }


def download_update(app_dir: Path, download_url: str) -> dict[str, Any]:
    if not download_url:
        return {"ok": False, "error": "download_url empty"}

    updates_dir = app_dir / "output" / "updates"
    updates_dir.mkdir(parents=True, exist_ok=True)
    name = download_url.rstrip("/").split("/")[-1] or "TubeDub_Setup.exe"
    if not name.lower().endswith(".exe"):
        name = "TubeDub_Setup.exe"
    dest = updates_dir / name

    req = urllib.request.Request(
        download_url,
        headers={"User-Agent": f"TubeDub/{APP_VERSION}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            dest.write_bytes(resp.read())
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return {"ok": False, "error": str(e)}

    return {"ok": True, "path": str(dest), "filename": dest.name}


def launch_installer(installer_path: str, silent: bool = True) -> dict[str, Any]:
    path = Path(installer_path)
    if not path.is_file():
        return {"ok": False, "error": "installer not found"}

    args = [str(path)]
    if silent:
        args.append("/VERYSILENT")
        args.append("/SUPPRESSMSGBOXES")
        args.append("/NORESTART")

    try:
        subprocess.Popen(args, close_fds=True)
    except OSError as e:
        return {"ok": False, "error": str(e)}

    return {"ok": True, "message": "installer started"}


def apply_update(app_dir: Path, download_url: str) -> dict[str, Any]:
    dl = download_update(app_dir, download_url)
    if not dl.get("ok"):
        return dl
    launched = launch_installer(dl["path"])
    if not launched.get("ok"):
        return launched
    return {
        "ok": True,
        "path": dl["path"],
        "restart_required": True,
        "message": "Обновление загружено. Установщик запущен — перезапустите TubeDub после завершения.",
    }
