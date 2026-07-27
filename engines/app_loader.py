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

# Must load or the process aborts — desktop shell depends on these.
_ESSENTIAL_CORE: frozenset[str] = frozenset(
    {
        "api.system_api",
        "api.files_api",
        "api.license_api",
        "api.prepare_api",
        "api.modules_api",
        "api.feature_flags_api",
        "api.tts_api",
        "api.translate_api",
    }
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
_heavy_failures: list[str] = []
_core_failures: list[str] = []


def _import_bp(module_path: str, attr: str):
    mod = importlib.import_module(module_path)
    return getattr(mod, attr)


def heavy_blueprint_status() -> dict:
    """Surface degraded heavy-API load state for /api/system/check."""
    return {
        "loaded": _heavy_loaded,
        "failures": list(_heavy_failures),
        "degraded": bool(_heavy_failures),
        "core_failures": list(_core_failures),
        "core_degraded": bool(_core_failures),
    }


def register_core_blueprints(app: Flask) -> None:
    """Register core blueprints; non-essential failures degrade instead of crash."""
    _core_failures.clear()
    for module_path, attr in _CORE_BLUEPRINTS:
        try:
            app.register_blueprint(_import_bp(module_path, attr))
        except Exception as exc:
            msg = f"{module_path}: {exc}"
            if module_path in _ESSENTIAL_CORE:
                logger.exception("Essential core blueprint failed: %s", msg)
                raise
            _core_failures.append(msg)
            logger.error("Non-essential core blueprint skipped: %s", msg)
    if _core_failures:
        logger.error(
            "Core blueprints degraded (%d): %s",
            len(_core_failures),
            "; ".join(_core_failures[:5]),
        )


def register_heavy_blueprints(app: Flask, *, feature_manager=None) -> None:
    global _heavy_loaded
    with _heavy_lock:
        if _heavy_loaded:
            return
        failures: list[str] = []
        try:
            for module_path, attr in _HEAVY_BLUEPRINTS:
                try:
                    app.register_blueprint(_import_bp(module_path, attr))
                except Exception as exc:
                    failures.append(f"{module_path}: {exc}")
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
                except Exception as exc:
                    failures.append(f"api.platform_sdk_api: {exc}")
                try:
                    from api.enterprise_api import bp as enterprise_bp

                    app.register_blueprint(enterprise_bp)
                except Exception as exc:
                    failures.append(f"api.enterprise_api: {exc}")
                if feature_manager.blueprint_enabled("dub_studio_api"):
                    from api.dub_studio_api import bp as dub_studio_bp

                    app.register_blueprint(dub_studio_bp)

            try:
                from engines.stress_test.api import bp as stress_test_bp

                app.register_blueprint(stress_test_bp)
            except Exception as exc:
                failures.append(f"engines.stress_test.api: {exc}")
                logger.warning("stress_test blueprint skipped: %s", exc)
        finally:
            _heavy_failures.clear()
            _heavy_failures.extend(failures)
            # Mark loaded even if optional blueprints fail — avoids retrying on every
            # request after Flask has already handled its first HTTP dispatch.
            _heavy_loaded = True
            if failures:
                logger.error(
                    "Heavy blueprints registered with %d failure(s): %s",
                    len(failures),
                    "; ".join(failures[:5]),
                )
            else:
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
