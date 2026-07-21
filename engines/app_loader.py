"""Lazy Flask blueprint loading — fast application cold start."""

from __future__ import annotations

import importlib
import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flask import Flask

logger = logging.getLogger(__name__)

_CORE_BLUEPRINTS: tuple[tuple[str, str], ...] = (
    ("api.translate_api", "bp"),
    ("api.tts_api", "bp"),
    ("api.files_api", "bp"),
    ("api.system_api", "bp"),
    ("api.license_api", "bp"),
    ("api.prepare_api", "bp"),
    ("api.modules_api", "bp"),
    ("api.feature_flags_api", "bp"),
    ("api.pipeline_platform_api", "bp"),
    ("api.plugins_api", "bp"),
    ("api.dev_assistant_api", "bp"),
    ("api.ai_sources_api", "bp"),
    ("api.tubedub_platform_api", "bp"),
    ("api.import_api", "bp"),
    ("api.owner_api", "bp"),
    ("api.openddf_analyzer_api", "bp"),
    ("api.ddf_api", "bp"),
    ("api.ai_core_api", "bp"),
    ("api.planner_api", "bp"),
    ("api.director_api", "bp"),
    ("api.translation_api", "bp"),
    ("api.semantic_api", "bp"),
    ("api.timing_api", "bp"),
    ("api.grammar_api", "bp"),
    ("api.quality_api", "bp"),
    ("api.streamdub_api", "bp"),
)

_HEAVY_BLUEPRINTS: tuple[tuple[str, str], ...] = (
    ("api.reader_api", "bp"),
    ("api.dub_api", "bp"),
    ("api.auto_dub_api", "bp"),
    ("api.projects_api", "bp"),
    ("api.studio_api", "bp"),
    ("api.voice_api", "bp"),
    ("api.ocr_api", "bp"),
    ("api.beta_api", "bp"),
    ("api.model_cache_api", "bp"),
    ("api.models_api", "bp"),
    ("api.storage_api", "bp"),
    ("api.ai_manager_api", "bp"),
    ("api.system_prepare_api", "bp"),
    ("api.owner_components_api", "bp"),
    ("api.dev_api", "bp"),
    ("api.assistant_api", "bp"),
    ("api.recording_api", "bp"),
)

_heavy_lock = threading.Lock()
_heavy_loaded = False


def _import_bp(module_path: str, attr: str):
    mod = importlib.import_module(module_path)
    return getattr(mod, attr)


def register_core_blueprints(app: Flask) -> None:
    for module_path, attr in _CORE_BLUEPRINTS:
        app.register_blueprint(_import_bp(module_path, attr))


def register_heavy_blueprints(app: Flask, *, feature_manager=None) -> None:
    global _heavy_loaded
    with _heavy_lock:
        if _heavy_loaded:
            return
        try:
            for module_path, attr in _HEAVY_BLUEPRINTS:
                try:
                    app.register_blueprint(_import_bp(module_path, attr))
                except Exception as exc:
                    logger.warning("Blueprint %s failed: %s", module_path, exc)

            if feature_manager:
                if feature_manager.blueprint_enabled("platform_api"):
                    from api.platform_api import bp as platform_bp

                    app.register_blueprint(platform_bp)
                if feature_manager.blueprint_enabled("cloud_api"):
                    from api.cloud_api import bp as cloud_bp

                    app.register_blueprint(cloud_bp)
                try:
                    from api.platform_sdk_api import bp as platform_sdk_bp

                    app.register_blueprint(platform_sdk_bp)
                except Exception:
                    pass
                try:
                    from api.enterprise_api import bp as enterprise_bp

                    app.register_blueprint(enterprise_bp)
                except Exception:
                    pass
                if feature_manager.blueprint_enabled("dub_studio_api"):
                    from api.dub_studio_api import bp as dub_studio_bp

                    app.register_blueprint(dub_studio_bp)

            try:
                from engines.stress_test.api import bp as stress_test_bp

                app.register_blueprint(stress_test_bp)
            except Exception as exc:
                logger.warning("stress_test blueprint skipped: %s", exc)
        finally:
            # Mark loaded even if optional blueprints fail — avoids retrying on every
            # request after Flask has already handled its first HTTP dispatch.
            _heavy_loaded = True
            logger.info("Heavy blueprints registered")


def start_background_blueprint_load(app: Flask, *, feature_manager=None) -> None:
    def _run() -> None:
        try:
            register_heavy_blueprints(app, feature_manager=feature_manager)
        except Exception as exc:
            logger.exception("Background blueprint load failed: %s", exc)

    threading.Thread(target=_run, daemon=True, name="vm-heavy-blueprints").start()


def ensure_heavy_blueprints(app: Flask, *, feature_manager=None) -> None:
    if not _heavy_loaded:
        register_heavy_blueprints(app, feature_manager=feature_manager)
