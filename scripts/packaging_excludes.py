"""Shared exclude rules for ZIP / release packaging."""

from __future__ import annotations

EXCLUDE_DIRS = frozenset(
    {
        "__pycache__",
        ".git",
        ".github",
        ".venv",
        "venv",
        "node_modules",
        "dist",
        "build",
        "cache",
        "models",
        "output",
        "uploads",
        "projects",
        ".pytest_cache",
        ".ruff_cache",
        ".cursor",
        ".idea",
        ".vscode",
    }
)

EXCLUDE_FILE_SUFFIXES = frozenset({".pyc", ".pyo", ".log", ".zip"})

EXCLUDE_FILE_NAMES = frozenset(
    {
        "license.json",
        "license_secret.txt",
        ".env",
        ".env.local",
    }
)


def should_exclude_path(rel_parts: tuple[str, ...], *, suffix: str = "", name: str = "") -> bool:
    if any(p in EXCLUDE_DIRS for p in rel_parts):
        return True
    if suffix.lower() in EXCLUDE_FILE_SUFFIXES:
        return True
    if name in EXCLUDE_FILE_NAMES:
        return True
    return False
