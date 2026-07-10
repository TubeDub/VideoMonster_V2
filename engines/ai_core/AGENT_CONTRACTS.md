# TubeDub AI Core — Agent Contracts (v4.3)

**AI Core 4.3** — Architecture Simplification + Streaming Pipeline + Global UX Standard.

- Each agent: **one responsibility**, writes only its contract fields.
- **Peer Validation**: next agent validates upstream input contract only.
- **Streaming Pipeline**: conveyor mode with immutable snapshots and segment isolation.
- **No duplicate checks** inside agents — orchestrator runs peer gate before each step.
- Diagnostics: `architecture_validation.json` (incl. `ux_validation`), `peer_validation_log.json`, `streaming_pipeline_report.json`.
- **Global UX**: Back/Cancel in wizard; `POST /api/auto_dub/cancel/{task_id}`, `POST /api/auto_dub/restart/{task_id}` with checkpoint resume.

## Shared segment shape

| Field | Written by | Read by |
|-------|------------|---------|
| `index` | STT / pipeline | all |
| `text` | STT (source) | director, translation, semantic |
| `creative_brief` | **Director** | translation, semantic, timing, grammar, quality, voice_preparation (READ ONLY) |
| `translated_text` | Translation | semantic, timing |
| `semantic_text` | Semantic | timing, grammar |
| `timing_text` | Timing | grammar, quality |
| `grammar_text` | Grammar | quality, voice_preparation |
| `quality_*` | Quality | voice_preparation (read only) |
| `voice_prep_*` | voice_preparation | voice (read only) |
| `text_for_tts` / `plain_text` | pipeline after agents | voice_preparation, voice |

## Agent chain (`AICoreOrchestrator.AGENT_CHAIN`)

| Agent | Timeout | Allowed writes | Notes |
|-------|---------|----------------|-------|
| **planner** | 30s | manifest only (no segments) | READ ONLY pre-flight |
| **stt** | 600s | hook only | Does not mutate manifest |
| **director** | 60s | `creative_brief` | Decision coordinator, READ ONLY text/timing |
| **translation** | 120s | `translated_text` | Raw MT; reads `literal_phrasing_importance`, `formality` |
| **semantic** | 90s | `semantic_text` | Reads `adaptation_priority`, `deep_semantic_adaptation_needed`, `emotion` |
| **timing** | 90s | `timing_text` | Reads `allowed_compression`, `allowed_expansion`, duration fields |
| **grammar** | 60s | `grammar_text` | Reads `speech_style`, `naturalness_priority`, `formality` |
| **quality** | 120s | `quality_*` flags | READ ONLY audit; reads priority thresholds from brief |
| **voice_preparation** | 30s | `emotion_tags_present`, `voice_prep_*` | Reads `emotion`, `speaking_speed`, `emotional_intensity` |
| **voice** | 300s | TTS artifacts via hook | Wraps TTS loop |
| **voice_quality** | 30s | `voice_quality_*`, `studio_ready` | Stub QA |
| **mix** | 180s | `mix_output`, `mix_ok` | Wraps studio mix |

Pipeline order: **Planner → STT → Director → Translation → Semantic → Timing → Grammar → Quality → Reviewer → Voice Prep → Voice → Voice Verification → Mix**

## Streaming Pipeline (AI Core 4.2)

**Mode, not a new agent:** `pipeline_mode: "streaming"` in project state.

Conveyor: translation → semantic → timing → grammar run in parallel on different segments.
Each handoff uses immutable `SegmentSnapshot`. Live peer validation per segment; one bad segment never stops the belt.

Diagnostics: `streaming_pipeline_report.json` (utilization, wait times, peer returns, segment traces).

Enable in AutoDub via `state.pipeline_mode = "streaming"` (default for orchestrator text path).

Implementation: `engines/ai_core/streaming_pipeline/`

## Peer Validation (Contract Validation Pipeline)

Each downstream agent validates **only its input contract** before work begins.
The next agent in the chain is the peer validator for the previous agent.

| Downstream agent | Validates upstream | Input contract |
|------------------|-------------------|----------------|
| **semantic** | translation | `translated_text` present, target language |
| **timing** | semantic | `semantic_text` present |
| **grammar** | timing | `timing_text` present; basic semantic meaning |
| **quality** | grammar | `grammar_text` present |
| **reviewer** | quality | `grammar_text` present |
| **voice_preparation** | reviewer | TTS-ready text exists |
| **voice** | voice_preparation | same (via hook) |
| **mix** | voice_verification | voice stage complete |

On contract failure: segment is **returned to upstream agent** (max 3 returns per segment).
Diagnostics: `output/diagnostics/{task_id}/peer_validation_log.json`

Implementation: `engines/ai_core/peer_validation.py`, `peer_validation_loop.py`
Wired in `AICoreOrchestrator.run_pipeline()` before each text/voice agent.

Single Responsibility: agents do not fix upstream errors except via explicit peer return routing.
Post-hoc `segment_text_polish` removed — duplicate of semantic + grammar responsibilities.

## Director Agent (`Director/v1`)

- **Module:** `engines/ai_core/director_agent/`
- **Strategy:** rule analyzer first; optional structured LLM JSON via `llm_gateway` (not chat)
- **Fallback:** full rule-based brief + defaults; record `"LLM skipped"`
- **Report:** `output/manifests/{project_uuid}/director_report.json`
- **API:** `GET /api/director/<task_id>`

## LLM usage

All LLM calls **must** go through `engines.ai_core.llm_gateway`:

- `can_call_llm(task_id, segment_idx) -> (bool, reason)` — never raises
- On `False`: use rule-based fallback, record `"LLM skipped"` in OpenDDF
- Reasons: `no_endpoint`, `llm_circuit_open`, `segment_time_budget`, `segment_breaker_open`, `budget_exhausted`

## OpenDDF fields (per agent step)

Recorded via `engines.open_ddf.record_agent`:

- `execution_time_ms`
- `retry_count`
- `fallback_reason`
- `llm_calls`
- `input_metrics` / `output_metrics`

## Pipeline stop policy

| Condition | Action |
|-----------|--------|
| Missing video file | **STOP** (critical) |
| No audio track (when required) | **STOP** (critical) |
| Output dir unavailable | **STOP** (critical) |
| Agent timeout / LLM skip / agent error | **fallback + continue** (debug always continues) |
| AdaptGate LLM skip with usable text | **continue** (rule fallback) |

## Reports

- OpenDDF: `output/ddf_{task_id}.json`
- AI Core report: `output/ai_core_report_{task_id}.json`
- Director report: `output/manifests/{project_uuid}/director_report.json`
- API: `GET /api/ai_core/report/<task_id>`, `GET /api/director/<task_id>`
