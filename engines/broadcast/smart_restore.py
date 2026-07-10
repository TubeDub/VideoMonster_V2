"""SmartRestore — fuzzy token repair + incident logging."""

from __future__ import annotations

import difflib
import json
import logging
import re
import time
from pathlib import Path

from engines.broadcast.config import INCIDENTS_LOG, TOKEN_PREFIX, TOKEN_SUFFIX
from engines.broadcast.gatekeeper import PipelineGateKeeper
from engines.broadcast.termbase import Termbase

logger = logging.getLogger("tubedub.broadcast.smart_restore")

_TOKEN_CANON = re.compile(
    re.escape(TOKEN_PREFIX) + r"(\d+)" + re.escape(TOKEN_SUFFIX)
)
_TOKEN_LOOSE = re.compile(r"\[\s*#+\s*(\d+)\s*#+\s*\]", re.IGNORECASE)


def _levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        return _levenshtein(b, a)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        cur = [i + 1]
        for j, cb in enumerate(b):
            cur.append(min(prev[j + 1] + 1, cur[j] + 1, prev[j] + (ca != cb)))
        prev = cur
    return prev[-1]


class SmartRestore:
    """Auto-correct corrupted [##ID##] tokens; log engine quality incidents."""

    def __init__(self, app_dir: Path | None = None):
        self.app_dir = app_dir
        self._incidents: list[dict] = []

    def _log_path(self) -> Path | None:
        if not self.app_dir:
            return None
        return self.app_dir / "logs" / INCIDENTS_LOG

    def _record_incident(
        self,
        *,
        term_id: int,
        engine: str,
        damaged: str,
        corrected: str,
        segment_index: int = -1,
    ) -> None:
        row = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "level": "WARNING",
            "message": (
                f"Entity ID {term_id} was corrupted by engine {engine}, auto-corrected"
            ),
            "term_id": term_id,
            "engine": engine,
            "damaged": damaged,
            "corrected": corrected,
            "segment_index": segment_index,
        }
        self._incidents.append(row)
        logger.warning(row["message"])
        path = self._log_path()
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _find_fuzzy_token(self, text: str, term_id: int) -> str | None:
        canonical = f"{TOKEN_PREFIX}{term_id}{TOKEN_SUFFIX}"
        if canonical in text:
            return canonical
        candidates: list[str] = []
        for m in _TOKEN_LOOSE.finditer(text):
            if int(m.group(1)) == term_id:
                candidates.append(m.group(0))
        for m in _TOKEN_CANON.finditer(text):
            if int(m.group(1)) == term_id:
                candidates.append(m.group(0))
        if not candidates:
            return None
        best = canonical
        best_score = 0.0
        for cand in candidates:
            ratio = difflib.SequenceMatcher(None, cand, canonical).ratio()
            lev = _levenshtein(cand, canonical)
            max_len = max(len(cand), len(canonical), 1)
            score = max(ratio, 1.0 - lev / max_len)
            if score > best_score:
                best_score = score
                best = cand
        return best if best_score >= 0.72 else None

    def restore_tokens_in_text(
        self,
        text: str,
        termbase: Termbase,
        *,
        engine: str = "",
        segment_index: int = -1,
        original_masked: str = "",
    ) -> tuple[str, list[dict]]:
        """
        Fix corrupted tokens in translated text.
        Returns (restored_text, incident list).
        """
        working = str(text or "")
        incidents: list[dict] = []
        expected_ids = PipelineGateKeeper.extract_token_ids(original_masked or working)

        for tid in sorted(expected_ids):
            canonical = f"{TOKEN_PREFIX}{tid}{TOKEN_SUFFIX}"
            entry = termbase.get(tid)
            display = entry.display if entry else canonical

            if canonical in working:
                working = working.replace(canonical, display)
                continue

            fuzzy = self._find_fuzzy_token(working, tid)
            if fuzzy and fuzzy != canonical:
                self._record_incident(
                    term_id=tid,
                    engine=engine,
                    damaged=fuzzy,
                    corrected=canonical,
                    segment_index=segment_index,
                )
                incidents.append(
                    {
                        "term_id": tid,
                        "engine": engine,
                        "damaged": fuzzy,
                        "corrected": canonical,
                    }
                )
                working = working.replace(fuzzy, display)
            elif fuzzy:
                working = working.replace(fuzzy, display)
            else:
                incidents.append(
                    {
                        "term_id": tid,
                        "engine": engine,
                        "failed": True,
                        "error": f"Token {tid} not recoverable",
                    }
                )

        return working, incidents

    @property
    def incidents(self) -> list[dict]:
        return list(self._incidents)
