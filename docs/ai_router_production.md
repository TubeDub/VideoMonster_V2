# Universal AI Architecture — Production Ready

## Principle

**Local dubbing is always free.** Cloud / user API are optional. No paywall dialogs. No automatic model downloads. Installer size is not bloated with AI weights.

## AI Router

```
TubeDub → AI Router
            ├─ Local AI (Free) — Ollama / LM Studio / vLLM
            ├─ My API — OpenAI / Anthropic / OpenRouter / GitHub Models
            ├─ TubeDub Cloud — optional
            └─ Future AI — extension point
```

| Module | Role |
|--------|------|
| `core/ai_sources.py` | Persist Local / My API / Cloud config (`data/ai_sources.json`) |
| `core/ai_router.py` | Intelligent failover across sources |
| `api/ai_sources_api.py` | HTTP API |
| `/ai/sources` | AI Sources page |
| `/ai/settings` | AI Settings page |

## Quality modes

- **Fast** / **Balanced** / **Maximum Quality**
- Priority chain (when available): GPT-5.5 → Claude Sonnet → GPT-4.1 → Qwen 14B → …

## Hardware → model (recommendation only)

| VRAM | Suggested |
|------|-----------|
| &lt;4 GB / CPU | Qwen 3B |
| 6–8 GB | Qwen 7B |
| 12+ GB | Qwen 14B |
| 24+ GB | Qwen 32B |

Never auto-downloads.

## First-run dialog

If no local models:

1. Continue without local models (Marian MT)
2. Use my API
3. Download local model (explicit confirm)
4. Point to existing folder (`D:\AI Models`, …) — **outside** TubeDub dir

## Pipeline helpers

- `engines/smart_segmentation.py` — no mid-sentence / name / unit / quote cuts
- `core/semantic_retry.py` — normal → strict → alt model → manual review
- `engines/quality_score_v2.py` — multi-dimension Quality Score
- `core/ai_benchmark.py` — compare models (offline from critical path)

## Env

| Var | Meaning |
|-----|---------|
| `VM_AI_SOURCE_MODE` | `local` \| `user_api` \| `tubedub_cloud` \| `future` |
| `VM_TRANSLATE_MODEL` | Active model tag |
| `VM_LLM_BASE_URL` | Endpoint |
| `OLLAMA_MODELS` | External models directory |

## Definition of Done checklist

- [x] Local path free by default
- [x] No auto-download
- [x] User chooses Local / My API / Cloud
- [x] Router fails over without reconfiguration
- [x] Semantic retry + Quality Score v2
- [x] New providers via unified adapter / catalog (no core rewrite for each)
- [x] Models stored outside app when user sets folder
- [x] Cloud optional
