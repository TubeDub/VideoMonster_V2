"""Auto Health Monitor — per-engine statistics and routing priority."""

from __future__ import annotations

import json
import time
from pathlib import Path

from engines.enterprise_translation.config import HEALTH_FILE


def _path(app_dir: Path) -> Path:
    return app_dir / "data" / HEALTH_FILE


class EngineHealthRecord:
    def __init__(self):
        self.health_score: float = 50.0
        self.average_score: float = 0.0
        self.average_latency_ms: float = 0.0
        self.failure_rate: float = 0.0
        self.placeholder_survival_rate: float = 1.0
        self.grammar_score: float = 0.0
        self.semantic_accuracy: float = 0.0
        self.samples: int = 0
        self.wins: int = 0

    def to_dict(self) -> dict:
        return {
            "health_score": round(self.health_score, 2),
            "average_score": round(self.average_score, 2),
            "average_latency_ms": round(self.average_latency_ms, 1),
            "failure_rate": round(self.failure_rate, 3),
            "placeholder_survival_rate": round(self.placeholder_survival_rate, 3),
            "grammar_score": round(self.grammar_score, 3),
            "semantic_accuracy": round(self.semantic_accuracy, 3),
            "samples": self.samples,
            "wins": self.wins,
        }

    @classmethod
    def from_dict(cls, d: dict) -> EngineHealthRecord:
        r = cls()
        r.health_score = float(d.get("health_score", 50))
        r.average_score = float(d.get("average_score", 0))
        r.average_latency_ms = float(d.get("average_latency_ms", 0))
        r.failure_rate = float(d.get("failure_rate", 0))
        r.placeholder_survival_rate = float(d.get("placeholder_survival_rate", 1))
        r.grammar_score = float(d.get("grammar_score", 0))
        r.semantic_accuracy = float(d.get("semantic_accuracy", 0))
        r.samples = int(d.get("samples", 0))
        r.wins = int(d.get("wins", 0))
        return r


class AutoHealthMonitor:
    def __init__(self, app_dir: Path):
        self.app_dir = app_dir
        self._engines: dict[str, EngineHealthRecord] = {}
        self._load()

    def _load(self) -> None:
        path = _path(self.app_dir)
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for eid, row in (data.get("engines") or {}).items():
                self._engines[eid] = EngineHealthRecord.from_dict(row)
        except Exception:
            pass

    def save(self) -> None:
        path = _path(self.app_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "engines": {k: v.to_dict() for k, v in self._engines.items()},
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def record(
        self,
        engine_id: str,
        *,
        score: float,
        latency_ms: float,
        placeholder_ok: bool,
        failed: bool = False,
        grammar: float = 0.0,
        semantic: float = 0.0,
        won: bool = False,
    ) -> None:
        rec = self._engines.setdefault(engine_id, EngineHealthRecord())
        n = rec.samples
        rec.samples = n + 1
        rec.average_score = (rec.average_score * n + score) / (n + 1)
        rec.average_latency_ms = (rec.average_latency_ms * n + latency_ms) / (n + 1)
        if failed:
            rec.failure_rate = (rec.failure_rate * n + 1) / (n + 1)
        else:
            rec.failure_rate = (rec.failure_rate * n) / (n + 1)
        surv = 1.0 if placeholder_ok else 0.0
        rec.placeholder_survival_rate = (rec.placeholder_survival_rate * n + surv) / (n + 1)
        rec.grammar_score = (rec.grammar_score * n + grammar) / (n + 1)
        rec.semantic_accuracy = (rec.semantic_accuracy * n + semantic) / (n + 1)
        if won:
            rec.wins += 1
        # Health score composite
        rec.health_score = (
            rec.average_score * 0.35
            + rec.placeholder_survival_rate * 100 * 0.35
            + rec.semantic_accuracy * 100 * 0.15
            + (100 - min(100, rec.average_latency_ms / 50)) * 0.05
            + (1 - rec.failure_rate) * 100 * 0.10
        )
        self.save()

    def ranked_engines(self, engine_ids: list[str]) -> list[str]:
        """Sort engines by health score descending."""
        def key(eid: str) -> float:
            rec = self._engines.get(eid)
            return rec.health_score if rec else 50.0

        return sorted(engine_ids, key=key, reverse=True)

    def snapshot(self) -> dict:
        return {k: v.to_dict() for k, v in self._engines.items()}
