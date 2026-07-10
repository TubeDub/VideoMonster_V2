import os
import sys
from pathlib import Path
from flask import Flask, render_template
from data.languages import LANGUAGES, VOICES

APP_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(APP_DIR))

from engines.model_manager import configure as configure_model_manager
from engines.model_manager.runtime import set_downloads_permitted

_fast = os.getenv("VM_FAST_START", "").strip().lower() in ("1", "true", "yes", "on")
configure_model_manager(APP_DIR, run_temp_cleanup=not _fast)
set_downloads_permitted(False)

if not _fast and os.getenv("VM_SKIP_STARTUP_STORAGE_AUDIT", "").strip().lower() not in (
    "1",
    "true",
    "yes",
):
    try:
        from engines.storage_cleanup import startup_storage_audit

        startup_storage_audit(APP_DIR)
    except Exception:
        pass

# Storage Manager Phase 1 — cleanup, legacy migration, recovery checkpoint.
if not _fast and os.getenv("VM_SKIP_STORAGE_MANAGER", "").strip().lower() not in (
    "1",
    "true",
    "yes",
):
    try:
        from engines.storage.manager import startup_storage

        _storage_startup = startup_storage(APP_DIR)
        if _storage_startup.get("recovery"):
            import logging as _logging

            _logging.getLogger(__name__).info(
                "Storage recovery available for project %s",
                _storage_startup["recovery"].get("project_id"),
            )
    except Exception:
        pass

from engines.app_logging import setup_app_logging
from engines.app_loader import (
    ensure_heavy_blueprints,
    register_core_blueprints,
    start_background_blueprint_load,
)
from engines.feature_flags.manager import get_feature_manager

setup_app_logging(APP_DIR)

try:
    from engines.app_version import APP_VERSION
    from engines.update_checker import compare_versions
    from engines.update_state import load_update_state, save_update_state

    _ustate = load_update_state(APP_DIR)
    _ustate["installed_version"] = APP_VERSION
    _latest = (_ustate.get("latest_version") or "").strip()
    if _latest and compare_versions(APP_VERSION, _latest) >= 0:
        _ustate["update_available"] = False
    save_update_state(APP_DIR, _ustate)
except Exception:
    pass

app = Flask(
    __name__,
    template_folder=str(APP_DIR / "templates"),
    static_folder=str(APP_DIR / "static"),
    static_url_path="/static",
)
app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("VM_MAX_UPLOAD_MB", "2048")) * 1024 * 1024

register_core_blueprints(app)

_FEATURE_MANAGER = get_feature_manager(APP_DIR)

start_background_blueprint_load(app, feature_manager=_FEATURE_MANAGER)


def _defer_bootstrap() -> None:
    try:
        _FEATURE_MANAGER.bootstrap()
    except Exception:
        pass
    try:
        from engines.tubedub.bootstrap import bootstrap_platform

        bootstrap_platform(APP_DIR)
    except Exception:
        pass


import threading as _threading

_threading.Thread(target=_defer_bootstrap, daemon=True, name="vm-feature-bootstrap").start()

os.chdir(APP_DIR)


@app.before_request
def _ensure_heavy_blueprints_loaded():
    ensure_heavy_blueprints(app, feature_manager=_FEATURE_MANAGER)


