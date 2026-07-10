"""Dub Studio plugin host — delegates to unified TubeDub PluginHost."""

from __future__ import annotations

from engines.tubedub.plugin_host import (
    PluginHost,
    PluginKind,
    PluginRecord,
    get_plugin_host,
)

__all__ = [
    "PluginHost",
    "PluginKind",
    "PluginRecord",
    "get_plugin_host",
]

# Backward-compatible aliases for dub_studio code
PluginBackend = PluginKind
PluginDescriptor = PluginRecord


def register_external_plugin(desc: PluginRecord) -> None:
    get_plugin_host().register(desc)


def set_vst_bridge(fn) -> None:
    get_plugin_host().set_vst_bridge(fn)


def list_all_plugins() -> list:
    host = get_plugin_host()
    host.import_dub_studio_fx()
    return host.list_plugins()


def resolve_plugin(plugin_id: str):
    host = get_plugin_host()
    rec = host.get(plugin_id)
    if not rec:
        return None
    return rec
