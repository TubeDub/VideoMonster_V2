"""AI Code Reviewer — static analysis after changes (TZ #10 §4).

Read-only: finds issues and reports them. Never modifies code (§14).
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.architecture_engine import PROTECTED_MODULES, get_architecture_engine
from core.technical_debt import get_technical_debt_monitor


@dataclass
class ReviewFinding:
    category: str
    severity: str
    file: str
    message: str
    line: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "severity": self.severity,
            "file": self.file,
            "message": self.message,
            "line": self.line,
        }


@dataclass
class ReviewReport:
    findings: list[ReviewFinding] = field(default_factory=list)
    files_reviewed: int = 0
    passed: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "files_reviewed": self.files_reviewed,
            "finding_count": len(self.findings),
            "findings": [f.to_dict() for f in self.findings],
        }


class CodeReviewer:
    """Post-change static code review (§4)."""

    def review_files(
        self,
        files: list[str],
        *,
        app_dir: str | Path | None = None,
    ) -> ReviewReport:
        base = Path(app_dir) if app_dir else Path.cwd()
        report = ReviewReport()

        for f in files:
            norm = f.replace("\\", "/")
            report.files_reviewed += 1

            # Architecture rules.
            if norm in PROTECTED_MODULES:
                report.findings.append(ReviewFinding(
                    "architecture", "critical", norm,
                    "Modifying protected core module",
                ))

            full = base / norm
            if not full.is_file():
                continue

            try:
                text = full.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(text)
            except SyntaxError as exc:
                report.findings.append(ReviewFinding(
                    "syntax", "critical", norm, str(exc), line=exc.lineno or 0,
                ))
                continue

            lines = text.splitlines()
            for i, line in enumerate(lines, 1):
                # Thread safety hints.
                if re.search(r"\bglobal\s+_", line):
                    report.findings.append(ReviewFinding(
                        "threading", "warning", norm,
                        "Mutable global state — verify thread safety", line=i,
                    ))
                if "except:" in line or "except Exception:" in line and "pass" in lines[i:i+2]:
                    report.findings.append(ReviewFinding(
                        "error_handling", "info", norm,
                        "Broad except — ensure errors are logged", line=i,
                    ))

            # API compatibility — public functions should have docstrings in core/.
            if norm.startswith("core/"):
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if not node.name.startswith("_") and not ast.get_docstring(node):
                            if node.lineno and node.lineno < 50:  # skip trivial
                                report.findings.append(ReviewFinding(
                                    "api_docs", "info", norm,
                                    f"Public function '{node.name}' lacks docstring",
                                    line=node.lineno,
                                ))

        report.passed = not any(
            f.severity in ("critical", "high") for f in report.findings
        )
        return report

    def review_directory(
        self,
        subdir: str = "core",
        *,
        app_dir: str | Path | None = None,
    ) -> ReviewReport:
        base = Path(app_dir) if app_dir else Path.cwd()
        target = base / subdir
        files = [
            str(p.relative_to(base)).replace("\\", "/")
            for p in target.rglob("*.py")
            if "__pycache__" not in str(p)
        ] if target.is_dir() else []
        return self.review_files(files, app_dir=base)


_reviewer: CodeReviewer | None = None


def get_code_reviewer() -> CodeReviewer:
    global _reviewer
    if _reviewer is None:
        _reviewer = CodeReviewer()
    return _reviewer
