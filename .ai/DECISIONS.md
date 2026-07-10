# Architecture Decisions

## ADR-001: Event Bus as Foundation

**Decision:** All pipeline communication via `AsyncEventBus`.  
**Rationale:** Decouples stages, enables orchestrator supervision.  
**Date:** Stage 1

## ADR-002: LLM Dispatcher Chokepoint

**Decision:** All LLM traffic through `LLMDispatcher.execute_chat()`.  
**Rationale:** Single point for cache, memory context, failover.  
**Date:** Stage 3

## ADR-003: Plugin-First Extensions

**Decision:** New capabilities via `plugins/` + SDK, not core edits.  
**Rationale:** Core stability, ecosystem growth.  
**Date:** Stage 9

## ADR-004: Human-in-the-Loop AI

**Decision:** AI never auto-modifies code or architecture.  
**Rationale:** Developer retains control, prevents drift.  
**Date:** Stage 10

## ADR-005: Project Brain

**Decision:** All AI tools read/write `.ai/` directory only.  
**Rationale:** Single source of truth for project knowledge.  
**Date:** Stage 10

*Add new decisions here. Never delete — mark deprecated instead (§16).*
