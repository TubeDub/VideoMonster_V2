"""Semantic Cache — avoid redundant LLM calls (TZ #6 §2, §9, §12).

Before every LLM request, checks whether an equivalent translation already
exists. On hit, returns the cached result immediately — no LLM call.

Matching strategy:
1. Exact semantic fingerprint (SHA-256 of normalised text + context).
2. Fuzzy token-overlap search when fingerprint misses (Jaccard ≥ threshold).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("tubedub.semantic_cache")

_TOKEN_RE = re.compile(r"[\w']+", re.UNICODE)
_DEFAULT_THRESHOLD = 0.85


def semantic_cache_enabled() -> bool:
    return str(os.getenv("VM_SEMANTIC_CACHE", "1")).strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _normalize(text: str) -> str:
    t = str(text or "").strip().lower()
    t = re.sub(r"\s+", " ", t)
    return t


def _tokenize(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(_normalize(text)))


def semantic_fingerprint(
    text: str,
    *,
    source_lang: str = "",
    target_lang: str = "",
    context: str = "",
    task_type: str = "",
) -> str:
    """Semantic hash for a segment (§12)."""
    core = "|".join([
        _normalize(text),
        source_lang.lower(),
        target_lang.lower(),
        _normalize(context)[:200],
        task_type.lower(),
    ])
    return hashlib.sha256(core.encode("utf-8")).hexdigest()


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


@dataclass
class CacheHit:
    text: str
    fingerprint: str
    similarity: float = 1.0
    source: str = "exact"  # exact | fuzzy
    model: str = ""
    hit_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "fingerprint": self.fingerprint,
            "similarity": round(self.similarity, 3),
            "source": self.source,
            "model": self.model,
            "hit_count": self.hit_count,
        }


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    fuzzy_hits: int = 0
    stores: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return round(self.hits / total, 3) if total else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "fuzzy_hits": self.fuzzy_hits,
            "stores": self.stores,
            "hit_rate": self.hit_rate,
        }


class SemanticCache:
    """Persistent semantic translation cache backed by SQLite."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        similarity_threshold: float = _DEFAULT_THRESHOLD,
    ) -> None:
        if db_path is None:
            base = Path(os.getenv("VM_MEMORY_DIR", "")) or Path("data") / "memory"
            base.mkdir(parents=True, exist_ok=True)
            db_path = base / "semantic_cache.db"
        self.db_path = Path(db_path)
        self.threshold = similarity_threshold
        self.stats = CacheStats()
        self._lock = threading.RLock()
        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cache_entries (
                    fingerprint TEXT PRIMARY KEY,
                    source_text TEXT NOT NULL,
                    result_text TEXT NOT NULL,
                    source_lang TEXT DEFAULT '',
                    target_lang TEXT DEFAULT '',
                    context TEXT DEFAULT '',
                    task_type TEXT DEFAULT '',
                    model TEXT DEFAULT '',
                    tokens TEXT DEFAULT '',
                    hit_count INTEGER DEFAULT 0,
                    created_at REAL,
                    updated_at REAL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_source_lang ON cache_entries(source_lang, target_lang)"
            )
            conn.commit()
            conn.close()

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path), check_same_thread=False)

    def lookup(
        self,
        text: str,
        *,
        source_lang: str = "",
        target_lang: str = "",
        context: str = "",
        task_type: str = "",
    ) -> CacheHit | None:
        """Check cache before LLM call (§2). Returns hit or None."""
        if not semantic_cache_enabled():
            return None

        fp = semantic_fingerprint(
            text, source_lang=source_lang, target_lang=target_lang,
            context=context, task_type=task_type,
        )

        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute(
                    "SELECT result_text, model, hit_count FROM cache_entries WHERE fingerprint=?",
                    (fp,),
                ).fetchone()
                if row:
                    result, model, hits = row
                    conn.execute(
                        "UPDATE cache_entries SET hit_count=hit_count+1, updated_at=? WHERE fingerprint=?",
                        (time.time(), fp),
                    )
                    conn.commit()
                    self.stats.hits += 1
                    return CacheHit(
                        text=result, fingerprint=fp, similarity=1.0,
                        source="exact", model=model or "", hit_count=hits + 1,
                    )

                # Fuzzy search (§9).
                query_tokens = _tokenize(text)
                if not query_tokens:
                    self.stats.misses += 1
                    return None

                rows = conn.execute(
                    "SELECT fingerprint, source_text, result_text, tokens, model, hit_count "
                    "FROM cache_entries WHERE source_lang=? AND target_lang=? AND task_type=? "
                    "ORDER BY hit_count DESC LIMIT 200",
                    (source_lang, target_lang, task_type),
                ).fetchall()

                best_sim = 0.0
                best_row = None
                for row_fp, src, result, tokens_str, model, hits in rows:
                    cached_tokens = set(tokens_str.split("|")) if tokens_str else _tokenize(src)
                    sim = _jaccard(query_tokens, cached_tokens)
                    if sim > best_sim:
                        best_sim = sim
                        best_row = (row_fp, result, model, hits)

                if best_row and best_sim >= self.threshold:
                    row_fp, result, model, hits = best_row
                    conn.execute(
                        "UPDATE cache_entries SET hit_count=hit_count+1, updated_at=? WHERE fingerprint=?",
                        (time.time(), row_fp),
                    )
                    conn.commit()
                    self.stats.hits += 1
                    self.stats.fuzzy_hits += 1
                    return CacheHit(
                        text=result, fingerprint=row_fp, similarity=best_sim,
                        source="fuzzy", model=model or "", hit_count=hits + 1,
                    )

                self.stats.misses += 1
                return None
            finally:
                conn.close()

    def store(
        self,
        source_text: str,
        result_text: str,
        *,
        source_lang: str = "",
        target_lang: str = "",
        context: str = "",
        task_type: str = "",
        model: str = "",
    ) -> str:
        """Save a successful translation result."""
        if not result_text or not source_text:
            return ""
        fp = semantic_fingerprint(
            source_text, source_lang=source_lang, target_lang=target_lang,
            context=context, task_type=task_type,
        )
        tokens = "|".join(sorted(_tokenize(source_text)))
        now = time.time()
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO cache_entries "
                    "(fingerprint, source_text, result_text, source_lang, target_lang, "
                    "context, task_type, model, tokens, hit_count, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, "
                    "COALESCE((SELECT hit_count FROM cache_entries WHERE fingerprint=?), 0), "
                    "COALESCE((SELECT created_at FROM cache_entries WHERE fingerprint=?), ?), ?)",
                    (fp, source_text, result_text, source_lang, target_lang,
                     context, task_type, model, tokens, fp, fp, now, now),
                )
                conn.commit()
                self.stats.stores += 1
            finally:
                conn.close()
        return fp

    def search(
        self,
        query: str,
        *,
        source_lang: str = "",
        target_lang: str = "",
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Semantic search over cached entries (§9)."""
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []
        results: list[dict[str, Any]] = []
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    "SELECT source_text, result_text, tokens, hit_count "
                    "FROM cache_entries WHERE source_lang=? AND target_lang=? "
                    "ORDER BY hit_count DESC LIMIT 500",
                    (source_lang, target_lang),
                ).fetchall()
                for src, result, tokens_str, hits in rows:
                    cached_tokens = set(tokens_str.split("|")) if tokens_str else _tokenize(src)
                    sim = _jaccard(query_tokens, cached_tokens)
                    if sim >= self.threshold * 0.7:
                        results.append({
                            "source": src,
                            "result": result,
                            "similarity": round(sim, 3),
                            "hit_count": hits,
                        })
            finally:
                conn.close()
        results.sort(key=lambda x: x["similarity"], reverse=True)
        return results[:limit]

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            conn = self._conn()
            try:
                count = conn.execute("SELECT COUNT(*) FROM cache_entries").fetchone()[0]
            finally:
                conn.close()
        return {
            "enabled": semantic_cache_enabled(),
            "entries": count,
            "threshold": self.threshold,
            "stats": self.stats.to_dict(),
            "db_path": str(self.db_path),
        }


_cache: SemanticCache | None = None
_cache_lock = threading.Lock()


def get_semantic_cache() -> SemanticCache:
    global _cache
    if _cache is None:
        with _cache_lock:
            if _cache is None:
                _cache = SemanticCache()
    return _cache
