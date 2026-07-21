"""P708 Extension Points + P709 Public API — stable surface only."""

from __future__ import annotations

from typing import Any, Callable

from engines.platform_sdk.event_bus import get_platform_bus
from engines.platform_sdk.types import ExtensionPoint, PLATFORM_SDK_VERSION, PlatformEvent

Handler = Callable[..., Any]

_EXTENSIONS: dict[str, list[dict[str, Any]]] = {e.value: [] for e in ExtensionPoint}


def register_extension(
    point: ExtensionPoint | str,
    *,
    plugin_id: str,
    handler: Handler,
    meta: dict[str, Any] | None = None,
) -> None:
    key = point.value if isinstance(point, ExtensionPoint) else str(point)
    if key not in _EXTENSIONS:
        _EXTENSIONS[key] = []
    _EXTENSIONS[key].append(
        {"plugin_id": plugin_id, "handler": handler, "meta": meta or {}}
    )


def list_extensions(point: ExtensionPoint | str | None = None) -> dict[str, list[dict[str, Any]]]:
    if point is None:
        return {
            k: [{"plugin_id": x["plugin_id"], "meta": x["meta"]} for x in v]
            for k, v in _EXTENSIONS.items()
        }
    key = point.value if isinstance(point, ExtensionPoint) else str(point)
    return {
        key: [{"plugin_id": x["plugin_id"], "meta": x["meta"]} for x in _EXTENSIONS.get(key, [])]
    }


def invoke_extensions(point: ExtensionPoint | str, *args: Any, **kwargs: Any) -> list[Any]:
    key = point.value if isinstance(point, ExtensionPoint) else str(point)
    results = []
    for entry in _EXTENSIONS.get(key, []):
        try:
            results.append(entry["handler"](*args, **kwargs))
        except Exception as exc:
            results.append({"error": str(exc), "plugin_id": entry["plugin_id"]})
    return results


class PublicAPI:
    """
    P709 — stable public API. Forbidden to reach internal classes through this façade.
    """

    version = PLATFORM_SDK_VERSION

    def emit(self, event: str | PlatformEvent, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return get_platform_bus().publish(event, payload)

    def subscribe(self, event: str | PlatformEvent, listener: Callable) -> None:
        get_platform_bus().subscribe(event, listener)

    def register_extension(
        self,
        point: str | ExtensionPoint,
        plugin_id: str,
        handler: Handler,
        meta: dict[str, Any] | None = None,
    ) -> None:
        register_extension(point, plugin_id=plugin_id, handler=handler, meta=meta)

    def list_extension_points(self) -> list[str]:
        return [e.value for e in ExtensionPoint]

    def get_plugin_manager(self):
        from engines.platform_sdk.manager import get_plugin_manager

        return get_plugin_manager()

    def synthesize_via_voice_platform(self, request_dict: dict[str, Any]) -> dict[str, Any]:
        """Delegate to Voice Platform without exposing TTS providers."""
        from engines.voice_platform import SynthesisRequest, synthesize

        result = synthesize(SynthesisRequest(**request_dict))
        return result.to_dict()

    def build_studio_qa(self, meta: dict[str, Any], info: dict[str, Any] | None = None) -> dict[str, Any]:
        from engines.studio_qa import build_studio_qa_bundle

        return build_studio_qa_bundle(meta=meta, info=info or {}).to_dict()

    def cloud(self):
        from engines.platform_sdk.cloud import get_cloud_facade

        return get_cloud_facade()

    def marketplace(self):
        from engines.platform_sdk.marketplace import get_marketplace

        return get_marketplace()

    def webhooks(self):
        from engines.platform_sdk.webhooks import get_webhook_registry

        return get_webhook_registry()

    def tokens(self):
        from engines.platform_sdk.tokens import get_token_store

        return get_token_store()

    def settings_profiles(self):
        from engines.platform_sdk.settings_profiles import list_profiles

        return list_profiles()

    def team(self):
        from engines.platform_sdk.team import get_team_service

        return get_team_service()


_API: PublicAPI | None = None


def get_public_api() -> PublicAPI:
    global _API
    if _API is None:
        _API = PublicAPI()
    return _API
