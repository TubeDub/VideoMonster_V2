"""Path allowlists and zip-slip protection for user-supplied paths."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._\-]+")


def is_under_root(path: Path, root: Path) -> bool:
    """True if *path* resolves inside *root* (no symlink escape)."""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def safe_filename(name: str, *, default: str = "document", max_len: int = 80) -> str:
    """Strip path components and sanitize to a single basename stem."""
    raw = Path(str(name or "")).name.strip()
    stem = Path(raw).stem if raw else default
    cleaned = _SAFE_NAME_RE.sub("_", stem).strip("._-") or default
    return cleaned[:max_len]


def resolve_under_roots(
    raw: str | Path,
    roots: list[Path] | tuple[Path, ...],
    *,
    basename_fallback: bool = True,
) -> Path | None:
    """Resolve a client path only if it lands under one of *roots*.

    Absolute paths outside roots are rejected. Relative paths are tried
    against each root; optionally the basename alone is tried under each root.
    """
    text = str(raw or "").strip()
    if not text:
        return None

    candidate = Path(text)
    resolved_roots = [r.resolve() for r in roots if r is not None]

    def _accept(p: Path) -> Path | None:
        try:
            resolved = p.resolve()
        except OSError:
            return None
        if not resolved.is_file() and not resolved.is_dir():
            # Allow non-existent only when caller will create? Prefer existing.
            if not resolved.exists():
                return None
        for root in resolved_roots:
            if is_under_root(resolved, root):
                return resolved
        return None

    # Absolute or drive-relative: must already be under a root.
    if candidate.is_absolute():
        return _accept(candidate)

    for root in resolved_roots:
        hit = _accept(root / candidate)
        if hit is not None:
            return hit

    if basename_fallback:
        base = Path(text).name
        if base and base != text:
            for root in resolved_roots:
                hit = _accept(root / base)
                if hit is not None:
                    return hit

    return None


def clamp_write_path(
    dest: str | Path,
    root: Path,
    *,
    default_name: str,
) -> Path:
    """Force a write destination under *root*; reject escapes."""
    root_r = root.resolve()
    root_r.mkdir(parents=True, exist_ok=True)
    raw_name = Path(str(dest or "").strip() or default_name).name
    default_name_only = Path(default_name).name
    compound = (".vmproj.zip", ".tar.gz", ".tar.bz2")

    def _split_compound(name: str) -> tuple[str, str]:
        lower = name.lower()
        for ext in compound:
            if lower.endswith(ext):
                return name[: -len(ext)], ext
        p = Path(name)
        return p.stem, p.suffix

    stem, suffix = _split_compound(raw_name)
    if not suffix:
        _, suffix = _split_compound(default_name_only)
    name = safe_filename(stem, default=_split_compound(default_name_only)[0] or "file")
    target = (root_r / f"{name}{suffix}").resolve()
    if not is_under_root(target, root_r):
        raise ValueError("path_escape")
    return target


def safe_extractall(archive: zipfile.ZipFile, dest_dir: Path) -> None:
    """Extract zip members only when each resolved path stays under *dest_dir*."""
    dest = dest_dir.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    for info in archive.infolist():
        name = info.filename or ""
        # Reject absolute / drive-letter members before join.
        if Path(name).is_absolute() or name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", name):
            raise ValueError(f"zip_slip:{name}")
        member = (dest / name).resolve()
        if not is_under_root(member, dest):
            raise ValueError(f"zip_slip:{name}")
        archive.extract(info, dest)
