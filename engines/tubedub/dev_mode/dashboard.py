"""Developer Mode architecture dashboard."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def build_architecture_dashboard(
    app_dir: Path,
    *,
    developer_session: bool = True,
    user_mode: str = "developer",
    task_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from engines.feature_flags.manager import get_feature_manager
    from engines.tubedub.api_bus import get_api_bus
    from engines.tubedub.module_manager import get_module_manager
    from engines.tubedub.plugin_host import get_plugin_host
    from engines.tubedub.release import parse_release_channel

    app_dir = Path(app_dir)
    mgr = get_module_manager(app_dir)
    if not mgr.all_modules():
        mgr.bootstrap()

    fm = get_feature_manager(app_dir)
    features = []
    for rec in fm.all_features():
        ch_raw = getattr(rec, "release_channel", None) or _derive_channel(rec)
        features.append(
            {
                "id": rec.id,
                "label": rec.label,
                "release_channel": ch_raw,
                "enabled": rec.enabled and not rec.auto_disabled,
                "status": rec.status,
                "readiness_pct": rec.readiness_pct,
            }
        )

    pipeline_view = None
    if task_info:
        resp = get_api_bus().call("pipeline", "trace", {"info": task_info}, caller="dev_dashboard")
        if resp.ok:
            pipeline_view = resp.result.get("view")

    bus = get_api_bus()
    return {
        "pipeline": {
            "stages": _pipeline_stages(),
            "trace": pipeline_view,
            "data_route": _data_route(),
        },
        "modules": mgr.snapshot(
            developer_session=developer_session,
            user_mode=user_mode,
        ),
        "feature_flags": features,
        "plugins": get_plugin_host().list_plugins(),
        "api_bus": {
            "routes": bus.list_routes(),
            "recent": bus.recent_log(50),
        },
        "health": mgr.health_all(),
        "logs": _dev_logs(app_dir),
        "models": _models_in_use(mgr),
        "load": _load_summary(mgr),
        "copy_text": _export_text(mgr, features, bus),
    }


def _derive_channel(rec: Any) -> str:
    from engines.tubedub.release import ReleaseChannel

    if getattr(rec, "auto_disabled", False) or not rec.enabled:
        return ReleaseChannel.DISABLED.value
    st = (rec.status or "").upper()
    if st in ("READY", "STABLE"):
        return ReleaseChannel.RELEASE.value
    return ReleaseChannel.DEVELOPER.value


def _pipeline_stages() -> list[dict[str, str]]:
    from engines.pipeline_platform.contract import StageId

    labels = {
        StageId.STT: "STT",
        StageId.TRANSLATION_MANAGER: "Translation Manager",
        StageId.ENTERPRISE_TRANSLATION: "Enterprise Translation",
        StageId.NATURAL_TRANSLATION: "Natural Translation",
        StageId.TRANSLATION_OPTIMIZER: "Translation Optimizer",
        StageId.TIMING_OPTIMIZER: "Timing Optimizer",
        StageId.TTS: "TTS",
        StageId.AUDIO_BUILDER: "Audio Builder",
        StageId.FINAL_MUX: "Final Mux",
    }
    return [{"id": s.value, "label": labels.get(s, s.value)} for s in StageId]


def _data_route() -> list[str]:
    return [
        "input.media",
        "→ pipeline.stt",
        "→ pipeline.translation_manager",
        "→ pipeline.enterprise_translation",
        "→ pipeline.natural_translation",
        "→ pipeline.translation_optimizer",
        "→ pipeline.timing_optimizer",
        "→ pipeline.tts",
        "→ pipeline.audio_builder",
        "→ pipeline.final_mux",
        "→ output.media",
    ]


def _dev_logs(app_dir: Path) -> list[str]:
    log_path = app_dir / "output" / "dev" / "feature_flags" / "developer.log"
    if not log_path.is_file():
        return []
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        return lines[-80:]
    except Exception:
        return []


def _models_in_use(mgr: Any) -> list[str]:
    models: list[str] = []
    for h in mgr.health_all():
        models.extend(h.get("models_in_use") or [])
    return sorted(set(models))


def _load_summary(mgr: Any) -> dict[str, Any]:
    health = mgr.health_all()
    ok = sum(1 for h in health if h.get("ok"))
    return {
        "modules_total": len(health),
        "modules_ok": ok,
        "modules_error": len(health) - ok,
        "avg_latency_ms": round(
            sum(float(h.get("latency_ms") or 0) for h in health) / max(len(health), 1),
            2,
        ),
    }


def _export_text(mgr: Any, features: list[dict], bus: Any) -> str:
    lines = ["TubeDub Architecture Dashboard", ""]
    lines.append("=== Modules ===")
    for m in mgr.snapshot(developer_session=True, user_mode="developer")["modules"]:
        lines.append(
            f"  {m['id']} [{m['release_channel']}] state={m.get('lifecycle_state')} visible={m.get('visible')}"
        )
    lines.append("")
    lines.append("=== Feature Flags ===")
    for f in features:
        lines.append(f"  {f['id']}: {f['release_channel']} enabled={f['enabled']}")
    lines.append("")
    lines.append("=== API Routes ===")
    for r in bus.list_routes():
        lines.append(f"  {r['key']} ({r['module_id']})")
    lines.append("")
    lines.append("=== Pipeline Route ===")
    for step in _data_route():
        lines.append(f"  {step}")
    return "\n".join(lines)
