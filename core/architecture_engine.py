"""Architecture Knowledge Engine — project structure & rules (TZ #10 §2)."""

from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Protected core modules — must not be modified by plugins/extensions (§15).
PROTECTED_MODULES = frozenset({
    "core/event_bus.py",
    "core/orchestrator.py",
    "core/llm_dispatcher.py",
    "core/pipeline_engine.py",
    "core/performance_optimizer.py",
    "core/monitoring_center.py",
})

STAGE_MODULES = {
    1: ["core/event_bus.py", "core/event_types.py", "core/event_pipeline.py"],
    2: ["core/orchestrator.py", "core/resource_monitor.py"],
    3: ["core/llm_dispatcher.py", "core/model_registry.py", "llm_adapters/"],
    4: ["core/chunk_manager.py", "core/pipeline_engine.py"],
    5: ["core/recovery_manager.py", "core/micro_validator.py"],
    6: ["core/ai_memory.py", "core/semantic_cache.py"],
    7: ["core/hardware_profiler.py", "core/benchmark.py",
        "core/performance_optimizer.py", "core/performance_monitor.py"],
    8: ["core/monitoring_center.py", "core/diagnostics.py",
        "core/bottleneck_analyzer.py", "core/analytics_db.py"],
    9: ["core/plugin_manager.py", "core/plugin_api.py", "sdk/"],
    10: ["core/architecture_engine.py", "core/dev_assistant.py",
         "core/technical_debt.py", "core/task_planner.py"],
}


@dataclass
class ModuleInfo:
    path: str
    lines: int
    imports: list[str] = field(default_factory=list)
    stage: int = 0