@app.before_request
def _guard_module_routes():
    """Block production users from development/disabled module pages."""
    from flask import jsonify, request

    path = request.path or ""
    skip_prefixes = (
        "/static/",
        "/soon/",
        "/api/modules/",
        "/api/features/",
        "/api/system/",
        "/api/license/",
        "/api/owner/",
        "/api/prepare/",
        "/api/dub-studio/",
        "/api/cloud/",
        "/api/dev/",
        "/api/assistant/",
        "/api/recording/",
        "/mini/",
    )
    if path in ("/", "") or any(path.startswith(p) for p in skip_prefixes):
        return None
    try:
        from engines.module_registry.registry import (
            get_registry,
            is_developer_session,
            module_accessible,
            resolve_module_for_path,
        )

        rec = resolve_module_for_path(path, APP_DIR)
        if not rec:
            return None
        dev = is_developer_session(
            request_headers=dict(request.headers),
            request_cookies=dict(request.cookies),
        )
        reg = get_registry(APP_DIR)
        from engines.feature_flags.modes import normalize_mode

        raw_mode = (
            request.headers.get("X-VM-User-Mode")
            or request.headers.get("X-VM-UI-Mode")
            or request.cookies.get("vm_user_mode")
            or ("developer" if dev else "basic")
        )
        user_mode = normalize_mode(raw_mode)
        if module_accessible(
            rec,
            developer_mode=dev,
            show_beta=reg.show_beta_to_users(),
            user_mode=user_mode,
            app_dir=APP_DIR,
        ):
            return None
        if path.startswith("/api/"):
            return jsonify({"ok": False, "error": "Module not available", "module": rec.id}), 403
        from flask import render_template

        hint = ""
        try:
            from engines.owner_first_run import is_owner_host

            if is_owner_host() and rec.developer_only:
                hint = (
                    " Включите режим 🔧 Dev в шапке приложения "
                    "(или перезапустите с VM_DEV_MODE=1)."
                )
        except Exception:
            pass
        return (
            render_template(
                "error.html",
                code=403,
                title="Модуль недоступен",
                description=(
                    f"Раздел «{rec.label('ru')}» находится в статусе {rec.status} "
                    "и скрыт в пользовательском режиме."
                    + hint
                ),
            ),
            403,
        )
    except Exception:
        return None


# Одноразовая инициализация владельца при первом запуске
try:
    from engines.owner_first_run import run_if_needed

    _owner_init_result = run_if_needed()
    if _owner_init_result.get("ran"):
        import logging as _logging

        _logging.getLogger(__name__).info("Owner first-run: %s", _owner_init_result)
except Exception as _owner_err:
    import logging as _logging

    _logging.getLogger(__name__).warning("Owner first-run skipped: %s", _owner_err)


try:
    import threading as _threading

    def _startup_prepare() -> None:
        try:
            from pathlib import Path as _P

            from engines.ai_manager.installer import ensure_backend_headless
            from engines.system_prepare import start_background_prepare

            start_background_prepare()
            ensure_backend_headless(_P(__file__).resolve().parent)
        except Exception as _hl_err:
            import logging as _logging

            _logging.getLogger(__name__).debug("Startup prepare skipped: %s", _hl_err)

    _threading.Thread(target=_startup_prepare, daemon=True, name="tubedub-startup").start()
except Exception:
    pass


# ─────────────────────────────────────────────
#  Основные страницы
# ─────────────────────────────────────────────


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/studio")
def studio():
    return render_template("studio.html", languages=LANGUAGES, voices=VOICES)


@app.route("/voice")
def voice():
    return render_template("voice.html", languages=LANGUAGES, voices=VOICES)


@app.route("/translate")
def translate_page():
    return render_template("translate.html", languages=LANGUAGES)


@app.route("/settings")
def settings():
    return render_template("settings.html", languages=LANGUAGES, voices=VOICES)


@app.route("/ai/sources")
def ai_sources_page():
    return render_template("ai_sources.html")


@app.route("/ai/settings")
def ai_settings_page():
    return render_template("ai_settings.html")


@app.route("/reader")
def reader():
    return render_template("reader.html")


# ─────────────────────────────────────────────
#  Coming Soon pages
# ─────────────────────────────────────────────

