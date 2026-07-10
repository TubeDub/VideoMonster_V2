"""Run MASTER TZ checks: import, e2e, ZIP. Writes output/master_check_results.txt."""
from __future__ import annotations

import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))
OUT_DIR = APP_DIR / "output"
LOG_FILE = OUT_DIR / "master_check_results.txt"
ZIP_DEST = OUT_DIR / "VideoMonster_V2_ready.zip"

from scripts.packaging_excludes import should_exclude_path


def log(msg: str) -> None:
    print(msg)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(msg + "\n")


def test_import() -> tuple[bool, str]:
    sys.path.insert(0, str(APP_DIR))
    try:
        import app  # noqa: F401

        return True, "import app: OK"
    except Exception as e:
        return False, f"import app FAILED: {e}"


def run_e2e() -> tuple[int, str]:
    script = APP_DIR / "scripts" / "e2e_test.py"
    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(APP_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    summary = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, summary.strip() or "(no output)"


def create_zip() -> tuple[bool, str, int]:
    if ZIP_DEST.exists():
        ZIP_DEST.unlink()

    count = 0
    with zipfile.ZipFile(ZIP_DEST, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in APP_DIR.rglob("*"):
            rel = path.relative_to(APP_DIR)
            if should_exclude_path(rel.parts, suffix=path.suffix.lower(), name=path.name):
                continue
            if path.is_file():
                zf.write(path, rel.as_posix())
                count += 1

    size = ZIP_DEST.stat().st_size if ZIP_DEST.exists() else 0
    return ZIP_DEST.exists() and size > 0, str(ZIP_DEST), size


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_FILE.write_text(
        f"=== VideoMonster V2 Master Checks {datetime.now(timezone.utc).isoformat()} ===\n",
        encoding="utf-8",
    )

    ok_import, import_msg = test_import()
    log(f"IMPORT: {import_msg}")

    log("\n--- E2E ---")
    e2e_code, e2e_out = run_e2e()
    log(e2e_out)
    log(f"E2E_EXIT_CODE: {e2e_code}")

    log("\n--- ZIP ---")
    zip_ok, zip_path, zip_size = create_zip()
    log(f"ZIP_PATH: {zip_path}")
    log(f"ZIP_EXISTS: {zip_ok}")
    log(f"ZIP_SIZE_BYTES: {zip_size}")
    log(f"ZIP_SIZE_MB: {round(zip_size / 1024 / 1024, 2)}")

    log("\n=== SUMMARY ===")
    log(f"IMPORT_OK: {ok_import}")
    log(f"E2E_OK: {e2e_code == 0}")
    log(f"ZIP_OK: {zip_ok}")

    if not ok_import:
        return 1
    return 0 if e2e_code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
