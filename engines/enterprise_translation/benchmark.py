"""Engine Benchmark — auto-select best serializer format per engine."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Callable

from engines.enterprise_translation.config import DEFAULT_BENCHMARK_SAMPLES
from engines.enterprise_translation.registry import PlaceholderRegistry
from engines.enterprise_translation.serializer import EntitySerializer
from engines.enterprise_translation.types import EntityType

logger = logging.getLogger(__name__)

_BENCH_SENTENCES = [
    "George Jr. works at Microsoft in Seattle.",
    "The film Avatar was directed by James Cameron.",
    "Apple announced a new product yesterday.",
    "Maria Garcia lives in Madrid.",
    "The World Cup 2026 will be held in North America.",
    "John Smith met with Google executives.",
    "Tesla opened a factory in Berlin.",
    "The event starts on March 15.",
    "Samsung released Galaxy S25 today.",
    "Barack Obama visited Paris last year.",
]


def _sample_count() -> int:
    if os.getenv("VM_ET_BENCH_FULL", "").strip().lower() in ("1", "true", "yes"):
        return 100
    return DEFAULT_BENCHMARK_SAMPLES


def run_engine_benchmark(
    engine_id: str,
    translate_fn: Callable[[str, str, str], str],
    app_dir: Path | None,
    *,
    source_lang: str = "en",
    target_lang: str = "ru",
) -> str:
    """
    Test all serializer formats; pick best survival rate.
    Returns chosen format_key and persists via EntitySerializer.
    """
    serializer = EntitySerializer(app_dir)
    formats = serializer.all_format_keys()
    samples = (_BENCH_SENTENCES * 10)[: _sample_count()]

    best_fmt = "bracket_double"
    best_rate = -1.0

    for fmt_key in formats:
        survived = 0
        total = 0
        for sent in samples:
            reg = PlaceholderRegistry()
            rec = reg.register("George Jr.", EntityType.PERSON, display="Джордж-младший")
            rec2 = reg.register("Microsoft", EntityType.ORG, display="Microsoft")
            # force format temporarily
            if app_dir:
                serializer._overrides[engine_id.lower()] = fmt_key  # noqa: SLF001
            token1 = serializer.get_token_for_engine(rec.entity_id, engine_id)
            token2 = serializer.get_token_for_engine(rec2.entity_id, engine_id)
            masked = sent.replace("George Jr.", token1).replace("Microsoft", token2)
            total += 2
            try:
                out = translate_fn(masked, source_lang, target_lang)
            except Exception:
                continue
            if token1 in out:
                survived += 1
            if token2 in out:
                survived += 1

        rate = survived / max(total, 1)
        if rate > best_rate:
            best_rate = rate
            best_fmt = fmt_key

    serializer.save_override(engine_id, best_fmt)
    logger.info(
        "Engine benchmark %s: format=%s survival=%.2f",
        engine_id,
        best_fmt,
        best_rate,
    )
    return best_fmt
