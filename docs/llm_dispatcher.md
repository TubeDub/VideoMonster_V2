# LLM Dispatcher — Stage 3 (TZ #3)

## Goal

Fully decouple VideoMonster V2 from any specific LLM (Ollama, Qwen, DeepSeek,
OpenAI, Claude, Gemini, …) and provide **one intelligent layer** that picks the
best model for each task.

**Principle:** All model access goes through the Dispatcher. No module knows which
model is used — everyone knows only the Dispatcher.

## Modules

| Module | Role |
|--------|------|
| `core/llm_dispatcher.py` | Single entry point; selection, failover, hot-swap, balancing, limits, stats, health |
| `core/model_registry.py` | Uniform model descriptors + live per-model statistics |
| `llm_adapters/` | One adapter per backend (uniform interface) |

## Single entry point (§1)

```python
from core.llm_dispatcher import get_dispatcher
d = get_dispatcher()
d.generate(prompt)   d.translate(prompt)  d.review(prompt)
d.rewrite(prompt)    d.summary(prompt)    d.fix_json(prompt)
```

No module calls `ollama.chat` / `openai.chat` / `claude.messages` directly.

### How every existing caller already routes through it

All real LLM chat in the codebase funnels through the single transport chokepoint
`engines/translation_adapt._llm_chat_once`. That function now:

1. Keeps its guards (semaphore, budget, in-flight, logging, circuit breaker).
2. Delegates model selection + network send to the Dispatcher (unless a caller
   forced an explicit model or the dispatcher is disabled).
3. Falls back to a direct send if the dispatcher yields no model — **zero
   regression** when only one model is configured.

The raw HTTP was extracted into `_raw_chat_send()` and is shared by both the
direct path and the OpenAI-compatible adapter, so request bytes are identical.

## Model registry (§2)

Each `ModelDescriptor` carries: name, provider, kind (local/cloud), adapter,
`param_b`, `context_tokens`, avg speed + quality (via `ModelStats`), `supports_json`,
`supports_tools`, `max_concurrency`, `cost_per_1k`, `priority`, `tier`, `status`.

Discovery reuses `llm_orchestrator.model_pool` + `llm_adaptation_mode`, plus cloud
models when API keys are present.

## Supported models (§3)

OpenAI-compatible (Ollama, Qwen, DeepSeek, Gemma, Llama, Mistral, vLLM, LM Studio,
OpenAI, OpenRouter) via one adapter; Claude and Gemini via dedicated adapters.
Adding a model = adding one adapter + registering it. Core never changes.

## Adapter interface (§4)

```python
class LLMAdapter:
    connect()          # prepare/verify endpoint
    generate(request)  # one completion → ChatResult (never raises)
    health()           # liveness + speed → HealthReport
    cancel()           # best-effort cancel
    estimate_time(req) # predicted latency
    estimate_tokens(s) # rough token count
```

Register new adapters with `llm_adapters.register_adapter(MyAdapter)`.

## Health monitoring (§5)

`d.refresh_health()` (or `d.start_health_monitor(interval_s=5)`) polls each adapter:
alive / stalled / last & avg latency / error count / GPU / network. Status feeds the
registry and is surfaced to the AI Orchestrator via the status API.

## Automatic selection (§6) + quality-first (§7)

Selection ranks candidates by **quality first**: strongest tier → priority → least
loaded → fewest recent errors. The Dispatcher never downgrades to a weaker model
just because it is faster. Speed comes from parallelism, queues, the conveyor,
load-balancing and caching — not from lower quality.

## Failover (§8)

On stall / unavailable / timeout / error, the request automatically moves to the
next model. Order is configurable:

```
VM_LLM_FAILOVER_CHAIN=qwen,deepseek,gemma,claude,openai
```

## Hot swap (§9)

```python
d.set_active_model("claude-3-5-sonnet-latest")  # remaining chunks use it
d.set_active_model(None)                          # back to auto-select
```

No film restart — selection reads the active model on every call.

## Load balancing (§10)

With several local models, equally-good candidates are ordered by current load, so
independent tasks/chunks spread across models (e.g. chunk 1 → Qwen, chunk 2 →
DeepSeek) while quality stays identical.

## Resource limits (§11)

Per-model `threading.Semaphore(max_concurrency)` prevents overloading a model.
If a model is saturated, the Dispatcher tries the next equally-good model instead
of queuing indefinitely.

## Statistics (§12)

Per model: requests, avg response time, avg quality, timeouts, errors, success
rate, avg generation length, RAM/VRAM. Exposed via `d.get_status()` for the
AI Orchestrator.

## HTTP API (developer mode)

- `GET  /api/pipeline/llm_dispatcher/status` — registry + stats + active model
- `POST /api/pipeline/llm_dispatcher/model` — `{"model": "..."}` hot-swap (empty → auto)

## Configuration

| Variable | Default | Meaning |
|----------|---------|---------|
| `VM_LLM_DISPATCHER` | `1` | Route LLM calls through the Dispatcher |
| `VM_LLM_FAILOVER_CHAIN` | — | Comma-separated failover order |
| `OPENAI_API_KEY` / `VM_OPENAI_MODEL` | — | OpenAI-compatible cloud |
| `ANTHROPIC_API_KEY` / `VM_ANTHROPIC_MODEL` | — | Claude |
| `GEMINI_API_KEY` / `VM_GEMINI_MODEL` | — | Gemini |

## Tests

`tests/test_llm_dispatcher.py` — entry points, quality-first selection, failover,
hot-swap, load-limit semaphore, stats, health, configurable chain (fake adapters,
no network).

## Next stage

**Adaptive Chunking & intelligent conveyor** — the Dispatcher now exposes model
capabilities (context window, speed, tiers) and per-model stats that the next stage
uses to size chunks and route work optimally.
