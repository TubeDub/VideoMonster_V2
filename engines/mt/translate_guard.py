"""Translation timeout + hang diagnostics (Dev Log)."""

from __future__ import annotations

import logging
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TypeVar

logger = logging.getLogger("tubedub.engines.mt.translate_guard")

T = TypeVar("T")
DEFAULT_TIMEOUT_SEC = 60.0


class TranslationTimeoutError(TimeoutError):
    """User-visible translation stall."""

    user_message = (
        "Перевод занял слишком много времени. "
        "Попробуйте снова или уменьшите модель Whisper."
    )

    def __init__(self, detail: str = "", *, context: dict[str, Any] | None = None):
        self.detail = detail
        self.context = context or {}
        super().__init__(detail or self.user_message)


@dataclass
class TranslateContext:
    src_lang: str = ""
    tgt_lang: str = ""
    engine_id: str = ""
    route_label: str = ""
    segment_index: int = -1
    phase: str = "mt"
    extra: dict[str, Any] = field(default_factory=dict)


def is_dev_mode() -> bool:
    return os.getenv("VM_DEV_MODE", "").strip().lower() in ("1", "true", "yes")


def log_translation_hang(app_dir: Path, ctx: TranslateContext, *, elapsed_sec: float, reason: str) -> str:
    log_dir = app_dir / "output" / "dev"
    log_dir.mkdir(parents=True, exist_ok=True)
    jid = uuid.uuid4().hex[:12]
    path = log_dir / f"translate_hang_{jid}.log"
    lines = [
        f"=== TRANSLATION HANG / TIMEOUT ts={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} ===",
        f"reason={reason}",
        f"elapsed_sec={round(elapsed_sec, 2)}",
        f"engine={ctx.engine_id}",
        f"pair={ctx.src_lang}->{ctx.tgt_lang}",
        f"route={ctx.route_label}",
        f"segment_index={ctx.segment_index}",
        f"phase={ctx.phase}",
    ]
    for k, v in ctx.extra.items():
        lines.append(f"{k}={v}")
    text = "\n".join(lines) + "\n"
    path.write_text(text, encoding="utf-8")
    latest = log_dir / "translate_hang_latest.log"
    latest.write_text(text, encoding="utf-8")
    logger.error("[TranslateGuard] %s", reason)
    return str(path)


def run_with_timeout(
    fn: Callable[[], T],
    *,
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    app_dir: Path | None = None,
    ctx: TranslateContext | None = None,
    reason: str = "translation_timeout",
) -> T:
    """
    Dev/diagnostic only — do NOT use for production Marian/PyTorch inference.
    PyTorch in a worker thread hangs on Windows; use stable_translate instead.
    """
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(fn)
        try:
            return fut.result(timeout=timeout_sec)
        except FuturesTimeout as exc:
            elapsed = time.perf_counter() - t0
            if app_dir and ctx:
                log_translation_hang(app_dir, ctx, elapsed_sec=elapsed, reason=reason)
            raise TranslationTimeoutError(
                f"{reason} after {elapsed:.1f}s",
                context={
                    "engine": ctx.engine_id if ctx else "",
                    "pair": f"{ctx.src_lang}->{ctx.tgt_lang}" if ctx else "",
                    "route": ctx.route_label if ctx else "",
                    "segment_index": ctx.segment_index if ctx else -1,
                    "elapsed_sec": round(elapsed, 2),
                },
            ) from exc
