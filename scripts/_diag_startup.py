"""One-shot startup diagnostic — writes output/startup_diag.txt"""
from __future__ import annotations

import socket
import sys
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output" / "startup_diag.txt"
sys.path.insert(0, str(ROOT))

lines: list[str] = []


def log(msg: str) -> None:
    lines.append(msg)
    print(msg)


def main() -> int:
    log(f"ROOT={ROOT}")
    log(f"python={sys.executable}")
    log(f"version={sys.version}")

    # Import app
    try:
        from app import app  # noqa: F401

        log("import app: OK")
    except Exception:
        log("import app: FAIL")
        log(traceback.format_exc())
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text("\n".join(lines), encoding="utf-8")
        return 1

    err_box: list[str] = []

    def run_flask() -> None:
        try:
            app.run(host="127.0.0.1", port=5199, debug=False, use_reloader=False)
        except Exception:
            err_box.append(traceback.format_exc())

    t = threading.Thread(target=run_flask, daemon=True)
    t.start()

    deadline = time.time() + 8
    ready = False
    while time.time() < deadline:
        if err_box:
            break
        try:
            with socket.create_connection(("127.0.0.1", 5199), timeout=0.4):
                ready = True
                break
        except OSError:
            time.sleep(0.2)

    if err_box:
        log("flask thread: FAIL")
        log(err_box[0])
    elif ready:
        log("flask server: OK on 5199")
    else:
        log("flask server: TIMEOUT (port not accepting)")

    # Check deps
    for mod in ("flask", "webview", "edge_tts", "faster_whisper", "langdetect"):
        try:
            __import__(mod)
            log(f"module {mod}: OK")
        except ImportError as e:
            log(f"module {mod}: MISSING ({e})")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    log(f"written {OUT}")
    return 0 if ready and not err_box else 2


if __name__ == "__main__":
    raise SystemExit(main())
