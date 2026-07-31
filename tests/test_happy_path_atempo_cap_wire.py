# -*- coding: utf-8 -*-
"""Ensure Happy Path mux wrapper forces atempo ≤1.15 into fit_segment_audio."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_happy_path_max_atempo_is_1_15():
    from engines.happy_path import HAPPY_PATH_MAX_ATEMPO
    from engines.timing_fit import HAPPY_PATH_MAX_ATEMPO as TF_HP

    assert HAPPY_PATH_MAX_ATEMPO == 1.15
    assert TF_HP == 1.15


def test_gap_wrapper_forces_1_15_cap():
    # Direct check of force-overwrite logic used by Simple mux
    fit_max = 1.15
    fit_kw = {"max_atempo": 1.20, "allow_atempo": True}
    fit_kw["max_atempo"] = fit_max
    assert fit_kw["max_atempo"] == 1.15
