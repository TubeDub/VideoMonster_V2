"""Golden Dataset scaffold — Dub Engine Stabilization TZ v2.0 P12/P13.

Deterministic fingerprints compared against goldens for regression.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from engines.audio_timing_optimizer import deterministic_fingerprint


DEFAULT_GOLDEN_ROOT = Path(__file__).resolve().parents[2] / "tests" / "golden" / "dub"


def golden_root() -> Path:
    return DEFAULT_GOLDEN_ROOT


def ensure_golden_layout(root: Path | None = None) -> Path:
    root = root or golden_root()
    for name in ("films", "interviews", "dialogs", "segments", "fingerprints"):
        (root / name).mkdir(parents=True, exist_ok=True)
    manifest = root / "manifest.json"
    if not manifest.is_file():
        manifest.write_text(
            json.dumps(
                {
                    "version": 1,
                    "targets": {
                        "films": 20,
                        "interviews": 100,
                        "dialogs": 1000,
                        "segments": 10000,
                    },
                    "entries": [],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    return root


def write_fingerprint_golden(
    name: str,
    segments: list[dict[str, Any]],
    *,
    settings: dict[str, Any] | None = None,
    root: Path | None = None,
) -> Path:
    root = ensure_golden_layout(root)
    fp = deterministic_fingerprint(segments, settings=settings)
    path = root / "fingerprints" / f"{name}.json"
    path.write_text(
        json.dumps(
            {"name": name, "fingerprint": fp, "settings": settings or {}, "segment_count": len(segments)},
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def assert_matches_golden(
    name: str,
    segments: list[dict[str, Any]],
    *,
    settings: dict[str, Any] | None = None,
    root: Path | None = None,
) -> str:
    root = ensure_golden_layout(root)
    path = root / "fingerprints" / f"{name}.json"
    current = deterministic_fingerprint(segments, settings=settings)
    if not path.is_file():
        write_fingerprint_golden(name, segments, settings=settings, root=root)
        return current
    expected = json.loads(path.read_text(encoding="utf-8")).get("fingerprint")
    if current != expected:
        raise AssertionError(
            f"golden regression: {name} fingerprint mismatch\n"
            f"expected={expected}\nactual={current}"
        )
    return current
