"""Storage paths and HF env configuration."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from engines.model_manager.config import load_config, save_config

logger = logging.getLogger("tubedub.model_manager.storage")

_CONFIGURED = False


def default_storage_root(app_dir: Path) -> Path:
    return app_dir / "models" / "huggingface"


def get_storage_root(app_dir: Path) -> Path:
    cfg = load_config(app_dir)
    custom = str(cfg.get("storage_root") or "").strip()
    if custom:
        return Path(custom)
    return default_storage_root(app_dir)


def hub_dir(app_dir: Path) -> Path:
    return get_storage_root(app_dir) / "hub"


def transformers_dir(app_dir: Path) -> Path:
    return get_storage_root(app_dir) / "transformers"


def tmp_dir(app_dir: Path) -> Path:
    return app_dir / "cache" / "huggingface" / "tmp"


def components_dir(app_dir: Path) -> Path:
    return get_storage_root(app_dir) / "components"


_DIR_SIZE_CACHE: dict[str, tuple[int, float, float]] = {}
_DIR_SIZE_TTL = 300.0


def dir_size(path: Path, *, max_files: int = 8000) -> int:
    """Directory size with cache — avoids repeated full rglob on large model trees."""
    import time

    key = str(path.resolve())
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return 0
    now = time.monotonic()
    cached = _DIR_SIZE_CACHE.get(key)
    if cached and cached[2] == mtime and (now - cached[1]) < _DIR_SIZE_TTL:
        return cached[0]

    total = 0
    if not path.exists():
        return 0
    try:
        if path.is_file():
            total = path.stat().st_size
        else:
            n = 0
            for p in path.rglob("*"):
                if p.is_file():
                    try:
                        total += p.stat().st_size
                    except OSError:
                        pass
                n += 1
                if n >= max_files:
                    break
    except OSError:
        pass
    _DIR_SIZE_CACHE[key] = (total, now, mtime)
    return total


def disk_usage_for_storage(app_dir: Path) -> dict[str, float]:
    root = get_storage_root(app_dir)
    try:
        usage = shutil.disk_usage(str(root.anchor or root))
        return {
            "free_gb": round(usage.free / 1024**3, 2),
            "total_gb": round(usage.total / 1024**3, 2),
        }
    except Exception:
        try:
            usage = shutil.disk_usage(str(app_dir))
            return {
                "free_gb": round(usage.free / 1024**3, 2),
                "total_gb": round(usage.total / 1024**3, 2),
            }
        except Exception:
            return {"free_gb": -1, "total_gb": -1}


def configure(app_dir: Path, *, run_temp_cleanup: bool = True) -> Path:
    """Set HF env vars before any transformers import."""
    global _CONFIGURED
    root = get_storage_root(app_dir)
    hub = hub_dir(app_dir)
    trans = transformers_dir(app_dir)
    tmp = tmp_dir(app_dir)
    comp = components_dir(app_dir)

    for d in (root, hub, trans, tmp, comp):
        d.mkdir(parents=True, exist_ok=True)

    os.environ["HF_HOME"] = str(root)
    os.environ["TRANSFORMERS_CACHE"] = str(trans)
    os.environ["HUGGINGFACE_HUB_CACHE"] = str(hub)
    os.environ["HF_HUB_CACHE"] = str(hub)
    os.environ["HF_DATASETS_CACHE"] = str(root / "datasets")
    os.environ["TMPDIR"] = str(tmp)
    if os.name == "nt":
        os.environ["TEMP"] = str(tmp)
        os.environ["TMP"] = str(tmp)

    _CONFIGURED = True
    logger.info("[ModelManager] storage=%s", root)

    if run_temp_cleanup:
        from engines.model_manager.integrity import cleanup_temp_files

        cleanup_temp_files(app_dir)

    cfg = load_config(app_dir)
    if not cfg.get("storage_wizard_done") and hub.is_dir() and any(hub.iterdir()):
        cfg["storage_wizard_done"] = True
        save_config(app_dir, cfg)

    return root


def is_configured() -> bool:
    return _CONFIGURED


def set_storage_root(app_dir: Path, new_root: Path) -> dict:
    new_root = Path(new_root)
    old = get_storage_root(app_dir)
    if old.resolve() == new_root.resolve():
        return {"ok": True, "moved": False, "path": str(new_root)}

    new_root.mkdir(parents=True, exist_ok=True)
    if old.is_dir() and any(old.iterdir()):
        for item in old.iterdir():
            dest = new_root / item.name
            if dest.exists():
                if dest.is_dir():
                    shutil.rmtree(dest)
                else:
                    dest.unlink()
            shutil.move(str(item), str(dest))

    cfg = load_config(app_dir)
    cfg["storage_root"] = str(new_root)
    save_config(app_dir, cfg)
    configure(app_dir, run_temp_cleanup=False)
    return {"ok": True, "moved": True, "path": str(new_root)}


def list_available_drives() -> list[dict]:
    drives: list[dict] = []
    if os.name == "nt":
        import string

        for letter in string.ascii_uppercase:
            p = Path(f"{letter}:\\")
            if p.is_dir():
                try:
                    u = shutil.disk_usage(str(p))
                    drives.append(
                        {
                            "path": str(p),
                            "label": f"Диск {letter}:",
                            "free_gb": round(u.free / 1024**3, 1),
                            "total_gb": round(u.total / 1024**3, 1),
                        }
                    )
                except OSError:
                    pass
    else:
        u = shutil.disk_usage("/")
        drives.append({"path": "/", "label": "Root", "free_gb": round(u.free / 1024**3, 1), "total_gb": round(u.total / 1024**3, 1)})
    return drives
