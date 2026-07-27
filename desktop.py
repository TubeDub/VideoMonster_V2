"""
TubeDub — точка входа для Windows-десктопного приложения.
Запускает Flask в фоновом потоке, затем открывает окно pywebview.
"""
import sys
import os
import threading
import time
import socket
import traceback
from pathlib import Path

APP_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(APP_DIR))
os.chdir(APP_DIR)

try:
    from engines.model_manager import configure as configure_model_manager
    from engines.model_manager.runtime import set_downloads_permitted

    configure_model_manager(APP_DIR)
    set_downloads_permitted(False)
except Exception:
    pass

try:
    from engines.ffmpeg_paths import ensure_ffmpeg_path
    from engines.app_logging import setup_app_logging

    ensure_ffmpeg_path()
    setup_app_logging(APP_DIR)
except Exception:
    pass

PORT = 5199
STARTUP_TIMEOUT = 45.0
ERROR_LOG = APP_DIR / "output" / "desktop_error.log"

_flask_error: str | None = None
_flask_ready = threading.Event()


def find_free_port(start: int = PORT) -> int:
    for p in range(start, start + 20):
        try:
            with socket.socket() as s:
                s.bind(("127.0.0.1", p))
                return p
        except OSError:
            continue
    return start


def _write_error(msg: str) -> None:
    global _flask_error
    _flask_error = msg
    ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
    try:
        ERROR_LOG.write_text(msg, encoding="utf-8")
    except OSError:
        pass


def run_flask(port: int) -> None:
    try:
        os.environ["PORT"] = str(port)
        try:
            from engines.owner_first_run import run_if_needed

            run_if_needed()
        except Exception as owner_err:
            import logging

            logging.getLogger(__name__).warning(
                "Owner first-run in desktop thread skipped: %s", owner_err
            )
        from app import app

        _flask_ready.set()
        app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False, threaded=True)
    except Exception:
        _write_error(traceback.format_exc())


def wait_for_server(port: int, timeout: float = STARTUP_TIMEOUT) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _flask_error:
            return False
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except (ConnectionRefusedError, OSError, TimeoutError):
            time.sleep(0.25)
    return False


def _show_error(title: str, message: str) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(title, message[:2000])
    except Exception:
        print(title + "\n" + message, file=sys.stderr)


def _open_in_browser(url: str) -> None:
    import webbrowser

    webbrowser.open(url)
    print(f"\nTubeDub: {url}")
    print("Приложение работает в браузере. Закройте это окно (Ctrl+C) для остановки сервера.")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


def main() -> None:
    port = find_free_port()
    print(f"TubeDub: запуск сервера на порту {port}...")

    # Non-daemon: closing the WebView must NOT kill Flask mid-dub
    # (otherwise UI shows «Нет связи с сервером»).
    flask_thread = threading.Thread(target=run_flask, args=(port,), daemon=False)
    flask_thread.start()

    ready = wait_for_server(port)
    if not ready:
        err = (_flask_error or "").strip()
        if not err and ERROR_LOG.is_file():
            try:
                err = ERROR_LOG.read_text(encoding="utf-8").strip()
            except OSError:
                pass
        if not err:
            err = (
                f"Сервер не ответил за {int(STARTUP_TIMEOUT)} сек (порт {port}).\n\n"
                "Возможные причины:\n"
                "• уже запущен другой TubeDub — закройте python.exe в диспетчере задач;\n"
                "• не установлены пакеты: pip install -r requirements_desktop.txt\n\n"
                f"Подробности: {ERROR_LOG}"
            )
        _write_error(err)
        _show_error("TubeDub", "Не удалось запустить приложение.\n\n" + err[:1800])
        return

    url = os.environ.get("VM_START_URL") or "/dub"
    if not url.startswith("http"):
        url = f"http://127.0.0.1:{port}{url}"

    print(f"TubeDub: сервер готов -> {url}")

    if os.environ.get("VM_BROWSER_ONLY", "").strip() in ("1", "true", "yes"):
        _open_in_browser(url)
        return

    # Low free RAM → skip WebView2 (often dies with OutOfMemoryException).
    try:
        import ctypes

        class _MEM(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        m = _MEM()
        m.dwLength = ctypes.sizeof(_MEM)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
            free_gb = m.ullAvailPhys / (1024**3)
            if free_gb < 2.5:
                print(
                    f"TubeDub: мало свободной RAM ({free_gb:.1f} GB) — "
                    "открываю браузер вместо WebView."
                )
                _open_in_browser(url)
                return
    except Exception:
        pass

    try:
        import webview

        webview.create_window(
            "TubeDub",
            url,
            width=1200,
            height=800,
            min_size=(800, 600),
            resizable=True,
        )
        webview.start()
    except ImportError:
        _open_in_browser(url)
        return
    except Exception as e:
        _write_error(traceback.format_exc())
        print(f"Окно WebView недоступно ({e}), открываю браузер...")
        _open_in_browser(url)
        return

    # WebView closed — keep API alive so remux/diagnostics still work.
    print(
        f"TubeDub: окно закрыто, сервер продолжает работу -> "
        f"http://127.0.0.1:{port}/dub\n"
        "Остановите сервер: Ctrl+C в этом окне терминала."
    )
    try:
        while flask_thread.is_alive():
            flask_thread.join(timeout=1.0)
    except KeyboardInterrupt:
        print("TubeDub: остановка...")



if __name__ == "__main__":
    main()
