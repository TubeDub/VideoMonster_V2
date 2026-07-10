"""FX processing chain — Chain of Responsibility, non-blocking worker."""

from __future__ import annotations

import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from engines.dub_studio.fx.base import EffectContext, ProcessResult
from engines.dub_studio.fx.registry import get_plugin


@dataclass
class FxSlotSpec:
    plugin_id: str
    enabled: bool = True
    params: dict[str, Any] = field(default_factory=dict)


class FxChain:
    """Ordered plugin chain — order can be reordered (drag-and-drop in UI)."""

    def __init__(self, slots: list[FxSlotSpec] | None = None):
        self.slots: list[FxSlotSpec] = list(slots or [])

    def reorder(self, from_idx: int, to_idx: int) -> None:
        if from_idx < 0 or from_idx >= len(self.slots):
            return
        item = self.slots.pop(from_idx)
        to_idx = max(0, min(to_idx, len(self.slots)))
        self.slots.insert(to_idx, item)

    def process_sync(
        self,
        input_path: Path,
        work_dir: Path,
        *,
        ctx: EffectContext | None = None,
    ) -> ProcessResult:
        work_dir.mkdir(parents=True, exist_ok=True)
        cur = Path(input_path)
        if not self.slots:
            out = work_dir / f"chain_{uuid.uuid4().hex[:8]}.wav"
            shutil.copy2(cur, out)
            return ProcessResult(str(out))

        for i, slot in enumerate(self.slots):
            if not slot.enabled:
                continue
            plugin = get_plugin(slot.plugin_id)
            nxt = work_dir / f"fx_{i}_{slot.plugin_id}.wav"
            result = plugin.process(cur, nxt, params=slot.params, ctx=ctx)
            cur = Path(result.output_path)
        return ProcessResult(str(cur))


class FxPipeline:
    """Background FX worker pool — UI thread stays responsive."""

    def __init__(self, *, max_workers: int = 2):
        self._pool = ThreadPoolExecutor(max_workers=max(1, max_workers))
        self._lock = threading.RLock()

    def submit(
        self,
        chain: FxChain,
        input_path: Path,
        work_dir: Path,
        *,
        ctx: EffectContext | None = None,
        on_done: Callable[[ProcessResult], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ):
        def _run():
            try:
                result = chain.process_sync(input_path, work_dir, ctx=ctx)
                if on_done:
                    on_done(result)
            except Exception as e:
                if on_error:
                    on_error(e)

        return self._pool.submit(_run)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
