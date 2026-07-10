"""Automatic Documentation Sync — keep Project Brain current (TZ #10 §6)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from core.architecture_engine import STAGE_MODULES, get_architecture_engine


class DocumentationSync:
    """Update .ai/ documentation from live project state (§6).

    Updates documentation files only — never modifies code (§14).
    """

    def sync_all(self, *, app_dir: str | Path | None = None) -> dict[str, Any]:
        engine = get_architecture_engine(app_dir)
        updated: list[str] = []

        arch = self._generate_architecture(engine)
        engine.write_brain("ARCHITECTURE.md", arch)
        updated.append("ARCHITECTURE.md")

        changelog = self._append_changelog(engine, "Documentation sync")
        engine.write_brain("CHANGELOG.md", changelog)
        updated.append("CHANGELOG.md")

        perf = self._generate_performance(app_dir)
        engine.write_brain("PERFORMANCE.md", perf)
        updated.append("PERFORMANCE.md")

        issues = self._generate_known_issues(app_dir)
        engine.write_brain("KNOWN_ISSUES.md", issues)
        updated.append("KNOWN_ISSUES.md")

        return {"updated": updated, "brain_dir": str(engine.brain_dir)}

    def _generate_architecture(self, engine) -> str:
        structure = engine.scan_structure()
        deps = engine.dependency_graph()
        lines = [
            "# VideoMonster V2 Architecture",
            "",
            f"*Auto-generated: {time.strftime('%Y-%m-%d %H:%M')}*",
            "",
            "## Platform Layers (Stages 1–10)",
            "",
        ]
        for stage, modules in sorted(STAGE_MODULES.items()):
            lines.append(f"### Stage {stage}")
            for m in modules:
                lines.append(f"- `{m}`")
            lines.append("")

        lines += [
            "## Core Modules",
            "",
        ]
        for mod in structure.get("core_modules", []):
            info = engine.analyze_module(mod)
            lines.append(f"- `{mod}` ({info.lines} lines, stage {info.stage})")

        lines += ["", "## Dependency Graph (core/)", "", "```"]
        for mod, imp in sorted(deps.items()):
            if imp:
                lines.append(f"{mod} → {', '.join(imp)}")
        lines += ["```", ""]
        return "\n".join(lines)

    def _append_changelog(self, engine, entry: str) -> str:
        existing = engine.read_brain("CHANGELOG.md")
        stamp = time.strftime("%Y-%m-%d")
        new_entry = f"\n## [{stamp}] {entry}\n- Documentation auto-sync\n"
        if not existing:
            return f"# Changelog\n{new_entry}"
        return existing + new_entry

    def _generate_performance(self, app_dir) -> str:
        lines = [
            "# Performance Guidelines",
            "",
            f"*Auto-generated: {time.strftime('%Y-%m-%d %H:%M')}*",
            "",
            "## Rules",
            "- No fixed worker/thread counts — use Performance Optimizer",
            "- Hardware Profiler runs on first launch",
            "- Semantic Cache before every LLM call",
            "- Memory pressure → reduce chunks, never crash",
            "",
        ]
        try:
            from core.performance_optimizer import get_performance_optimizer, optimizer_enabled
            if optimizer_enabled():
                status = get_performance_optimizer(app_dir=app_dir).get_status()
                plan = status.get("plan") or {}
                lines += [
                    "## Current Plan",
                    f"- Tier: {plan.get('tier', '?')}",
                    f"- Mode: {plan.get('mode', '?')}",
                    f"- Max concurrent: {plan.get('max_concurrent_tasks', '?')}",
                    f"- Chunk size: {plan.get('default_chunk_size', '?')}",
                    "",
                ]
        except Exception:
            pass
        return "\n".join(lines)

    def _generate_known_issues(self, app_dir) -> str:
        lines = [
            "# Known Issues",
            "",
            f"*Auto-generated: {time.strftime('%Y-%m-%d %H:%M')}*",
            "",
        ]
        try:
            from core.technical_debt import get_technical_debt_monitor
            summary = get_technical_debt_monitor(app_dir).summary()
            for item in summary.get("high_priority", [])[:15]:
                lines.append(f"- **[{item.get('severity')}]** `{item.get('file')}`: {item.get('detail')}")
        except Exception:
            lines.append("- No issues scanned")
        lines.append("")
        return "\n".join(lines)

    def sync_api_reference(self, *, app_dir: str | Path | None = None) -> str:
        """Sync SDK API reference into .ai/ and sdk/docs/."""
        base = Path(app_dir) if app_dir else Path.cwd()
        sdk_ref = base / "sdk" / "docs" / "API_REFERENCE.md"
        if sdk_ref.is_file():
            content = sdk_ref.read_text(encoding="utf-8")
            engine = get_architecture_engine(app_dir)
            engine.write_brain("API_REFERENCE.md", content)
            return str(engine.brain_path("API_REFERENCE.md"))
        return ""

    def sync_plugin_reference(self, *, app_dir: str | Path | None = None) -> str:
        """Sync plugin SDK guide into .ai/PLUGIN_REFERENCE.md (§6)."""
        base = Path(app_dir) if app_dir else Path.cwd()
        for candidate in (
            base / "sdk" / "docs" / "PLUGIN_GUIDE.md",
            base / "sdk" / "docs" / "PLUGIN_REFERENCE.md",
        ):
            if candidate.is_file():
                content = candidate.read_text(encoding="utf-8")
                engine = get_architecture_engine(app_dir)
                engine.write_brain("PLUGIN_REFERENCE.md", content)
                return str(engine.brain_path("PLUGIN_REFERENCE.md"))
        return ""


_sync: DocumentationSync | None = None


def get_documentation_sync() -> DocumentationSync:
    global _sync
    if _sync is None:
        _sync = DocumentationSync()
    return _sync
