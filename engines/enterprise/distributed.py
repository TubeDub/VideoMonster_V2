"""P807 Distributed Execution + P808 Task Orchestration (architecture)."""

from __future__ import annotations

import time
from typing import Any, Callable

from engines.enterprise.feature_flags import is_feature_enabled
from engines.enterprise.types import ComputeNodeKind, PipelineTask, TaskStatus

# Canonical pipeline stages → preferred node kind (logic unchanged)
STAGE_NODES: list[tuple[str, str]] = [
    ("Recognition", ComputeNodeKind.GPU.value),
    ("Semantic Core", ComputeNodeKind.CPU.value),
    ("Translation", ComputeNodeKind.GPU.value),
    ("Semantic Lock", ComputeNodeKind.CPU.value),
    ("Decision", ComputeNodeKind.CPU.value),
    ("Dub", ComputeNodeKind.CPU.value),
    ("Voice Platform", ComputeNodeKind.GPU.value),
    ("Scheduler", ComputeNodeKind.CPU.value),
    ("Alignment", ComputeNodeKind.CPU.value),
    ("Merge", ComputeNodeKind.CPU.value),
    ("Studio", ComputeNodeKind.CPU.value),
    ("Diagnostics", ComputeNodeKind.CPU.value),
    ("Export", ComputeNodeKind.CPU.value),
]


class TaskGraph:
    def __init__(self) -> None:
        self.tasks: dict[str, PipelineTask] = {}

    def add(self, task: PipelineTask) -> PipelineTask:
        self.tasks[task.task_uuid] = task
        return task

    def build_default_pipeline(self, project_id: str = "") -> list[PipelineTask]:
        prev: str | None = None
        ordered: list[PipelineTask] = []
        for name, kind in STAGE_NODES:
            t = PipelineTask(
                name=name,
                dependencies=[prev] if prev else [],
                node_kind=kind,
                payload={"project_id": project_id, "stage": name},
            )
            self.add(t)
            ordered.append(t)
            prev = t.task_uuid
        return ordered

    def ready_tasks(self) -> list[PipelineTask]:
        done = {
            tid
            for tid, t in self.tasks.items()
            if t.status == TaskStatus.SUCCEEDED.value
        }
        ready = []
        for t in self.tasks.values():
            if t.status != TaskStatus.PENDING.value:
                continue
            if all(d in done for d in t.dependencies):
                ready.append(t)
        return sorted(ready, key=lambda x: x.priority)

    def to_dict(self) -> dict[str, Any]:
        return {"tasks": [t.to_dict() for t in self.tasks.values()]}


class LocalTaskOrchestrator:
    """
    Executes task graph locally. Distributed workers activate when
    Feature Flag 'Distributed Pipeline' is ON (architecture ready).
    """

    def __init__(self) -> None:
        self.graph = TaskGraph()

    def plan(self, project_id: str = "") -> TaskGraph:
        self.graph = TaskGraph()
        self.graph.build_default_pipeline(project_id)
        return self.graph

    def run(
        self,
        *,
        handlers: dict[str, Callable[[PipelineTask], dict[str, Any]]] | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        handlers = handlers or {}
        distributed = is_feature_enabled("Distributed Pipeline", default=False)
        results = []
        # Topological: repeatedly pick ready tasks
        safety = 0
        while safety < 1000:
            safety += 1
            ready = self.graph.ready_tasks()
            if not ready:
                break
            for task in ready:
                task.status = TaskStatus.RUNNING.value
                t0 = time.perf_counter()
                try:
                    if dry_run:
                        out = {"ok": True, "dry_run": True, "node": task.node_kind}
                    elif task.name in handlers:
                        out = handlers[task.name](task)
                    else:
                        out = {"ok": True, "skipped": True}
                    if distributed:
                        out["distributed_eligible"] = True
                        out["assigned_node"] = task.node_kind
                    task.status = TaskStatus.SUCCEEDED.value
                    task.metrics = {
                        "elapsed_ms": (time.perf_counter() - t0) * 1000,
                        **(out if isinstance(out, dict) else {}),
                    }
                    results.append(task.to_dict())
                except Exception as exc:
                    retries = int(task.retry_policy.get("max") or 0)
                    attempt = int(task.metrics.get("attempts") or 0) + 1
                    task.metrics["attempts"] = attempt
                    if attempt <= retries:
                        task.status = TaskStatus.RETRYING.value
                        task.status = TaskStatus.PENDING.value
                    else:
                        task.status = TaskStatus.FAILED.value
                        task.metrics["error"] = str(exc)
                        results.append(task.to_dict())
                        return {
                            "ok": False,
                            "results": results,
                            "graph": self.graph.to_dict(),
                        }
        ok = all(
            t.status == TaskStatus.SUCCEEDED.value for t in self.graph.tasks.values()
        )
        return {"ok": ok, "results": results, "graph": self.graph.to_dict(), "distributed": distributed}
