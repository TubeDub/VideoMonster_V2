"""Technical Debt Monitor — complexity, duplication, TODOs (TZ #10 §8)."""

from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_TODO_RE = re.compile(r"\b(TODO|FIXME|HACK|XXX|TEMP)\b", re.IGNORECASE)
_LARGE_FILE_LINES = 500
_LARGE_FUNC_LINES = 80
_COMPLEXITY_THRESHOLD = 15


@dataclass
class DebtItem:
    category: str
    severity: str  # low | medium | high
    file: str
    detail: str
    line: int = 0
    priority: int = 50

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "severity": self.severity,
            "file": self.file,
            "detail": self.detail,
            "line": self.line,
            "priority": self.priority,
        }


class TechnicalDebtMonitor:
    """Scan codebase for technical debt indicators (§8)."""

    def __init__(self, app_dir: str | Path | None = None) -> None:
        self.app_dir = Path(app_dir) if app_dir else Path.cwd()

    def scan(self, *, include_engines: bool = False) -> list[DebtItem]:
        items: list[DebtItem] = []
        scan_dirs = ["core", "api", "sdk"]
        if include_engines:
            scan_dirs.append("engines")

        for scan_dir in scan_dirs:
            base = self.app_dir / scan_dir
            if not base.is_dir():
                continue
            for path in base.rglob("*.py"):
                if "__pycache__" in str(path):
                    continue
                rel = str(path.relative_to(self.app_dir)).replace("\\", "/")
                items.extend(self._scan_file(rel, path))
        return sorted(items, key=lambda x: x.priority, reverse=True)

    def _scan_file(self, rel: str, path: Path) -> list[DebtItem]:
        items: list[DebtItem] = []
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()
        except Exception:
            return items

        if len(lines) > _LARGE_FILE_LINES:
            items.append(DebtItem(
                "large_file", "medium", rel,
                f"File has {len(lines)} lines (threshold {_LARGE_FILE_LINES})",
                priority=60,
            ))

        for i, line in enumerate(lines, 1):
            if _TODO_RE.search(line):
                items.append(DebtItem(
                    "todo", "low", rel, line.strip()[:120], line=i, priority=40,
                ))

        try:
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    end = getattr(node, "end_lineno", node.lineno + 1) or node.lineno + 1
                    flen = end - node.lineno
                    if flen > _LARGE_FUNC_LINES:
                        items.append(DebtItem(
                            "complex_function", "medium", rel,
                            f"Function '{node.name}' is {flen} lines",
                            line=node.lineno, priority=55,
                        ))
                    complexity = self._cyclomatic_complexity(node)
                    if complexity > _COMPLEXITY_THRESHOLD:
                        items.append(DebtItem(
                            "high_complexity", "high", rel,
                            f"Function '{node.name}' complexity={complexity}",
                            line=node.lineno, priority=70,
                        ))
        except SyntaxError:
            items.append(DebtItem(
                "syntax_error", "high", rel, "Cannot parse file", priority=90,
            ))

        return items

    @staticmethod
    def _cyclomatic_complexity(node: ast.AST) -> int:
        complexity = 1
        for child in ast.walk(node):
            if isinstance(child, (ast.If, ast.While, ast.For, ast.ExceptHandler,
                                  ast.With, ast.Assert)):
                complexity += 1
            elif isinstance(child, ast.BoolOp):
                complexity += len(child.values) - 1
        return complexity

    def detect_duplicates(self, *, min_lines: int = 8) -> list[DebtItem]:
        """Find duplicate code blocks across core/ files."""
        blocks: dict[str, list[str]] = {}
        core = self.app_dir / "core"
        if not core.is_dir():
            return []
        for path in core.rglob("*.py"):
            rel = str(path.relative_to(self.app_dir)).replace("\\", "/")
            try:
                lines = [
                    ln.strip() for ln in path.read_text(encoding="utf-8", errors="replace").splitlines()
                    if ln.strip() and not ln.strip().startswith("#")
                ]
            except Exception:
                continue
            for i in range(len(lines) - min_lines):
                block = "\n".join(lines[i:i + min_lines])
                if len(block) < 40:
                    continue
                blocks.setdefault(block, []).append(f"{rel}:{i+1}")

        items: list[DebtItem] = []
        for block, locations in blocks.items():
            if len(locations) > 1:
                items.append(DebtItem(
                    "duplication", "medium", locations[0].split(":")[0],
                    f"Duplicate block in {len(locations)} places: {', '.join(locations[:3])}",
                    priority=65,
                ))
        return items[:30]

    def summary(self) -> dict[str, Any]:
        items = self.scan()
        dupes = self.detect_duplicates()
        all_items = items + dupes
        by_cat: dict[str, int] = {}
        for item in all_items:
            by_cat[item.category] = by_cat.get(item.category, 0) + 1
        return {
            "total": len(all_items),
            "by_category": by_cat,
            "high_priority": [i.to_dict() for i in all_items if i.priority >= 70][:20],
            "items": [i.to_dict() for i in all_items[:50]],
        }


_monitor: TechnicalDebtMonitor | None = None


def get_technical_debt_monitor(app_dir: str | Path | None = None) -> TechnicalDebtMonitor:
    global _monitor
    if _monitor is None:
        _monitor = TechnicalDebtMonitor(app_dir=app_dir)
    return _monitor