_SOON_MODULES: dict[str, dict] = {
    "dub-studio": {
        "id": "dub-studio",
        "icon": "🎙️",
        "name": {"ru": "Dub Studio", "uk": "Dub Studio", "en": "Dub Studio", "de": "Dub Studio"},
        "tagline": {
            "ru": "Профессиональная студия ручного дубляжа",
            "uk": "Професійна студія ручного дублювання",
            "en": "Professional manual dubbing studio",
            "de": "Professionelles manuelles Synchronisierungsstudio",
        },
        "features": {
            "ru": ["Многодорожечный редактор", "Ручная синхронизация звука и видео", "Плагины и эффекты", "Профессиональный монтаж", "Экспорт в любом формате"],
            "uk": ["Багатодоріжковий редактор", "Ручна синхронізація звуку і відео", "Плагіни та ефекти", "Професійний монтаж", "Експорт у будь-якому форматі"],
            "en": ["Multi-track editor", "Manual audio/video sync", "Plugins and effects", "Professional editing", "Export in any format"],
            "de": ["Mehrspuriger Editor", "Manuelle Audio/Video-Synchronisation", "Plugins und Effekte", "Professioneller Schnitt", "Export in jedem Format"],
        },
    },
    "voice-studio": {
        "id": "voice-studio",
        "icon": "🎤",
        "name": {"ru": "Voice Studio", "uk": "Voice Studio", "en": "Voice Studio", "de": "Voice Studio"},
        "tagline": {
            "ru": "Создание и клонирование голосов",
            "uk": "Створення та клонування голосів",
            "en": "Voice creation and cloning",
            "de": "Stimmerstellung und -klonierung",
        },
        "features": {
            "ru": ["Клонирование голоса по образцу", "Редактирование тембра и интонации", "Управление эмоциями голоса", "Создание уникальных голосовых персонажей", "Экспорт голосовой модели"],
            "uk": ["Клонування голосу за зразком", "Редагування тембру та інтонації", "Керування емоціями голосу", "Створення унікальних голосових персонажів", "Експорт голосової моделі"],
            "en": ["Voice cloning from a sample", "Timbre and intonation editing", "Voice emotion control", "Create unique voice characters", "Export voice model"],
            "de": ["Stimmklonierung aus einem Sample", "Klangfarben- und Intonationsbearbeitung", "Stimmungssteuerung der Stimme", "Einzigartige Stimmcharaktere erstellen", "Stimmmodell exportieren"],
        },
    },
    "ai-director": {
        "id": "ai-director",
        "icon": "🎬",
        "name": {"ru": "AI Director", "uk": "AI Director", "en": "AI Director", "de": "AI Director"},
        "tagline": {
            "ru": "Автоматическая AI-режиссура дубляжа",
            "uk": "Автоматична AI-режисура дублювання",
            "en": "Automatic AI dubbing direction",
            "de": "Automatische KI-Synchronisierungsregie",
        },
        "features": {
            "ru": ["Умная синхронизация губ и аудио", "Автоматическое исправление ошибок дубляжа", "Эмоциональный анализ речи", "Адаптация темпа и ритма", "AI-контроль качества финального результата"],
            "uk": ["Розумна синхронізація губ і аудіо", "Автоматичне виправлення помилок дублювання", "Емоційний аналіз мови", "Адаптація темпу і ритму", "AI-контроль якості фінального результату"],
            "en": ["Smart lip and audio sync", "Automatic dubbing error correction", "Emotional speech analysis", "Tempo and rhythm adaptation", "AI quality control of final result"],
            "de": ["Intelligente Lippen- und Audiosynchronisation", "Automatische Synchronisierungsfehlerkorrektur", "Emotionale Sprachanalyse", "Tempo- und Rhythmusanpassung", "KI-Qualitätskontrolle des Ergebnisses"],
        },
    },
    "live": {
        "id": "live",
        "icon": "📡",
        "name": {"ru": "Live Stream", "uk": "Live Stream", "en": "Live Stream", "de": "Live Stream"},
        "tagline": {
            "ru": "Дубляж прямых трансляций в реальном времени",
            "uk": "Дублювання прямих трансляцій у реальному часі",
            "en": "Real-time live stream dubbing",
            "de": "Echtzeit-Live-Stream-Synchronisation",
        },
        "features": {
            "ru": ["Онлайн-перевод и дубляж трансляций", "Поддержка YouTube Live и Twitch", "Поддержка IPTV", "Минимальная задержка звука", "Переключение языков на лету"],
            "uk": ["Онлайн-переклад і дублювання трансляцій", "Підтримка YouTube Live та Twitch", "Підтримка IPTV", "Мінімальна затримка звуку", "Перемикання мов на льоту"],
            "en": ["Online translation and dubbing of streams", "YouTube Live and Twitch support", "IPTV support", "Minimal audio latency", "On-the-fly language switching"],
            "de": ["Online-Übersetzung und Synchronisation von Streams", "YouTube Live und Twitch-Unterstützung", "IPTV-Unterstützung", "Minimale Audiolatenz", "Echtzeit-Sprachwechsel"],
        },
    },
    "cloud": {
        "id": "cloud",
        "icon": "☁️",
        "name": {"ru": "Cloud", "uk": "Cloud", "en": "Cloud", "de": "Cloud"},
        "tagline": {
            "ru": "Облачное хранение и совместная работа",
            "uk": "Хмарне зберігання та спільна робота",
            "en": "Cloud storage and collaboration",
            "de": "Cloud-Speicher und Zusammenarbeit",
        },
        "features": {
            "ru": ["Облачное хранение всех проектов", "Совместная работа с командой", "Автоматические резервные копии", "Синхронизация между устройствами", "Доступ к проектам из любой точки мира"],
            "uk": ["Хмарне зберігання всіх проєктів", "Спільна робота з командою", "Автоматичні резервні копії", "Синхронізація між пристроями", "Доступ до проєктів з будь-якої точки світу"],
            "en": ["Cloud storage for all projects", "Team collaboration", "Automatic backups", "Multi-device synchronization", "Access projects from anywhere"],
            "de": ["Cloud-Speicher für alle Projekte", "Teamzusammenarbeit", "Automatische Backups", "Geräteübergreifende Synchronisation", "Projekte von überall aufrufen"],
        },
    },
    "plugins": {
        "id": "plugins",
        "icon": "🛍️",
        "name": {"ru": "Plugin Store", "uk": "Plugin Store", "en": "Plugin Store", "de": "Plugin Store"},
        "tagline": {
            "ru": "Магазин расширений и плагинов",
            "uk": "Магазин розширень і плагінів",
            "en": "Extensions and plugin marketplace",
            "de": "Erweiterungs- und Plugin-Marktplatz",
        },
        "features": {
            "ru": ["Новые голосовые эффекты и обработчики", "Дополнительные TTS-движки", "Новые AI-модели для перевода", "Сторонние плагины от разработчиков", "Автоматическое обновление плагинов"],
            "uk": ["Нові голосові ефекти та обробники", "Додаткові TTS-рушії", "Нові AI-моделі для перекладу", "Сторонні плагіни від розробників", "Автоматичне оновлення плагінів"],
            "en": ["New voice effects and processors", "Additional TTS engines", "New AI translation models", "Third-party developer plugins", "Automatic plugin updates"],
            "de": ["Neue Stimmeffekte und -prozessoren", "Zusätzliche TTS-Engines", "Neue KI-Übersetzungsmodelle", "Plugins von Drittentwicklern", "Automatische Plugin-Updates"],
        },
    },
}


