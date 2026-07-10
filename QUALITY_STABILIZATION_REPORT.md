# AI Dub Quality Stabilization v1.0 — Implementation Report

## Scope

P0 quality stabilization for TubeDub without rewriting AI Core orchestrator architecture.
Pipeline remains end-to-end; changes target translation completeness, sentence integrity,
semantic adaptation fallbacks, music preservation in final mux, reviewer gate, and OpenDDF metrics.

## User Problems Addressed

| # | Problem | Fix |
|---|---------|-----|
| 1 | Sentences truncated/cut off | `dub_quality_stabilization.is_sentence_complete`, `is_truncated_sentence`, `sentence_integrity.enforce_pre_tts_integrity`; Timing/Grammar/Quality agents unchanged but Reviewer re-audits before Voice |
| 2 | Segments not translated | `guarantee_translation_completeness` (Translation Agent pass 6); `retry_segment_translation` chain; fallback reason in OpenDDF |
| 3 | Machine-like literal MT | Semantic Agent rule fallback (`generate_rule_candidates` variant C) when MT empty; existing rule_engine calque fixes retained |
| 4 | Background music disappears | `auto_dub_api` passes `background_audio_path` + attenuation to `DubEngine`; stem mix via `get_background_mix_params`; `final_mix` diagnostics persisted |
| 5 | Voice quality regression | Voice prep uses `resolve_post_quality_text` only (post Quality/Reviewer); no rollback of prior voice fixes |

## Key Files

- `engines/dub_quality_stabilization.py` — validators, completeness guarantee, `dub_quality_report.json` builder
- `engines/ai_core/reviewer_agent/agent.py` — pre-voice gate with inline repair + agent routing
- `engines/ai_core/orchestrator.py` — Reviewer inserted after Quality, before Voice Preparation
- `engines/translation_validation.py` — `ensure_segment_translation` / `ensure_all_segments_translated`
- `engines/translation_adapt.py` — `reset_circuit_for_phase` (deduplicated)
- `engines/ai_core/llm_gateway.py` — phase circuit reset wrapper
- `api/auto_dub_api.py` — POST_TTS_QA circuit reset, background stem mux, quality report write
- `engines/ai_core/mix_agent/agent.py` — `music_preserved` / `mix_quality` OpenDDF metrics
- `engines/ai_core/semantic_agent/agent.py` — rule semantic fallback on empty MT
- `data/templates/dub_quality_report.json` — output template
- `tests/test_dub_quality_stabilization.py` — unit tests (mocked, no ffmpeg E2E)

## Music Preservation (Mode B)

1. Source separation runs during dub prep → `task.info.source_separation` with `accompaniment_path`
2. Final mux: `get_background_mix_params()` → `DubEngine(background_audio_path=..., background_attenuation_db=...)`
3. `DubEngine._cmd_stem_mix` overlays dubbed speech on attenuated music/SFX stem (default Mode B / `full_dub` + stem)
4. `build_final_mix_diagnostics` → `info.final_mix` with `used_stem_mix`, `music_detected_in_final`
5. Mode A (full speech replacement without stem) only when separation failed or user chose `original_only`

## OpenDDF / dub_quality_report.json Fields

```json
{
  "summary": {
    "empty_segments": 0,
    "truncated_sentences": 0,
    "semantic_compression": 0,
    "music_preserved": true,
    "mix_quality": 0.95,
    "reviewer_failures": 0
  },
  "per_segment": [{
    "retry_count": 0,
    "final_voice_duration_ms": 3200,
    "translation_status": "ok",
    "fallback_reason": ""
  }]
}
```

Agents: `Reviewer/v1`, `DubQualityReport/v1`, `TranslationCompleteness`, `Mix/v1` (with `music_preserved`).

## Golden Video Verification

Path: `uploads/video_e00b875b63.mp4` (aliases in `GOLDEN_VIDEO_PATH`).

Manual check:
1. Run EN→UK dub with default style (modern / full_dub)
2. After completion inspect `output/diagnostics/<task_id>/dub_quality_report.json`
3. Confirm `music_preserved: true` and `truncated_sentences: 0`
4. Listen: background score audible under new speech

Unit tests: `python -m pytest tests/test_dub_quality_stabilization.py -q`

## LLM / Ollama Dependencies

Still required for best results:
- Semantic Agent LLM rewrite (`llm_rewrite`)
- Timing Agent LLM compression (when rule-based insufficient)
- POST_TTS_QA timing adaptation (after circuit reset at phase boundary)

Rule-based fallbacks operate when LLM unavailable (`llm_circuit_open`, `no_endpoint`, segment budget).

## Test Results

Run:
```
python -m pytest tests/test_dub_quality_stabilization.py tests/test_translation_validation.py tests/test_studio_mix_idempotent.py -q
python -c "import app"
```
