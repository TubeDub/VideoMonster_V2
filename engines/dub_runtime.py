"""Dub pipeline runtime tuning — UI responsiveness without touching translation."""

from __future__ import annotations

import os


def apply_ml_thread_limits() -> None:
    """Limit BLAS/torch threads so WebView + Flask stay responsive during dub."""
    n = os.getenv("VM_ML_THREADS", "2").strip() or "2"
    os.environ.setdefault("OMP_NUM_THREADS", n)
    os.environ.setdefault("MKL_NUM_THREADS", n)
    os.environ.setdefault("OPENBLAS_NUM_THREADS", n)
    os.environ.setdefault("NUMEXPR_NUM_THREADS", n)
    try:
        import torch

        torch.set_num_threads(max(1, int(n)))
    except Exception:
        pass
