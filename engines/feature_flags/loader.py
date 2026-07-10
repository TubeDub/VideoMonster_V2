"""Safe import and execution wrappers."""

from __future__ import annotations

import importlib
import time
from pathlib import Path
from typing import Any, Callable, TypeVar

T = TypeVar("T")


def safe_import(module_path: str, *, feature_id: str, app_dir: Path) -> Any | None:
    from engines.feature_flags.dev_log import get_dev_log
    from engines.feature_flags.manager import get_feature_manager

    fm = get_feature_manager(app_dir)
    if not fm.is_enabled(feature_id, ignore_auto_disabled=False):
        return None
    log = get_dev_log(app_dir)
    t0 = time.perf_counter()
    try:
        mod = importlib.import_module(module_path.replace("/", ".").replace(".py", ""))
        log.log(
            event="import_ok",
            feature_id=feature_id,
            message=module_path,
            duration_ms=(time.perf_counter() - t0) * 1000,
        )
        return mod
    except Exception as e:
        fm.auto_disable(feature_id, reason=str(e))
        log.log_exception(feature_id, e, context=f"import {module_path}")
        return None


def safe_call(
    feature_id: str,
    fn: Callable[..., T],
    *args: Any,
    app_dir: Path | None = None,
    **kwargs: Any,
) -> T | None:
    from engines.feature_flags.dev_log import get_dev_log
    from engines.feature_flags.manager import get_feature_manager

    base = Path(app_dir or Path(__file__).resolve().parents[2])
    fm = get_feature_manager(base)
    fm.require(feature_id)
    log = get_dev_log(base)
    t0 = time.perf_counter()
    try:
        result = fn(*args, **kwargs)
        log.log(
            event="call_ok",
            feature_id=feature_id,
            message=getattr(fn, "__name__", "callable"),
            duration_ms=(time.perf_counter() - t0) * 1000,
        )
        return result
    except Exception as e:
        fm.auto_disable(feature_id, reason=str(e))
        log.log_exception(feature_id, e, context=getattr(fn, "__name__", "callable"))
        return None
