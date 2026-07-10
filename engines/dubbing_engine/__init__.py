"""
DubbingEngine — Unified 7-stage professional dubbing pipeline.

Replaces the patchwork of SSO + ADA + timing fixes with a single engine
that makes holistic decisions: context → adaptation → punctuation →
stress → voice quality gate → timing → validation.

Usage:
    from engines.dubbing_engine import DubbingEngine

    engine = DubbingEngine(lang="uk", app_dir=APP_DIR, task_id=task_id)
    results = engine.process_all(segments, timing_map, source_hints=source_hints)
    ready_segments = [r.output_text for r in results if r.passed_validation]
"""

from engines.dubbing_engine.engine import DubbingEngine
from engines.dubbing_engine.types import DubbingResult, DubbingSegment, StageLog

__all__ = ["DubbingEngine", "DubbingResult", "DubbingSegment", "StageLog"]
