"""Component registry, last_used, cleanup suggestions."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engines.model_manager.config import load_config
from engines.model_manager.integrity import folder_to_model_id, verify_hf_model, verify_whisper
from engines.model_manager.labels import label
from engines.model_manager.storage import dir_size, hub_dir


def _registry_path(app_dir: Path) -> Path:
    return app_dir / "data" / "model_cache_registry.json"


def load_registry(app_dir: Path) -> dict[str, Any]:
    path = _registry_path(app_dir)
    if not path.is_file():
        return {"version": 1, "models": {}, "components": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"version": 1, "models": {}, "components": {}}
    except Exception:
        return {"version": 1, "models": {}, "components": {}}


def save_registry(app_dir: Path, data: dict[str, Any]) -> None:
    path = _registry_path(app_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def touch_component(
    app_dir: Path,
    component_id: str,
    variant: str,
    *,
    engine_hint: str = "",
    artifact_id: str = "",
) -> None:
    reg = load_registry(app_dir)
    comps = reg.setdefault("components", {})
    key = f"{component_id}:{variant}"
    entry = comps.setdefault(key, {})
    entry["last_used"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry["component_id"] = component_id
    entry["variant"] = variant
    if engine_hint:
        entry["engine_hint"] = engine_hint
    if artifact_id:
        entry["artifact_id"] = artifact_id
        models = reg.setdefault("models", {})
        models.setdefault(artifact_id, {})["last_used"] = entry["last_used"]
    save_registry(app_dir, reg)


def scan_all_components(app_dir: Path) -> list[dict[str, Any]]:
    reg = load_registry(app_dir)
    comps_meta = reg.get("components") or {}
    models_meta = reg.get("models") or {}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    hub = hub_dir(app_dir)
    if hub.is_dir():
        for child in hub.iterdir():
            if not child.is_dir():
                continue
            if child.name.startswith("models--"):
                artifact = folder_to_model_id(child.name)
                engine = models_meta.get(artifact, {}).get("engine", "")
                if "opus-mt" in artifact:
                    cid, variant, eng = "mt", artifact.split("opus-mt-")[-1], "marian"
                elif "nllb" in artifact.lower():
                    cid, variant, eng = "mt", "nllb", "nllb"
                else:
                    cid, variant, eng = "llm", artifact, engine or "hf"
            elif "whisper" in child.name.lower():
                artifact = child.name
                cid, variant, eng = "whisper", child.name.replace("models--Systran--faster-whisper-", ""), "whisper"
                for s in ("tiny", "base", "small", "medium", "large"):
                    if s in child.name.lower():
                        variant = s
                        break
            else:
                continue

            key = f"{cid}:{variant}"
            if key in seen:
                continue
            seen.add(key)
            size = dir_size(child)
            meta = comps_meta.get(key, {})
            out.append(
                {
                    "id": cid,
                    "variant": variant,
                    "label": label(cid),
                    "artifact_id": artifact if child.name.startswith("models--") else child.name,
                    "engine_hint": eng,
                    "bytes": size,
                    "size_mb": round(size / 1024**2, 1),
                    "version": "1",
                    "last_used": meta.get("last_used") or models_meta.get(artifact, {}).get("last_used", "") if child.name.startswith("models--") else meta.get("last_used", ""),
                    "path": str(child),
                    "status": "ready",
                }
            )

    try:
        import argostranslate.package as argos_pkg

        for pkg in argos_pkg.get_installed_packages():
            variant = f"{pkg.from_code}-{pkg.to_code}"
            key = f"mt:{variant}"
            if key in seen:
                continue
            seen.add(key)
            meta = comps_meta.get(key, {})
            out.append(
                {
                    "id": "mt",
                    "variant": variant,
                    "label": label("mt"),
                    "artifact_id": f"argos-{variant}",
                    "engine_hint": "argos",
                    "bytes": 0,
                    "size_mb": 0,
                    "version": "1",
                    "last_used": meta.get("last_used", ""),
                    "path": "",
                    "status": "ready",
                }
            )
    except Exception:
        pass

    for key, meta in comps_meta.items():
        if key in seen:
            continue
        cid = meta.get("component_id", key.split(":")[0])
        variant = meta.get("variant", key.split(":")[-1] if ":" in key else "")
        out.append(
            {
                "id": cid,
                "variant": variant,
                "label": label(cid),
                "artifact_id": meta.get("artifact_id", ""),
                "engine_hint": meta.get("engine_hint", ""),
                "bytes": 0,
                "size_mb": 0,
                "version": "1",
                "last_used": meta.get("last_used", ""),
                "path": "",
                "status": "ready",
            }
        )

    out.sort(key=lambda x: x.get("last_used", ""), reverse=True)
    return out


def suggest_cleanup(app_dir: Path) -> list[dict[str, Any]]:
    cfg = load_config(app_dir)
    days = int(cfg.get("cleanup_unused_days", 90))
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    suggestions: list[dict[str, Any]] = []

    for comp in scan_all_components(app_dir):
        lu = comp.get("last_used") or ""
        try:
            ts = datetime.fromisoformat(lu.replace("Z", "+00:00")).timestamp() if lu else 0
        except Exception:
            ts = 0
        if ts and ts < cutoff and comp.get("bytes", 0) > 0:
            comp = dict(comp)
            comp["unused_days"] = max(1, int((datetime.now(timezone.utc).timestamp() - ts) / 86400))
            suggestions.append(comp)

    suggestions.sort(key=lambda x: x.get("bytes", 0), reverse=True)
    return suggestions


def lru_candidates(app_dir: Path) -> list[dict[str, Any]]:
    cfg = load_config(app_dir)
    max_b = int(float(cfg.get("max_storage_gb", 10)) * 1024**3)
    comps = scan_all_components(app_dir)
    total = sum(c.get("bytes", 0) for c in comps)
    if total <= max_b:
        return []

    comps.sort(key=lambda x: x.get("last_used", ""))
    over = total - int(max_b * 0.9)
    candidates: list[dict] = []
    freed = 0
    for c in comps:
        if freed >= over:
            break
        candidates.append(c)
        freed += c.get("bytes", 0)
    return candidates
