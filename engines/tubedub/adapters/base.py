"""Platform module adapters — boundary to legacy code via ApiBus only."""

from __future__ import annotations

from typing import Any

from engines.tubedub.lifecycle import HealthReport, ModuleContext, PlatformModule


class ArchitectureModule(PlatformModule):
    """Base adapter: registers public API; implementation hooks in subclasses."""

    def _on_initialize(self, ctx: ModuleContext) -> None:
        self._register_api(ctx)

    def _on_load(self) -> None:
        pass

    def _on_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"module_id": self.module_id, "status": "architecture_ready", "payload": payload}

    def _on_stop(self) -> None:
        pass

    def _on_dispose(self) -> None:
        from engines.tubedub.api_bus import get_api_bus

        bus = get_api_bus()
        for route in bus.list_routes():
            if route.get("module_id") == self.module_id:
                bus.unregister(route["namespace"], route["method"])

    def _on_health_check(self) -> HealthReport:
        return HealthReport(
            module_id=self.module_id,
            state=self._state.value,
            ok=self._state.value not in ("error", "disposed"),
            message="architecture_ready",
        )

    def _register_api(self, ctx: ModuleContext) -> None:
        from engines.tubedub.api_bus import get_api_bus

        bus = get_api_bus()
        ns = ctx.api_namespace or self.api_namespace

        bus.register(
            ns,
            "status",
            lambda _p: {"module_id": self.module_id, "state": self._state.value},
            module_id=self.module_id,
            description=f"Status of {self.module_id}",
        )
        bus.register(
            ns,
            "health",
            lambda _p: self.health_check().to_dict(),
            module_id=self.module_id,
            description=f"Health check for {self.module_id}",
        )


class CoreModule(ArchitectureModule):
    module_id = "core"
    api_namespace = "core"


class TranslationModule(ArchitectureModule):
    module_id = "translation"
    api_namespace = "translation"
    dependencies = ["core"]

    def _register_api(self, ctx: ModuleContext) -> None:
        super()._register_api(ctx)
        from engines.tubedub.api_bus import get_api_bus

        bus = get_api_bus()

        def _translate(payload: dict[str, Any]) -> dict[str, Any]:
            text = str(payload.get("text") or "")
            src = str(payload.get("src_lang") or "en")
            tgt = str(payload.get("tgt_lang") or "uk")
            try:
                from engines.translation_pipeline import translate_text

                out = translate_text(text, src_lang=src, tgt_lang=tgt)
                return {"text": out, "engine": "translation_pipeline"}
            except Exception as exc:
                return {"text": text, "error": str(exc), "stub": True}

        bus.register(
            "translation",
            "translate",
            _translate,
            module_id=self.module_id,
            description="Translate text (legacy via bus boundary)",
        )


class PipelineModule(ArchitectureModule):
    module_id = "pipeline_platform"
    api_namespace = "pipeline"
    dependencies = ["translation"]

    def _register_api(self, ctx: ModuleContext) -> None:
        super()._register_api(ctx)
        from engines.tubedub.api_bus import get_api_bus

        bus = get_api_bus()

        def _trace(payload: dict[str, Any]) -> dict[str, Any]:
            from engines.pipeline_platform.dev_view import build_dev_pipeline_view

            info = dict(payload.get("info") or {})
            view = build_dev_pipeline_view(info, app_dir=str(ctx.app_dir))
            return {"view": view}

        def _stages(_payload: dict[str, Any]) -> dict[str, Any]:
            from engines.pipeline_platform import platform_status

            return platform_status()

        bus.register("pipeline", "trace", _trace, module_id=self.module_id)
        bus.register("pipeline", "stages", _stages, module_id=self.module_id)


class DubbingModule(ArchitectureModule):
    module_id = "dubbing"
    api_namespace = "dubbing"
    dependencies = ["translation", "tts", "audio", "video"]


class TtsModule(ArchitectureModule):
    module_id = "tts"
    api_namespace = "tts"
    dependencies = ["core"]


class AudioModule(ArchitectureModule):
    module_id = "audio"
    api_namespace = "audio"
    dependencies = ["core"]


class VideoModule(ArchitectureModule):
    module_id = "video"
    api_namespace = "video"
    dependencies = ["core"]


class DubStudioModule(ArchitectureModule):
    module_id = "dub_studio"
    api_namespace = "dub_studio"
    dependencies = ["dubbing", "audio"]

    def _register_api(self, ctx: ModuleContext) -> None:
        super()._register_api(ctx)
        from engines.tubedub.api_bus import get_api_bus

        bus = get_api_bus()

        def _plugins(_p: dict[str, Any]) -> dict[str, Any]:
            from engines.tubedub.plugin_host import get_plugin_host

            return {"plugins": get_plugin_host().list_plugins()}

        bus.register("dub_studio", "plugins", _plugins, module_id=self.module_id)


class ProjectModule(ArchitectureModule):
    module_id = "project"
    api_namespace = "project"
    dependencies = ["core"]

    def _register_api(self, ctx: ModuleContext) -> None:
        super()._register_api(ctx)
        from engines.tubedub.api_bus import get_api_bus
        from engines.tubedub.project.store import get_project_store

        bus = get_api_bus()
        store = get_project_store(ctx.app_dir)

        bus.register(
            "project",
            "create",
            lambda p: store.create_empty(title=str(p.get("title") or "New Project")).to_dict(),
            module_id=self.module_id,
        )
        bus.register(
            "project",
            "load",
            lambda p: (store.load(str(p.get("project_id") or "")) or TdProject(project_id="", title="")).to_dict(),
            module_id=self.module_id,
        )
        bus.register(
            "project",
            "list",
            lambda _p: {"projects": store.list_projects()},
            module_id=self.module_id,
        )


from engines.tubedub.project.model import TdProject  # noqa: E402 — used in load handler

ADAPTER_MAP: dict[str, type[ArchitectureModule]] = {
    "core": CoreModule,
    "translation": TranslationModule,
    "pipeline_platform": PipelineModule,
    "dubbing": DubbingModule,
    "tts": TtsModule,
    "audio": AudioModule,
    "video": VideoModule,
    "dub_studio": DubStudioModule,
    "project": ProjectModule,
}


def create_adapter(entry_id: str, adapter_key: str) -> PlatformModule | None:
    cls = ADAPTER_MAP.get(adapter_key or entry_id)
    if not cls:
        stub = ArchitectureModule()
        stub.module_id = entry_id
        stub.api_namespace = entry_id
        return stub
    return cls()