class ArchitectureEngine:
    """Knows project structure, dependencies, and architecture rules (§2)."""

    BRAIN_FILES = (
        "PROJECT.md", "ARCHITECTURE.md", "ROADMAP.md", "CODING_RULES.md",
        "UX_RULES.md", "PERFORMANCE.md", "CHANGELOG.md", "DECISIONS.md",
        "MEMORY.md", "KNOWN_ISSUES.md", "API_REFERENCE.md", "PLUGIN_REFERENCE.md",
    )

    def __init__(self, app_dir: str | Path | None = None) -> None:
        self.app_dir = Path(app_dir) if app_dir else Path.cwd()
        self.brain_dir = self.app_dir / ".ai"
        self._module_cache: dict[str, ModuleInfo] = {}

    def brain_path(self, name: str) -> Path:
        return self.brain_dir / name

    def read_brain(self, name: str) -> str:
        path = self.brain_path(name)
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")
        return ""

    def write_brain(self, name: str, content: str) -> None:
        self.brain_dir.mkdir(parents=True, exist_ok=True)
        self.brain_path(name).write_text(content, encoding="utf-8")

    def scan_structure(self) -> dict[str, Any]:
        """Map project directories and core modules."""
        dirs: dict[str, int] = {}
        py_files: list[str] = []
        for root, _, files in os.walk(self.app_dir):
            rel_root = Path(root).relative_to(self.app_dir)
            if any(p.startswith(".") or p in ("__pycache__", "node_modules", ".git")
                   for p in rel_root.parts):
                continue
            for f in files:
                if f.endswith(".py"):
                    rel = str(rel_root / f).replace("\\", "/")
                    if rel.startswith("./"):
                        rel = rel[2:]
                    py_files.append(rel)
            if str(rel_root) != ".":
                dirs[str(rel_root).replace("\\", "/")] = len(files)

        core_modules = sorted(p for p in py_files if p.startswith("core/"))
        return {
            "app_dir": str(self.app_dir),
            "brain_dir": str(self.brain_dir),
            "brain_files": [f for f in self.BRAIN_FILES if self.brain_path(f).is_file()],
            "core_modules": core_modules,
            "total_py_files": len(py_files),
            "directories": dirs,
            "stages": {str(k): v for k, v in STAGE_MODULES.items()},
        }

    def analyze_module(self, rel_path: str) -> ModuleInfo:
        if rel_path in self._module_cache:
            return self._module_cache[rel_path]
        full = self.app_dir / rel_path
        info = ModuleInfo(path=rel_path, lines=0)
        if full.is_file():
            try:
                text = full.read_text(encoding="utf-8", errors="replace")
                info.lines = text.count("\n") + 1
                tree = ast.parse(text)
                info.imports = self._extract_imports(tree)
            except Exception:
                pass
        for stage, modules in STAGE_MODULES.items():
            for m in modules:
                if rel_path == m or rel_path.startswith(m.rstrip("/")):
                    info.stage = stage
        self._module_cache[rel_path] = info
        return info

    @staticmethod
    def _extract_imports(tree: ast.AST) -> list[str]:
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
        return imports

    def dependency_graph(self) -> dict[str, list[str]]:
        """Build import dependency graph for core/ modules."""
        graph: dict[str, list[str]] = {}
        structure = self.scan_structure()
        for mod in structure.get("core_modules", []):
            info = self.analyze_module(mod)
            deps = [i for i in info.imports if i.startswith("core.")]
            graph[mod] = deps
        return graph

    def check_architecture_rules(self, files: list[str]) -> list[dict[str, str]]:
        """Validate proposed changes against architecture rules."""
        violations: list[dict[str, str]] = []
        for f in files:
            norm = f.replace("\\", "/")
            if norm in PROTECTED_MODULES:
                violations.append({
                    "file": norm,
                    "rule": "protected_core",
                    "message": "Protected core module — extend via plugins/wrappers only",
                })
            if norm.startswith("engines/translation") or "translation_adapt" in norm:
                violations.append({
                    "file": norm,
                    "rule": "translation_algorithm",
                    "message": "Translation algorithms are protected — do not modify",
                })
        return violations

    def assess_change_impact(self, files: list[str]) -> dict[str, Any]:
        """High-level impact before any change (§2, §3 precursor)."""
        graph = self.dependency_graph()
        affected_tests: list[str] = []
        dependents: list[str] = []

        for f in files:
            norm = f.replace("\\", "/")
            stem = Path(norm).stem
            for test_dir in ("tests",):
                test_path = self.app_dir / test_dir
                if test_path.is_dir():
                    for tp in test_path.glob(f"test_*{stem}*.py"):
                        affected_tests.append(str(tp.relative_to(self.app_dir)).replace("\\", "/"))
                    for tp in test_path.glob("*.py"):
                        try:
                            if stem in tp.read_text(encoding="utf-8", errors="replace"):
                                rel = str(tp.relative_to(self.app_dir)).replace("\\", "/")
                                if rel not in affected_tests:
                                    affected_tests.append(rel)
                        except Exception:
                            pass
            for mod, deps in graph.items():
                mod_stem = Path(mod).stem
                if any(d.endswith(stem) or stem in d for d in deps):
                    dependents.append(mod)

        rules = self.check_architecture_rules(files)
        risks = []
        if rules:
            risks.append(f"{len(rules)} architecture rule violation(s)")
        if any(f.replace("\\", "/").startswith("core/") for f in files):
            risks.append("Core module change — run full test suite")

        return {
            "files": files,
            "affected_tests": affected_tests[:20],
            "dependents": list(set(dependents))[:20],
            "rule_violations": rules,
            "risks": risks,
            "requires_full_suite": any(f.replace("\\", "/").startswith("core/") for f in files),
        }

    def get_context_for_ai(self) -> dict[str, Any]:
        """Bundle Project Brain + structure for AI tools."""
        return {
            "structure": self.scan_structure(),
            "dependencies": self.dependency_graph(),
            "brain": {f: self.read_brain(f)[:4000] for f in self.BRAIN_FILES
                      if self.brain_path(f).is_file()},
            "protected_modules": sorted(PROTECTED_MODULES),
        }


_engine: ArchitectureEngine | None = None


def get_architecture_engine(app_dir: str | Path | None = None) -> ArchitectureEngine:
    global _engine
    if _engine is None:
        _engine = ArchitectureEngine(app_dir=app_dir)
    return _engine
