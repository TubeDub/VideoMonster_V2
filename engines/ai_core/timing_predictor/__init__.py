"""Timing predictor interface hierarchy (TZ Stage 6)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class BaseTimingPredictor(ABC):
    """Replaceable timing model — agents depend on this interface only."""

    name: str = "base"

    @abstractmethod
    def predict_ms(self, text: str, lang: str = "ru", **kwargs: Any) -> int:
        """Predict TTS duration in milliseconds."""

    def predict_with_meta(self, text: str, lang: str = "ru", **kwargs: Any) -> dict[str, Any]:
        ms = self.predict_ms(text, lang, **kwargs)
        return {"duration_ms": ms, "predictor": self.name, "lang": lang}


class HeuristicTimingPredictor(BaseTimingPredictor):
    """Chars/sec heuristic — wraps existing duration_predictor."""

    name = "heuristic"

    def predict_ms(self, text: str, lang: str = "ru", **kwargs: Any) -> int:
        from engines.ai_core.timing_agent.duration_predictor import predict_duration_ms

        return int(
            predict_duration_ms(
                text,
                lang,
                use_cache=bool(kwargs.get("use_cache", True)),
                app_dir=kwargs.get("app_dir"),
            )
        )


class AdaptiveTimingPredictor(BaseTimingPredictor):
    """Learns from probe cache + segment history; falls back to heuristic."""

    name = "adaptive"

    def predict_ms(self, text: str, lang: str = "ru", **kwargs: Any) -> int:
        heuristic = HeuristicTimingPredictor()
        base = heuristic.predict_ms(text, lang, **kwargs)
        run_id = str(kwargs.get("run_id") or "")
        segment_index = kwargs.get("segment_index")
        if run_id and segment_index is not None:
            try:
                from engines.ai_core.services.ai_memory import get_memory_service

                hist = get_memory_service(run_id).get_segment_history(int(segment_index))
                errors = [
                    int(e["timing_error_ms"])
                    for e in hist
                    if e.get("timing_error_ms") is not None
                ]
                if errors:
                    bias = sum(errors) / len(errors)
                    return max(0, int(base - bias * 0.25))
            except Exception:
                pass
        return base


class FutureAITimingPredictor(BaseTimingPredictor):
    """Placeholder for ML-based predictor — delegates to adaptive until model exists."""

    name = "future_ai"

    def predict_ms(self, text: str, lang: str = "ru", **kwargs: Any) -> int:
        return AdaptiveTimingPredictor().predict_ms(text, lang, **kwargs)


def get_timing_predictor(*, developer_session: bool = False) -> BaseTimingPredictor:
    """Select predictor via feature flags without changing call sites."""
    try:
        from engines.ai_core.platform.feature_registry import is_platform_feature_enabled

        if is_platform_feature_enabled("future_ai_timing", developer_session=developer_session):
            return FutureAITimingPredictor()
        if is_platform_feature_enabled(
            "adaptive_timing_predictor", developer_session=developer_session
        ):
            return AdaptiveTimingPredictor()
    except Exception:
        pass
    return HeuristicTimingPredictor()
