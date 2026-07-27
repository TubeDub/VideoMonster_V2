"""Production path-safety / zip-slip guards."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from engines.path_safety import (
    clamp_write_path,
    is_under_root,
    resolve_under_roots,
    safe_extractall,
    safe_filename,
)


def test_safe_filename_strips_traversal():
    assert safe_filename("../../evil") == "evil"
    assert ".." not in safe_filename("a/../../x")
    assert "/" not in safe_filename("foo/bar")


def test_resolve_under_roots_rejects_absolute_escape(tmp_path):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    victim = tmp_path / "secret.txt"
    victim.write_text("x", encoding="utf-8")
    assert resolve_under_roots(str(victim), [uploads]) is None


def test_resolve_under_roots_allows_uploads(tmp_path):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    f = uploads / "clip.mp4"
    f.write_bytes(b"1")
    assert resolve_under_roots("clip.mp4", [uploads]) == f.resolve()
    assert resolve_under_roots(str(f), [uploads]) == f.resolve()


def test_clamp_write_path_stays_in_output(tmp_path):
    out = tmp_path / "output"
    out.mkdir()
    p = clamp_write_path("../../evil.zip", out, default_name="proj.vmproj.zip")
    assert is_under_root(p, out)
    assert p.name.endswith(".zip")


def test_safe_extractall_rejects_zip_slip(tmp_path):
    dest = tmp_path / "project"
    dest.mkdir()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../escape.txt", "pwned")
    buf.seek(0)
    with zipfile.ZipFile(buf, "r") as zf:
        with pytest.raises(ValueError, match="zip_slip"):
            safe_extractall(zf, dest)
    assert not (tmp_path / "escape.txt").exists()


def test_safe_extractall_ok(tmp_path):
    dest = tmp_path / "project"
    dest.mkdir()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("project.json", '{"ok": true}')
    buf.seek(0)
    with zipfile.ZipFile(buf, "r") as zf:
        safe_extractall(zf, dest)
    assert (dest / "project.json").is_file()


def test_resolve_under_roots_rejects_parent_escape(tmp_path):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"x")
    # Relative traversal must not escape via basename-only fallback either.
    assert resolve_under_roots("../outside.mp4", [uploads]) is None
    assert resolve_under_roots(str(outside), [uploads]) is None
