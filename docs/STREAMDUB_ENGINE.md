# StreamDub Engine V1

Independent fast-dubbing conductor — **does not modify TubeDub `auto_dub_api`**.

## Architecture

```
Video → Whisper → Smart Segmenter → Fast MT → Quality Analyzer
      → LLM Refiner (selective) → TTS → Video Merge
```

Cinema mode adds Voice Clone + Lip Sync (stubs in V1).

## Modes

| Mode | Path | Goal |
|------|------|------|
| **fast** | Whisper → Marian → TTS | Max speed |
| **smart** | + Quality + selective LLM | 90–95% fast MT, 5–10% LLM |
| **cinema** | LLM-first + voice clone + lip sync | Max quality |

## Module interface

Every stage implements:

- `initialize()` / `process()` / `shutdown()` / `health_check()` / `capabilities()`

## API

```
GET  /api/streamdub/health
GET  /api/streamdub/modes
POST /api/streamdub/run
```

Example:

```json
POST /api/streamdub/run
{
  "video_path": "uploads/test.mp4",
  "source_lang": "en",
  "target_lang": "uk",
  "mode": "smart",
  "mt_backend": "marian"
}
```

## Artifacts (per project)

```
output/streamdub/<project_id>/diagnostics/
  performance_report.json
  timeline.json
  quality_report.json
```

## Memory

```
data/streamdub/projects/<project_id>/
  translation_memory.json
  entities.json
  project_memory.json
```

## Package layout

```
engines/streamdub/
  engine.py              — entry point
  pipeline/orchestrator.py — async conductor
  modules/               — stage implementations
  memory/                — TM, entities, project context
  artifacts/benchmark.py — reports
```

## Reused (read-only adapters)

- `engines/stt_engine.py` — Whisper
- `engines/mt/stable_translate.py` — Marian
- `engines/mt/nllb_engine.py`, `argos_engine.py` — backends
- `engines/translation_quality_score.py` — quality scoring
- `engines/translation_adapt.py` — LLM refiner
- `engines/tts.py` — TTS

## V2 planned

- Voice Clone backend
- Lip Sync backend
- Streaming segment queues (worker pool per stage)
- UI integration in Dub wizard (mode selector)