@app.route("/soon/<module_id>")
def coming_soon_page(module_id: str):
    mod = _SOON_MODULES.get(module_id)
    if mod is None:
        from flask import abort
        abort(404)
    return render_template("soon.html", mod=mod)


@app.route("/owner/download-center")
def download_center():
    return render_template("download_center.html")


@app.route("/dub")
def dub():
    return render_template("dub.html", languages=LANGUAGES, voices=VOICES)


@app.route("/projects")
def projects():
    return render_template("projects.html")


@app.route("/platform")
def platform_hub():
    return render_template("platform.html", languages=LANGUAGES, voices=VOICES)


@app.route("/platform/live")
def platform_live():
    return render_template("platform.html", languages=LANGUAGES, voices=VOICES)


@app.route("/platform/streaming")
def platform_streaming():
    return render_template("platform.html", languages=LANGUAGES, voices=VOICES)


@app.route("/platform/broadcast")
def platform_broadcast():
    return render_template("platform.html", languages=LANGUAGES, voices=VOICES)


@app.route("/platform/recording")
def platform_recording():
    return render_template("platform.html", languages=LANGUAGES, voices=VOICES)


@app.route("/platform/voice-training")
def platform_voice_training():
    return render_template("platform.html", languages=LANGUAGES, voices=VOICES)


