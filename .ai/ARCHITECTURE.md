# VideoMonster V2 Architecture

*Auto-generated: 2026-08-22 19:56*

## Platform Layers (Stages 1–10)

### Stage 1
- `core/event_bus.py`
- `core/event_types.py`
- `core/event_pipeline.py`

### Stage 2
- `core/orchestrator.py`
- `core/resource_monitor.py`

### Stage 3
- `core/llm_dispatcher.py`
- `core/model_registry.py`
- `llm_adapters/`

### Stage 4
- `core/chunk_manager.py`
- `core/pipeline_engine.py`

### Stage 5
- `core/recovery_manager.py`
- `core/micro_validator.py`

### Stage 6
- `core/ai_memory.py`
- `core/semantic_cache.py`

### Stage 7
- `core/hardware_profiler.py`
- `core/benchmark.py`
- `core/performance_optimizer.py`
- `core/performance_monitor.py`

### Stage 8
- `core/monitoring_center.py`
- `core/diagnostics.py`
- `core/bottleneck_analyzer.py`
- `core/analytics_db.py`

### Stage 9
- `core/plugin_manager.py`
- `core/plugin_api.py`
- `sdk/`

### Stage 10
- `core/architecture_engine.py`
- `core/dev_assistant.py`
- `core/technical_debt.py`
- `core/task_planner.py`

## Core Modules

- `core/__init__.py` (211 lines, stage 0)
- `core/ai_benchmark.py` (188 lines, stage 0)
- `core/ai_memory.py` (667 lines, stage 6)
- `core/ai_router.py` (370 lines, stage 0)
- `core/ai_sources.py` (369 lines, stage 0)
- `core/analytics_db.py` (210 lines, stage 8)
- `core/architecture_engine.py` (225 lines, stage 10)
- `core/benchmark.py` (237 lines, stage 7)
- `core/bottleneck_analyzer.py` (206 lines, stage 8)
- `core/change_impact.py` (103 lines, stage 0)
- `core/chunk_manager.py` (379 lines, stage 4)
- `core/code_reviewer.py` (142 lines, stage 0)
- `core/dev_assistant.py` (253 lines, stage 10)
- `core/development_history.py` (169 lines, stage 0)
- `core/diagnostics.py` (246 lines, stage 8)
- `core/doc_sync.py` (161 lines, stage 0)
- `core/event_agents.py` (427 lines, stage 0)
- `core/event_bus.py` (207 lines, stage 1)
- `core/event_pipeline.py` (449 lines, stage 1)
- `core/event_types.py` (172 lines, stage 1)
- `core/hardware_profiler.py` (501 lines, stage 7)
- `core/knowledge_base.py` (179 lines, stage 0)
- `core/llm_dispatcher.py` (446 lines, stage 3)
- `core/micro_validator.py` (255 lines, stage 5)
- `core/model_registry.py` (362 lines, stage 3)
- `core/monitoring_center.py` (793 lines, stage 8)
- `core/orchestrator.py` (629 lines, stage 2)
- `core/performance_monitor.py` (326 lines, stage 7)
- `core/performance_optimizer.py` (653 lines, stage 7)
- `core/pipeline_engine.py` (676 lines, stage 4)
- `core/plugin_api.py` (212 lines, stage 9)
- `core/plugin_manager.py` (988 lines, stage 9)
- `core/recommendation_engine.py` (131 lines, stage 0)
- `core/recovery_manager.py` (564 lines, stage 5)
- `core/refactoring_advisor.py` (96 lines, stage 0)
- `core/report_exporter.py` (205 lines, stage 0)
- `core/resource_monitor.py` (123 lines, stage 2)
- `core/semantic_cache.py` (349 lines, stage 6)
- `core/semantic_retry.py` (207 lines, stage 0)
- `core/task_planner.py` (166 lines, stage 10)
- `core/technical_debt.py` (173 lines, stage 10)

## Dependency Graph (core/)

```
core/__init__.py → core.event_bus, core.event_pipeline, core.event_types, core.orchestrator, core.llm_dispatcher, core.model_registry, core.chunk_manager, core.pipeline_engine, core.micro_validator, core.recovery_manager, core.semantic_cache, core.ai_memory, core.hardware_profiler, core.benchmark, core.performance_optimizer, core.performance_monitor, core.analytics_db, core.monitoring_center, core.diagnostics, core.bottleneck_analyzer, core.report_exporter, core.plugin_api, core.plugin_manager, core.dev_assistant, core.architecture_engine, core.ai_router, core.ai_sources
core/ai_benchmark.py → core.ai_router
core/ai_memory.py → core.semantic_cache
core/ai_router.py → core.ai_sources, core.hardware_profiler, core.ai_sources, core.ai_sources
core/bottleneck_analyzer.py → core.performance_optimizer
core/change_impact.py → core.architecture_engine
core/chunk_manager.py → core.resource_monitor
core/code_reviewer.py → core.architecture_engine, core.technical_debt
core/dev_assistant.py → core.architecture_engine, core.change_impact, core.code_reviewer, core.development_history, core.doc_sync, core.knowledge_base, core.recommendation_engine, core.refactoring_advisor, core.task_planner, core.technical_debt, core.monitoring_center
core/doc_sync.py → core.architecture_engine, core.performance_optimizer, core.technical_debt
core/event_agents.py → core.event_bus, core.event_types
core/event_bus.py → core.event_types
core/event_pipeline.py → core.event_agents, core.event_bus, core.event_types, core.pipeline_engine, core.orchestrator, core.orchestrator, core.performance_optimizer, core.performance_monitor, core.monitoring_center, core.dev_assistant
core/llm_dispatcher.py → core.model_registry, core.semantic_cache, core.ai_memory, core.semantic_cache
core/monitoring_center.py → core.analytics_db, core.bottleneck_analyzer, core.diagnostics, core.report_exporter, core.orchestrator, core.pipeline_engine, core.llm_dispatcher, core.recovery_manager, core.ai_memory, core.performance_monitor, core.performance_optimizer, core.hardware_profiler, core.bottleneck_analyzer, core.performance_monitor, core.resource_monitor
core/orchestrator.py → core.event_bus, core.event_types, core.resource_monitor, core.event_pipeline, core.event_types
core/performance_optimizer.py → core.benchmark, core.hardware_profiler
core/pipeline_engine.py → core.chunk_manager, core.micro_validator, core.recovery_manager, core.ai_memory
core/plugin_manager.py → core.plugin_api
core/recommendation_engine.py → core.performance_optimizer, core.monitoring_center, core.refactoring_advisor, core.plugin_manager, core.knowledge_base
core/recovery_manager.py → core.micro_validator, core.orchestrator
core/refactoring_advisor.py → core.technical_debt
```
