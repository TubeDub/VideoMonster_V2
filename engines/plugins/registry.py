"""Plugin registry — ordered chain with real ffmpeg effects."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from engines.plugins.base import AudioPlugin, PluginParams
from engines.plugins.effects import LoudnessNormalizePlugin, SimpleCompressorPlugin

logger = logging.getLogger(__name__)

_LOCK = threading.RLock()
_REGISTRY: dict[str, AudioPlugin] = {}
_DEFAULT_ORDER = ["eq", "compressor"]


def _ensure_plugins() -> None:
    if _REGISTRY:
        return
    register(LoudnessNormalizePlugin())
    register(SimpleCompressorPlugin())


def register(plugin: AudioPlugin) -> None:
    with _LOCK:
        _REGISTRY[plugin.plugin_id] = plugin


def get(plugin_id: str) -> AudioPlugin | None:
    _ensure_plugins()
    return _REGISTRY.get(plugin_id)


def list_plugins() -> list[dict[str, Any]]:
    _ensure_plugins()
    return [p.describe() for p in _REGISTRY.values()]


def default_order_path(app_dir: Path) -> Path:
    return Path(app_dir) / "data" / "plugin_order.json"


def load_order(app_dir: Path, project_order: list[str] | None = None) -> list[str]:
    if project_order:
        return [str(x) for x in project_order]
    path = default_order_path(app_dir)
    if not path.is_file():
        return list(_DEFAULT_ORDER)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        order = data.get("order") or _DEFAULT_ORDER
        return [str(x) for x in order]
    except Exception:
        return list(_DEFAULT_ORDER)


def save_order(app_dir: Path, order: list[str], *, project_id: str | None = None) -> None:
    path = default_order_path(app_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {"order": order}
    if project_id:
        payload["project_id"] = project_id
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def process_chain(
    audio_path: str | Path,
    app_dir: Path,
    *,
    order: list[str] | None = None,
    params: dict[str, dict[str, Any]] | None = None,
    project_order: list[str] | None = None,
) -> str:
    _ensure_plugins()
    chain = order or load_order(app_dir, project_order=project_order)
    cur = str(audio_path)
    params = params or {}
    for pid in chain:
        plugin = _REGISTRY.get(pid)
        if not plugin:
            continue
        pp = PluginParams(values=dict(params.get(pid) or {}))
        cur = plugin.process(cur, pp)
    return cur
