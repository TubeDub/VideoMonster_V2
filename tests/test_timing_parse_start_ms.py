# -*- coding: utf-8 -*-
"""timing maps use start_ms/end_ms — parsers must not KeyError (_tmp_uk_fix)."""

from __future__ import annotations


def test_timing_engine_parse_start_ms():
    from engines.timing_engine import parse_timing

    start, end = parse_timing({"start_ms": 590, "end_ms": 2590})
    assert start == 590
    assert end == 2590


def test_timing_engine_parse_start_legacy():
    from engines.timing_engine import parse_timing

    start, end = parse_timing({"start": 100, "end": 200})
    assert start == 100
    assert end == 200


def test_timing_fit_parse_start_ms():
    from engines.timing_fit import _parse_timing

    start, end = _parse_timing({"start_ms": 590, "end_ms": 2590})
    assert start == 590
    assert end == 2590


def test_auto_dub_parse_timing_start_ms():
    from api.auto_dub_api import _parse_timing

    start, end = _parse_timing({"start_ms": 590, "end_ms": 2590})
    assert start == 590
    assert end == 2590
