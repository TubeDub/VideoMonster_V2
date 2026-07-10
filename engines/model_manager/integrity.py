"""Integrity checks and temp cleanup."""

from __future__ import annotations

from pathlib import Path

from engines.model_manager.storage import dir_size, hub_dir, tmp_dir, get_storage_root


def model_id_to_folder(model_id: str) -> str:
    return "models--" + model_id.replace("/", "--")


def folder_to_model_id(folder_name: str) -> str:
    if not folder_name.startswith("models--"):
        return folder_name
    parts = folder_name[len("models--") :].split("--", 1)
    if len(parts) == 2:
        return f"{parts[0]}/{parts[1]}"
    return folder_name


def verify_hf_model(app_dir: Path, model_id: str) -> bool:
    folder = hub_dir(app_dir) / model_id_to_folder(model_id)
    if folder.is_dir():
        snaps = folder / "snapshots"
        if snaps.is_dir():
            for snap in snaps.iterdir():
                if not snap.is_dir():
                    continue
                for name in ("model.safetensors", "pytorch_model.bin", "model.bin"):
                    if (snap / name).is_file():
                        return True
        if any(folder.rglob("*.safetensors")):
            return True
        if any(folder.rglob("*.bin")):
            return True
    try:
        from huggingface_hub import try_to_load_from_cache

        for name in ("config.json", "pytorch_model.bin", "model.safetensors"):
            if try_to_load_from_cache(model_id, name) is not None:
                return True
    except Exception:
        pass
    return False


def verify_whisper(app_dir: Path, size: str) -> bool:
    root = hub_dir(app_dir)
    if not root.is_dir():
        return False
    direct = root / f"models--Systran--faster-whisper-{size}"
    if direct.is_dir():
        snaps = direct / "snapshots"
        if snaps.is_dir():
            for snap in snaps.iterdir():
                if snap.is_dir() and (
                    any(snap.glob("*.bin")) or (snap / "model.bin").is_file()
                ):
                    return True
    markers = (size, f"faster-whisper-{size}", f"models--Systran--faster-whisper-{size}")
    for child in root.iterdir():
        if not child.is_dir():
            continue
        name = child.name.lower()
        if any(m.lower() in name for m in markers):
            snaps = child / "snapshots"
            if snaps.is_dir():
                for snap in snaps.iterdir():
                    if snap.is_dir() and any(snap.glob("*.bin")):
                        return True
    return False


def cleanup_temp_files(app_dir: Path) -> dict:
    removed = 0
    freed = 0
    for root in (hub_dir(app_dir), tmp_dir(app_dir), get_storage_root(app_dir)):
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            try:
                if path.is_file() and (
                    path.name.endswith(".incomplete")
                    or path.suffix == ".tmp"
                    or path.name.startswith(".download")
                ):
                    freed += path.stat().st_size
                    path.unlink()
                    removed += 1
            except OSError:
                pass
        for path in sorted(root.rglob("*"), reverse=True):
            try:
                if path.is_dir() and not any(path.iterdir()):
                    path.rmdir()
            except OSError:
                pass
    return {"removed_files": removed, "removed_bytes": freed}


def artifact_path(app_dir: Path, component_id: str, variant: str) -> Path:
    return get_storage_root(app_dir) / "components" / component_id / variant.replace("/", "_")
