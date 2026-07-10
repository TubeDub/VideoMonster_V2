"""AI Module installer — fully automated, no user-facing technical terms."""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

from engines.ai_manager.config import (
    DEFAULT_MODEL,
    INSTALLER_MIN_BYTES,
    INSTALLER_URL,
    append_log,
    set_progress,
)

logger = logging.getLogger("tubedub.ai_manager.installer")

_OLLAMA_PATHS = (
    Path(os.getenv("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
    Path(os.getenv("ProgramFiles", "C:\\Program Files")) / "Ollama" / "ollama.exe",
)

# GUI / tray / onboarding-launcher executables that must NEVER be visible to the
# end user. The headless HTTP server ("ollama.exe serve") is the only thing we
# keep running, so the bare "ollama.exe" image name is deliberately NOT listed
# here — only the desktop/tray launcher variants are force-closed.
_GUI_PROCESS_NAMES = (
    "ollama app.exe",
    "ollama-app.exe",
    "OllamaApp.exe",
)
_AUTOSTART_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_AUTOSTART_VALUE_NAMES = ("Ollama", "OllamaApp", "Ollama App")

_LOCAL_HOST = "127.0.0.1:11434"


def _hidden_flags() -> int:
    """creationflags that fully detach a child and never flash a console."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
    flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return flags


def _hidden_startupinfo():
    """STARTUPINFO with SW_HIDE so no window is ever shown (Windows only)."""
    if os.name != "nt":
        return None
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
        return si
    except Exception:
        return None


def _headless_env() -> dict:
    """Environment that keeps the backend server-only and quiet."""
    env = os.environ.copy()
    env.setdefault("OLLAMA_HOST", _LOCAL_HOST)
    # Suppress the new desktop-app onboarding / update nags where supported.
    env.setdefault("OLLAMA_NOPRUNE", "1")
    return env


def find_ollama_binary() -> Path | None:
    for p in _OLLAMA_PATHS:
        if p.is_file():
            return p
    found = shutil.which("ollama")
    return Path(found) if found else None


def suppress_backend_gui(app_dir: Path | None = None) -> None:
    """Force-close any AI backend GUI/tray/launcher window and kill its autostart.

    TZ: the user must only ever see TubeDub — never Ollama's «Launch» window with
    Claude Code / Codex / Hermes / etc. This is safe to call repeatedly and is a
    no-op on non-Windows hosts. The headless ``ollama serve`` process is left
    untouched (matched by name «ollama.exe», not the GUI «ollama app.exe»).
    """
    if os.name != "nt":
        return
    si = _hidden_startupinfo()
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    for name in _GUI_PROCESS_NAMES:
        try:
            subprocess.run(
                ["taskkill", "/IM", name, "/F", "/T"],
                capture_output=True,
                timeout=20,
                startupinfo=si,
                creationflags=flags,
            )
        except Exception:
            pass
    # Remove "run desktop app at login" so the GUI never returns after reboot.
    for value in _AUTOSTART_VALUE_NAMES:
        try:
            subprocess.run(
                ["reg", "delete", f"HKCU\\{_AUTOSTART_RUN_KEY}", "/v", value, "/f"],
                capture_output=True,
                timeout=20,
                startupinfo=si,
                creationflags=flags,
            )
        except Exception:
            pass
    if app_dir is not None:
        try:
            append_log(app_dir, "Backend GUI/launcher suppressed (headless mode)")
        except Exception:
            pass


def _ollama_models_dir() -> Path:
    return Path.home() / ".ollama"


def _work_dir(app_dir: Path) -> Path:
    """Installer scratch dir — kept OUTSIDE the project ``data/`` tree so the
    multi-GB download is never copied, audited, or committed with the app."""
    return Path(tempfile.gettempdir()) / "tubedub_ai_setup"


def _download_installer(dest: Path, app_dir: Path, *, max_retries: int = 3) -> Path:
    """Download backend installer with size validation and retry."""
    for attempt in range(1, max_retries + 1):
        set_progress(app_dir, "download", 5 + attempt * 5, "Загрузка AI-модуля…")
        append_log(app_dir, f"Download attempt {attempt}/{max_retries}")
        try:
            if dest.is_file():
                dest.unlink()
            req = urllib.request.Request(
                INSTALLER_URL,
                headers={"User-Agent": "TubeDub-AIManager/1.0"},
            )
            with urllib.request.urlopen(req, timeout=600) as resp, dest.open("wb") as out:
                shutil.copyfileobj(resp, out, length=1024 * 256)
            size = dest.stat().st_size
            if size < INSTALLER_MIN_BYTES:
                append_log(app_dir, f"Download too small ({size} bytes), retry", level="warn")
                dest.unlink(missing_ok=True)
                continue
            raw = dest.read_bytes()
            if raw[:2] != b"MZ":
                append_log(app_dir, "Download corrupt (invalid file), retry", level="warn")
                dest.unlink(missing_ok=True)
                continue
            sha = hashlib.sha256(raw).hexdigest()
            append_log(app_dir, f"Download OK ({size} bytes, sha256={sha[:16]}…)")
            return dest
        except Exception as exc:
            append_log(app_dir, f"Download failed: {exc}", level="error")
            dest.unlink(missing_ok=True)
            time.sleep(2)
    raise RuntimeError("Не удалось загрузить AI-модуль. Проверьте подключение к интернету.")


def _run_installer(installer: Path, app_dir: Path) -> None:
    set_progress(app_dir, "install", 35, "Установка AI-модуля…")
    append_log(app_dir, "Running installer (silent)")
    # Silent per-user install. The installer may still auto-launch the desktop
    # GUI at the end — we suppress that immediately afterwards.
    proc = subprocess.run(
        [str(installer), "/S"],
        capture_output=True,
        text=True,
        timeout=900,
        env=_headless_env(),
        startupinfo=_hidden_startupinfo(),
        creationflags=_hidden_flags(),
    )
    if proc.returncode not in (0, None) and not find_ollama_binary():
        append_log(app_dir, f"Installer exit {proc.returncode}", level="warn")
    # Wait for the CLI binary to appear, suppressing the GUI launcher meanwhile.
    for _ in range(60):
        suppress_backend_gui(app_dir)
        if find_ollama_binary():
            append_log(app_dir, "AI backend installed")
            suppress_backend_gui(app_dir)
            return
        time.sleep(2)
    if not find_ollama_binary():
        raise RuntimeError("Установка не завершилась. Попробуйте переустановить AI-модуль из настроек.")


def _start_headless_server(app_dir: Path) -> None:
    """Start the backend HTTP server only — no GUI, no console, fully detached."""
    ollama = find_ollama_binary()
    if not ollama:
        return
    try:
        subprocess.Popen(
            [str(ollama), "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            env=_headless_env(),
            startupinfo=_hidden_startupinfo(),
            creationflags=_hidden_flags(),
        )
        append_log(app_dir, "Headless AI server started")
    except Exception as exc:
        append_log(app_dir, f"Headless server start failed: {exc}", level="warn")


def _ensure_ollama_running(app_dir: Path) -> None:
    set_progress(app_dir, "start", 45, "Запуск AI-модуля…")
    if find_ollama_binary() is None:
        raise RuntimeError("AI-модуль не найден после установки")
    # Make sure no GUI/launcher window lingers from the installer.
    suppress_backend_gui(app_dir)
    for _ in range(30):
        try:
            from engines.llm_adaptation_mode import discover_local_llm

            if discover_local_llm(force=True):
                suppress_backend_gui(app_dir)
                return
        except Exception:
            pass
        # Start our own headless server (never the desktop app) and keep the
        # GUI suppressed in case the backend tried to spawn it.
        _start_headless_server(app_dir)
        suppress_backend_gui(app_dir)
        time.sleep(2)
    raise RuntimeError("AI-модуль не отвечает. Перезапустите TubeDub и попробуйте снова.")


def _pull_model(model: str, app_dir: Path) -> None:
    set_progress(app_dir, "model", 55, "Загрузка языковой модели…")
    ollama = find_ollama_binary()
    if not ollama:
        raise RuntimeError("AI-модуль недоступен")
    append_log(app_dir, f"Pulling model {model}")
    proc = subprocess.Popen(
        [str(ollama), "pull", model],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=_headless_env(),
        startupinfo=_hidden_startupinfo(),
        creationflags=_hidden_flags(),
    )
    lines = 0
    for line in proc.stdout or []:
        lines += 1
        if lines % 5 == 0:
            set_progress(app_dir, "model", min(90, 55 + lines // 10), "Загрузка языковой модели…")
    proc.wait(timeout=3600)
    if proc.returncode != 0:
        raise RuntimeError("Не удалось загрузить языковую модель")
    append_log(app_dir, "Model ready")


def verify_ai_module(app_dir: Path, model: str) -> dict[str, Any]:
    """Health check — real chat roundtrip.

    The FIRST request to a freshly installed model loads several GB into RAM and
    on a CPU-only machine can take 30–90s. So verification uses a patient
    timeout and warms the model up before the (fast) health question, and it
    never counts against the dub-run LLM budget.
    """
    set_progress(app_dir, "verify", 92, "Проверка AI-модуля…")
    try:
        import engines.llm_adaptation_mode as lam

        lam._discovery_cache["ts"] = 0.0
        lam._discovery_cache["result"] = None
    except Exception:
        pass
    from engines.translation_adapt import llm_rephrase_available, _llm_chat

    if not llm_rephrase_available():
        raise RuntimeError("AI-модуль не обнаружен")

    verify_timeout = _verify_timeout()
    out = None
    # Up to 2 patient attempts: the first may spend most of its time loading the
    # model weights into memory; the second is warm and quick.
    for attempt in range(2):
        out = _llm_chat(
            "Ответь одним словом: да.",
            max_tokens=8,
            temperature=0,
            timeout=verify_timeout,
            count_budget=False,
        )
        if out:
            break
        append_log(app_dir, f"Verification attempt {attempt + 1} slow/empty, retrying", level="warn")
    if not out:
        raise RuntimeError("AI-модуль не отвечает")
    append_log(app_dir, "Verification OK")
    return {"ok": True, "sample": out[:40]}


def _verify_timeout() -> float:
    try:
        v = float(os.getenv("VM_LLM_VERIFY_TIMEOUT", "") or "")
        return v if v > 0 else 180.0
    except (TypeError, ValueError):
        return 180.0


def warmup_ai_for_dub(app_dir: Path | None = None) -> bool:
    """Warm up local LLM before dub so the first segment does not cold-start fail."""
    try:
        from engines.translation_adapt import llm_rephrase_available, _llm_chat

        if not llm_rephrase_available():
            return False
        verify_timeout = _verify_timeout()
        out = _llm_chat(
            "Ответь одним словом: да.",
            max_tokens=8,
            temperature=0,
            timeout=verify_timeout,
            count_budget=False,
        )
        if app_dir is not None and out:
            append_log(app_dir, "AI warmup OK")
        return bool(out)
    except Exception as exc:
        logger.debug("warmup_ai_for_dub: %s", exc)
        return False


def install_ai_module(app_dir: Path, *, model: str = DEFAULT_MODEL) -> None:
    """Full install pipeline when no compatible local AI exists."""
    work = _work_dir(app_dir)
    work.mkdir(parents=True, exist_ok=True)
    installer = work / "tubedub_ai_backend_setup.exe"
    _download_installer(installer, app_dir)
    _run_installer(installer, app_dir)
    _ensure_ollama_running(app_dir)
    _pull_model(model, app_dir)
    verify_ai_module(app_dir, model)
    # Final safety net: ensure the GUI launcher is gone after everything.
    suppress_backend_gui(app_dir)


def ensure_backend_headless(app_dir: Path) -> bool:
    """Keep the AI backend running server-only on app startup.

    Called at TubeDub launch. If the AI module is installed it (a) closes any
    GUI/launcher window the OS may have auto-started, and (b) makes sure the
    headless HTTP server is up — all silently, with no user-visible windows.
    Returns True if a backend binary is present.
    """
    if find_ollama_binary() is None:
        return False
    try:
        suppress_backend_gui(app_dir)
        from engines.llm_adaptation_mode import discover_local_llm

        if not discover_local_llm(force=True):
            _start_headless_server(app_dir)
            suppress_backend_gui(app_dir)
    except Exception as exc:
        logger.debug("ensure_backend_headless: %s", exc)
    # Repair a stuck «installing» status left by an interrupted install.
    try:
        from engines.ai_manager.manager import reconcile_install_state

        reconcile_install_state(app_dir)
    except Exception as exc:
        logger.debug("reconcile on startup: %s", exc)
    return True


def use_existing_backend(app_dir: Path, *, model: str = DEFAULT_MODEL) -> None:
    """Compatible local AI already running — verify and optionally pull model."""
    set_progress(app_dir, "detect", 20, "Обнаружен совместимый AI-модуль…")
    try:
        import engines.llm_adaptation_mode as lam

        lam._discovery_cache["ts"] = 0.0
        lam._discovery_cache["result"] = None
    except Exception:
        pass
    _ensure_ollama_running(app_dir)
    suppress_backend_gui(app_dir)
    ollama = find_ollama_binary()
    if ollama:
        try:
            subprocess.run(
                [str(ollama), "pull", model],
                capture_output=True,
                timeout=3600,
                env=_headless_env(),
                startupinfo=_hidden_startupinfo(),
                creationflags=_hidden_flags(),
            )
        except Exception:
            pass
    verify_ai_module(app_dir, model)


def uninstall_ai_module(app_dir: Path, *, installed_by_tubedub: bool) -> int:
    """Remove AI module data installed/managed by TubeDub."""
    freed = 0
    work = _work_dir(app_dir)
    if work.is_dir():
        freed += _dir_size(work)
        shutil.rmtree(work, ignore_errors=True)
    models = _ollama_models_dir()
    if installed_by_tubedub and models.is_dir():
        freed += _dir_size(models)
        shutil.rmtree(models, ignore_errors=True)
    # LLM rewrite cache
    cache = Path(app_dir) / "data" / "cache" / "llm_rewrite_cache.json"
    if cache.is_file():
        try:
            freed += cache.stat().st_size
            cache.unlink()
        except OSError:
            pass
    tmp_installer = Path(tempfile.gettempdir()) / "OllamaSetup.exe"
    if tmp_installer.is_file() and installed_by_tubedub:
        try:
            freed += tmp_installer.stat().st_size
            tmp_installer.unlink()
        except OSError:
            pass
    return freed


def ai_module_disk_bytes(app_dir: Path | None = None) -> int:
    """Total bytes used by AI module (backend + models + install work dir)."""
    total = 0
    for p in _OLLAMA_PATHS:
        if p.is_file():
            total += p.stat().st_size
            break
    md = _ollama_models_dir()
    if md.is_dir():
        total += _dir_size(md)
    total += _dir_size(Path(os.getenv("LOCALAPPDATA", "")) / "Programs" / "Ollama")
    if app_dir:
        total += _dir_size(_work_dir(app_dir))
    return total


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    try:
        for p in path.rglob("*"):
            if p.is_file():
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total