@app.route("/platform/vocal-training")
def platform_vocal_training():
    return render_template("platform.html", languages=LANGUAGES, voices=VOICES)


@app.route("/dev/modules")
def dev_modules():
    return render_template("dev_modules.html")


@app.route("/dev/panel")
def dev_panel():
    return render_template("dev_panel.html")


@app.route("/dev/pipeline")
@app.route("/dev/pipeline/<task_id>")
def dev_pipeline(task_id: str | None = None):
    return render_template("dev_pipeline.html", task_id=task_id or "")


@app.route("/dev/architecture")
def dev_architecture():
    return render_template("dev_architecture.html")


@app.route("/monitoring")
def monitoring_page():
    """User-facing monitoring dashboard (TZ #8 §2, §15)."""
    return render_template("monitoring.html", developer=False)


@app.route("/dev/monitoring")
def dev_monitoring_page():
    """Developer monitoring dashboard with full system visibility (TZ #8 §14)."""
    return render_template("monitoring.html", developer=True)


@app.route("/plugins")
def plugins_page():
    """Plugin management page (TZ #9)."""
    return render_template("plugins.html")


@app.route("/dev/brain")
def dev_brain_page():
    """Project Brain viewer (TZ #10)."""
    return render_template("dev_brain.html")


@app.route("/cloud")
def cloud_page():
    return render_template("cloud.html")


@app.route("/dub-studio")
def dub_studio_page():
    return render_template("dub_studio.html")


# ─────────────────────────────────────────────
#  Мини-приложения (ТЗ #15)
#  Каждая функция — отдельная страница без боковой навигации
# ─────────────────────────────────────────────


@app.route("/mini/dub")
def mini_dub():
    return render_template(
        "dub.html",
        languages=LANGUAGES,
        voices=VOICES,
        _mini=True,
        back_url="/dub",
    )


@app.route("/mini/voice")
def mini_voice():
    return render_template(
        "voice.html",
        languages=LANGUAGES,
        voices=VOICES,
        _mini=True,
        back_url="/voice",
    )


@app.route("/mini/translate")
def mini_translate():
    return render_template(
        "translate.html",
        languages=LANGUAGES,
        _mini=True,
        back_url="/translate",
    )


@app.route("/mini/reader")
def mini_reader():
    return render_template("reader.html", _mini=True, back_url="/reader")


@app.route("/mini/studio")
def mini_studio():
    return render_template(
        "studio.html",
        languages=LANGUAGES,
        voices=VOICES,
        _mini=True,
        back_url="/studio",
    )


# ─────────────────────────────────────────────
#  Обработчики ошибок
# ─────────────────────────────────────────────


@app.errorhandler(404)
def error_404(e):
    return (
        render_template(
            "error.html",
            code=404,
            title="Страница не найдена",
            description="Возможно, страница была удалена или адрес указан неверно.",
        ),
        404,
    )


@app.errorhandler(500)
def error_500(e):
    return (
        render_template(
            "error.html",
            code=500,
            title="Внутренняя ошибка",
            description="Что-то пошло не так на стороне сервера. Попробуйте ещё раз или перезапустите приложение.",
        ),
        500,
    )


@app.errorhandler(413)
def error_413(e):
    return (
        render_template(
            "error.html",
            code=413,
            title="Файл слишком большой",
            description="Максимальный размер загружаемого файла — 2 ГБ. Попробуйте файл меньшего размера.",
        ),
        413,
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
