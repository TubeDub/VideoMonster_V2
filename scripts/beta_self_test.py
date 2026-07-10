"""Beta stabilization self-test — UI, API, pipeline, E2E."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import traceback
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

RESULTS: dict[str, bool | str] = {}


def _ok(name: str, detail: str = "") -> None:
    RESULTS[name] = True
    print(f"  OK  {name}" + (f" — {detail}" if detail else ""))


def _fail(name: str, exc: BaseException | str) -> None:
    RESULTS[name] = False
    msg = traceback.format_exc() if isinstance(exc, BaseException) else str(exc)
    print(f"  FAIL {name}\n{msg}")


def test_jinja_dub_page() -> None:
    from app import app

    client = app.test_client()
    r = client.get("/dub")
    if r.status_code != 200:
        raise RuntimeError(f"/dub returned {r.status_code}")
    html = r.get_data(as_text=True)

    m_voices = re.search(r"window\.VM_VOICES\s*=\s*(\{.*?\});", html, re.S)
    m_langs = re.search(r"window\.VM_LANGUAGES\s*=\s*(\{.*?\});", html, re.S)
    bootstrap = re.search(r'id="vm-bootstrap-data"[^>]*>(\{.*?\})</script>', html, re.S)

    voices = langs = None
    if bootstrap:
        data = json.loads(bootstrap.group(1))
        voices = data.get("voices")
        langs = data.get("languages")
    elif m_voices and m_langs:
        voices = json.loads(m_voices.group(1))
        langs = json.loads(m_langs.group(1))
    else:
        raise RuntimeError("VM_VOICES / VM_LANGUAGES not found in rendered HTML")

    if not isinstance(voices, dict) or not voices:
        raise RuntimeError("VM_VOICES empty or not dict")
    if not isinstance(langs, dict) or not langs:
        raise RuntimeError("VM_LANGUAGES empty or not dict")
    if "ru" not in voices:
        raise RuntimeError("VM_VOICES missing ru key")

    opts = re.findall(r'<select id="target-lang"[^>]*>.*?<option', html, re.S)
    if not opts:
        raise RuntimeError("target-lang select missing options")
    _ok("UI/Jinja dub.html", f"voices={len(voices)} langs={len(langs)}")


def test_render_routes() -> None:
    from app import app

    client = app.test_client()
    routes = [
        ("/", 200),
        ("/dub", 200),
        ("/settings", 200),
        ("/translate", 200),
        ("/voice", 200),
        ("/studio", 200),
    ]
    for path, code in routes:
        r = client.get(path)
        if r.status_code != code:
            raise RuntimeError(f"{path} -> {r.status_code}")
    _ok("Flask pages", f"{len(routes)} routes")


def test_apis() -> None:
    from app import app

    client = app.test_client()
    checks = [
        ("GET", "/api/system/check", None),
        ("GET", "/api/system/diagnostics", None),
        ("GET", "/api/languages", None),
        ("GET", "/api/voices?lang=ru", None),
        ("GET", "/api/license/status", None),
        ("GET", "/api/auto_dub/styles?target_lang=ru", None),
        ("GET", "/api/dub/check", None),
    ]
    for method, path, body in checks:
        if method == "GET":
            r = client.get(path)
        else:
            r = client.post(path, json=body or {})
        if r.status_code >= 500:
            raise RuntimeError(f"{path} -> {r.status_code}: {r.get_data(as_text=True)[:500]}")
        data = r.get_json(silent=True)
        if path == "/api/languages":
            if not (data or {}).get("languages"):
                raise RuntimeError("/api/languages empty")
        if path.startswith("/api/voices"):
            if not (data or {}).get("voices"):
                raise RuntimeError("/api/voices empty")
        if path.startswith("/api/auto_dub/styles"):
            if not (data or {}).get("styles"):
                raise RuntimeError("/api/auto_dub/styles empty")
    _ok("API", f"{len(checks)} endpoints")


def test_translation_pipeline() -> None:
    from engines.translation_pipeline import UniversalTranslationPipeline
    from engines.translation_quality import run_quality_validation

    pipe = UniversalTranslationPipeline(app_dir=APP_DIR, task_id="beta_self_test")
    segments = ["Hello world.", "John went home."]
    timing = [{"start": 0, "end": 2000}, {"start": 2000, "end": 4000}]
    result = pipe.translate_segments(segments, timing, "en", "ru")
    if len(result.segments) != 2:
        raise RuntimeError(f"expected 2 segments, got {len(result.segments)}")
    for i, seg in enumerate(result.segments):
        if not str(seg).strip():
            raise RuntimeError(f"empty translation at index {i}")
    for audit in result.audits:
        if not audit.whisper_text.strip():
            raise RuntimeError(f"audit {audit.index}: empty whisper")
        if not audit.naturalized_text.strip() and audit.whisper_text.strip():
            raise RuntimeError(f"audit {audit.index}: empty naturalized")
        if audit.quality_pass_before != audit.quality_pass_after:
            raise RuntimeError(f"audit {audit.index}: quality pass mutated text")
    texts, warnings = run_quality_validation(
        segments, result.segments, src_lang="en", tgt_lang="ru", raw_segments=[a.raw_translation for a in result.audits]
    )
    if texts != result.segments:
        raise RuntimeError("run_quality_validation changed texts")
    _ok("Translation pipeline", f"warnings={sum(len(w) for w in warnings)}")


def test_translation_review() -> None:
    from engines.translation_quality_log import synthesize_audits_from_segments
    from engines.translation_review import build_translation_review

    src = ["Hello world"]
    tr = ["Привет мир"]
    audits = synthesize_audits_from_segments(src, tr, "en", "ru", engine="test")
    info = {
        "source_segments": src,
        "translation_audits": [a.__dict__ for a in audits],
        "source_lang": "en",
        "target_lang": "ru",
    }
    review = build_translation_review(info)
    row = review["segments"][0]
    for field in ("original", "raw_translation", "naturalized_text", "final_text"):
        if not str(row.get(field) or "").strip():
            raise RuntimeError(f"review missing {field}")
    _ok("Translation review")


def test_regression_scripts() -> None:
    scripts = [
        "test_translation_quality_guards.py",
        "test_translation_pipeline.py",
        "test_naturalizer_unit.py",
        "test_semantic_adaptation.py",
        "test_translation_cache_audits.py",
        "test_translation_router.py",
    ]
    for name in scripts:
        p = subprocess.run(
            [sys.executable, str(APP_DIR / "scripts" / name)],
            capture_output=True,
            text=True,
            cwd=str(APP_DIR),
        )
        if p.returncode != 0:
            raise RuntimeError(f"{name} failed:\n{p.stdout}\n{p.stderr}")
    _ok("Regression scripts", str(len(scripts)))


def test_e2e() -> None:
    p = subprocess.run(
        [sys.executable, str(APP_DIR / "scripts" / "e2e_test.py")],
        capture_output=True,
        text=True,
        cwd=str(APP_DIR),
        timeout=300,
    )
    if p.returncode != 0:
        raise RuntimeError(f"e2e_test failed:\n{p.stdout[-2000:]}\n{p.stderr[-1000:]}")
    if "DONE:" not in p.stdout:
        raise RuntimeError("e2e_test did not complete dub")
    _ok("E2E", "MP4 created")


def main() -> int:
    print("=== Beta Self-Test ===\n")
    steps = [
        ("UI/Jinja", test_jinja_dub_page),
        ("Flask", test_render_routes),
        ("API", test_apis),
        ("Translation", test_translation_pipeline),
        ("Review", test_translation_review),
        ("Regression", test_regression_scripts),
        ("E2E", test_e2e),
    ]
    for name, fn in steps:
        print(f"[{name}]")
        try:
            fn()
        except Exception as e:
            _fail(name, e)

    print("\n=== Summary ===")
    all_ok = True
    for k, v in RESULTS.items():
        status = "OK" if v is True else "FAIL"
        if v is not True:
            all_ok = False
        print(f"  {k}: {status}")

    if all_ok:
        print("\nREADY FOR CLOSED BETA")
        return 0
    print("\nNOT READY — see failures above")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
