"""Audit translation pipeline — prints stage connectivity report."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    from engines.translation_manager import use_translation_manager
    from engines.mt.stable_translate import use_stable_mt
    from engines.translation import translate_text_traced
    from engines.translation_pipeline import UniversalTranslationPipeline
    from engines.translation_naturalizer import polish_lines
    from engines.translation_review import build_translation_review
    from engines.translation_trace import TranslationTraceLog
    from engines.translation_quality_log import SegmentTranslationAudit

    lines = [
        "TubeDub Translation Pipeline Audit",
        "=" * 40,
        "",
        "Data flow per segment:",
        "  Original (source_segments / whisper_text)",
        "    ↓",
        "  Whisper Result (STT output → segments)",
        "    ↓",
        "  Raw Translation (translate_text_traced → Translation Manager / Marian)",
        "    ↓",
        "  Alternative Translation (manager meta.alternative_translation)",
        "    ↓",
        "  Natural Translation (polish_lines → translation_naturalizer)",
        "    ↓",
        "  Quality Pass (run_quality_validation — always on)",
        "    ↓",
        "  Semantic Polish (apply_semantic_polish_lines)",
        "    ↓",
        "  Final Translation (audit.final_text)",
        "    ↓",
        "  Text sent to TTS (audit.tts_text / segments_data.text)",
        "",
        "Runtime mode:",
        f"  Translation Manager: {'ON' if use_translation_manager() else 'OFF'}",
        f"  Stable Marian only:  {'ON' if use_stable_mt() else 'OFF'}",
        "",
        "Modules:",
        f"  translate_text_traced:     {translate_text_traced.__module__}",
        f"  UniversalTranslationPipeline: connected",
        f"  polish_lines (Naturalizer): connected",
        f"  build_translation_review:   connected",
        f"  TranslationTraceLog:        {TranslationTraceLog.LOG_NAME}",
        f"  SegmentTranslationAudit:    alt_mt field={'alternative_translation' in SegmentTranslationAudit.__dataclass_fields__}",
        "",
        "Logs:",
        "  output/dev/translation_trace.log",
        "  output/dev/translation_quality.log",
        "  output/dev/translation_stage.log",
        "",
        "Dev mode (VM_DEV_MODE=1):",
        "  segments_preview shows all stages",
        "  translation_review panel with warnings",
        "",
    ]
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
