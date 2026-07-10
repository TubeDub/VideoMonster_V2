"""AI Memory Engine — project knowledge & consistency (TZ #6 §1–§15).

Central memory for characters, terminology, style, voices, and film context.
All modules access memory exclusively through this API:

    memory.find() / memory.save() / memory.learn() / memory.update()
    memory.search() / memory.get_character() / memory.get_style()
    memory.get_voice() / memory.get_glossary()
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.semantic_cache import SemanticCache, get_semantic_cache, semantic_cache_enabled

logger = logging.getLogger("tubedub.ai_memory")

_TOKEN_RE = re.compile(r"[\w']+", re.UNICODE)


def memory_enabled() -> bool:
    return str(os.getenv("VM_AI_MEMORY", "1")).strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


@dataclass
class MemoryEntry:
    """Generic memory record."""

    key: str
    value: str
    category: str = ""
    locked: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "category": self.category,
            "locked": self.locked,
            "metadata": dict(self.metadata),
            "updated_at": self.updated_at,
        }


class AIMemory:
    """Central project + global memory (TZ #6)."""

    def __init__(
        self,
        project_id: str = "",
        *,
        app_dir: str | Path | None = None,
        series_id: str = "",
    ) -> None:
        self.project_id = project_id
        self.series_id = series_id or project_id
        self.app_dir = Path(app_dir) if app_dir else Path.cwd()
        self._memory_dir = self._resolve_memory_dir()
        self._memory_dir.mkdir(parents=True, exist_ok=True)
        self._project_db = self._memory_dir / f"project_{self._safe_id(project_id)}.db"
        self._global_db = self._memory_dir / "global_memory.db"
        self._project_json = self._memory_dir / f"project_{self._safe_id(project_id)}_memory.json"
        self._lock = threading.RLock()
        self._cache = get_semantic_cache() if semantic_cache_enabled() else None
        self._init_dbs()

    @staticmethod
    def _safe_id(pid: str) -> str:
        return re.sub(r"[^\w\-]", "_", pid or "default")[:64]

    def _resolve_memory_dir(self) -> Path:
        env = os.getenv("VM_MEMORY_DIR")
        if env:
            return Path(env)
        return self.app_dir / "data" / "memory"

    def _init_dbs(self) -> None:
        for db in (self._project_db, self._global_db):
            with self._lock:
                conn = sqlite3.connect(str(db), check_same_thread=False)
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS characters (
                        name TEXT PRIMARY KEY,
                        translation TEXT NOT NULL,
                        gender TEXT DEFAULT '',
                        age TEXT DEFAULT '',
                        style TEXT DEFAULT '',
                        relationships TEXT DEFAULT '{}',
                        locked INTEGER DEFAULT 0,
                        hit_count INTEGER DEFAULT 0,
                        updated_at REAL
                    );
                    CREATE TABLE IF NOT EXISTS locations (
                        name TEXT PRIMARY KEY,
                        translation TEXT NOT NULL,
                        locked INTEGER DEFAULT 0,
                        updated_at REAL
                    );
                    CREATE TABLE IF NOT EXISTS brands (
                        name TEXT PRIMARY KEY,
                        translation TEXT NOT NULL,
                        locked INTEGER DEFAULT 0,
                        updated_at REAL
                    );
                    CREATE TABLE IF NOT EXISTS glossary (
                        term TEXT PRIMARY KEY,
                        translation TEXT NOT NULL,
                        category TEXT DEFAULT '',
                        locked INTEGER DEFAULT 0,
                        hit_count INTEGER DEFAULT 0,
                        updated_at REAL
                    );
                    CREATE TABLE IF NOT EXISTS voice_profiles (
                        character TEXT PRIMARY KEY,
                        timbre TEXT DEFAULT '',
                        pitch TEXT DEFAULT '',
                        emotion TEXT DEFAULT '',
                        voice_model TEXT DEFAULT '',
                        rate TEXT DEFAULT '',
                        locked INTEGER DEFAULT 0,
                        updated_at REAL
                    );
                    CREATE TABLE IF NOT EXISTS style_matrix (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        locked INTEGER DEFAULT 0,
                        updated_at REAL
                    );
                    CREATE TABLE IF NOT EXISTS user_corrections (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        field_type TEXT,
                        original TEXT,
                        corrected TEXT,
                        segment_index INTEGER DEFAULT -1,
                        locked INTEGER DEFAULT 1,
                        created_at REAL
                    );
                    CREATE TABLE IF NOT EXISTS film_context (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at REAL
                    );
                """)
                conn.commit()
                conn.close()

    def _conn(self, *, global_db: bool = False) -> sqlite3.Connection:
        path = self._global_db if global_db else self._project_db
        return sqlite3.connect(str(path), check_same_thread=False)

  # ── Public API (§14) ─────────────────────────────────────────────

    def find(self, key: str, *, category: str = "glossary") -> MemoryEntry | None:
        """Look up a memory entry by key."""
        table = self._table_for(category)
        if not table:
            return None
        key_col = self._key_col(category)
        val_col = self._value_col(category)
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute(
                    f"SELECT {key_col}, {val_col}, locked, updated_at "
                    f"FROM {table} WHERE {key_col}=? COLLATE NOCASE",
                    (key,),
                ).fetchone()
                if not row:
                    conn.close()
                    conn = self._conn(global_db=True)
                    row = conn.execute(
                        f"SELECT {key_col}, {val_col}, locked, updated_at "
                        f"FROM {table} WHERE {key_col}=? COLLATE NOCASE",
                        (key,),
                    ).fetchone()
                if row:
                    return MemoryEntry(
                        key=row[0], value=row[1], category=category,
                        locked=bool(row[2]), updated_at=row[3] or 0.0,
                    )
            finally:
                conn.close()
        return None

    @staticmethod
    def _value_col(category: str) -> str:
        return "value" if category == "style" else "translation"

    def save(self, entry: MemoryEntry, *, global_memory: bool = False) -> bool:
        """Save or update a memory entry."""
        table = self._table_for(entry.category)
        if not table:
            return False
        key_col = self._key_col(entry.category)
        val_col = self._value_col(entry.category)
        now = time.time()
        with self._lock:
            conn = self._conn(global_db=global_memory)
            try:
                if entry.category in ("voice",):
                    existing = conn.execute(
                        f"SELECT locked FROM {table} WHERE {key_col}=? COLLATE NOCASE",
                        (entry.key,),
                    ).fetchone()
                else:
                    existing = conn.execute(
                        f"SELECT locked, {val_col} FROM {table} WHERE {key_col}=? COLLATE NOCASE",
                        (entry.key,),
                    ).fetchone()
                if existing and existing[0]:
                    if entry.category != "voice" and len(existing) > 1 and existing[1] != entry.value:
                        logger.warning(
                            "[MEMORY] locked entry %s/%s — save rejected", entry.category, entry.key
                        )
                        return False
                if entry.category == "character":
                    meta = entry.metadata
                    conn.execute(
                        "INSERT OR REPLACE INTO characters "
                        "(name, translation, gender, age, style, relationships, locked, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (entry.key, entry.value,
                         meta.get("gender", ""), meta.get("age", ""),
                         meta.get("style", ""), json.dumps(meta.get("relationships", {})),
                         int(entry.locked), now),
                    )
                elif entry.category == "voice":
                    meta = entry.metadata
                    conn.execute(
                        "INSERT OR REPLACE INTO voice_profiles "
                        "(character, timbre, pitch, emotion, voice_model, rate, locked, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (entry.key, meta.get("timbre", ""), meta.get("pitch", ""),
                         meta.get("emotion", ""), meta.get("voice_model", ""),
                         meta.get("rate", ""), int(entry.locked), now),
                    )
                elif entry.category == "style":
                    conn.execute(
                        "INSERT OR REPLACE INTO style_matrix (key, value, locked, updated_at) "
                        "VALUES (?, ?, ?, ?)",
                        (entry.key, entry.value, int(entry.locked), now),
                    )
                elif entry.category == "location":
                    conn.execute(
                        "INSERT OR REPLACE INTO locations (name, translation, locked, updated_at) "
                        "VALUES (?, ?, ?, ?)",
                        (entry.key, entry.value, int(entry.locked), now),
                    )
                elif entry.category == "brand":
                    conn.execute(
                        "INSERT OR REPLACE INTO brands (name, translation, locked, updated_at) "
                        "VALUES (?, ?, ?, ?)",
                        (entry.key, entry.value, int(entry.locked), now),
                    )
                else:
                    conn.execute(
                        "INSERT OR REPLACE INTO glossary "
                        "(term, translation, category, locked, updated_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (entry.key, entry.value, entry.category, int(entry.locked), now),
                    )
                conn.commit()
                return True
            finally:
                conn.close()

    def update(
        self,
        key: str,
        value: str,
        *,
        category: str = "glossary",
        user_correction: bool = False,
        global_memory: bool = False,
    ) -> bool:
        """Update an entry. User corrections become canonical (§11)."""
        entry = MemoryEntry(
            key=key, value=value, category=category,
            locked=user_correction,
        )
        ok = self.save(entry, global_memory=global_memory)
        if ok and user_correction:
            self._record_correction(category, key, value)
        return ok

    def learn(self, job_data: dict[str, Any]) -> dict[str, Any]:
        """Auto-learn from completed film (§10)."""
        learned: dict[str, int] = {
            "characters": 0, "glossary": 0, "voices": 0, "corrections": 0, "cache": 0,
        }
        segments = job_data.get("segments") or []
        source_segments = job_data.get("source_segments") or []
        audits = job_data.get("translation_audits") or []
        target_lang = str(job_data.get("target_lang") or "uk")

        # Learn from translation audits.
        for audit in audits:
            idx = int(audit.get("index", -1))
            source = str(audit.get("source_text") or "")
            final = str(audit.get("final_text") or audit.get("text") or "")
            if not source or not final:
                continue

            # Store in semantic cache.
            if self._cache:
                self._cache.store(
                    source, final,
                    source_lang=str(job_data.get("source_lang") or "en"),
                    target_lang=target_lang,
                    task_type="translate",
                )
                learned["cache"] += 1

            # Extract named entities for glossary.
            for word in self._extract_entities(source):
                existing = self.find(word, category="glossary")
                if existing and existing.locked:
                    continue
                trans = self._find_translation_in_text(word, source, final)
                if trans and self.save(MemoryEntry(key=word, value=trans, category="glossary")):
                    learned["glossary"] += 1

        # Learn user corrections (§11).
        corrections = job_data.get("user_corrections") or []
        for corr in corrections:
            self.update(
                str(corr.get("key") or corr.get("original") or ""),
                str(corr.get("value") or corr.get("corrected") or ""),
                category=str(corr.get("category") or "glossary"),
                user_correction=True,
            )
            learned["corrections"] += 1

        # Voice profiles from job data.
        voice_map = job_data.get("voice_profiles") or {}
        for char, profile in voice_map.items():
            if isinstance(profile, dict):
                entry = MemoryEntry(
                    key=char, value=profile.get("voice_model", ""),
                    category="voice", metadata=profile,
                )
                if self.save(entry):
                    learned["voices"] += 1

        self._save_project_json(learned)
        logger.info("[MEMORY] learned from job %s: %s", self.project_id, learned)
        return learned

    def search(
        self,
        query: str,
        *,
        category: str = "glossary",
        limit: int = 10,
    ) -> list[MemoryEntry]:
        """Search memory entries (§9)."""
        results: list[MemoryEntry] = []
        table = self._table_for(category)
        if not table:
            return results
        key_col = self._key_col(category)
        val_col = self._value_col(category)
        q = f"%{query}%"
        with self._lock:
            for global_db in (False, True):
                conn = self._conn(global_db=global_db)
                try:
                    rows = conn.execute(
                        f"SELECT {key_col}, {val_col}, locked, updated_at "
                        f"FROM {table} WHERE {key_col} LIKE ? OR {val_col} LIKE ? "
                        f"LIMIT ?",
                        (q, q, limit),
                    ).fetchall()
                    for row in rows:
                        results.append(MemoryEntry(
                            key=row[0], value=row[1], category=category,
                            locked=bool(row[2]), updated_at=row[3] or 0.0,
                        ))
                finally:
                    conn.close()
        return results[:limit]

    def get_character(self, name: str) -> dict[str, Any] | None:
        entry = self.find(name, category="character")
        if not entry:
            return None
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute(
                    "SELECT translation, gender, age, style, relationships, locked "
                    "FROM characters WHERE name=? COLLATE NOCASE", (name,),
                ).fetchone()
                if row:
                    return {
                        "name": name, "translation": row[0],
                        "gender": row[1], "age": row[2], "style": row[3],
                        "relationships": json.loads(row[4] or "{}"),
                        "locked": bool(row[5]),
                    }
            finally:
                conn.close()
        return {"name": name, "translation": entry.value, "locked": entry.locked}

    def get_glossary(self) -> list[dict[str, Any]]:
        return self._load_table("glossary", "term")

    def get_style(self) -> dict[str, str]:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute("SELECT key, value FROM style_matrix").fetchall()
                return {k: v for k, v in rows}
            finally:
                conn.close()

    def get_voice(self, character: str) -> dict[str, Any] | None:
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute(
                    "SELECT timbre, pitch, emotion, voice_model, rate, locked "
                    "FROM voice_profiles WHERE character=? COLLATE NOCASE",
                    (character,),
                ).fetchone()
                if row:
                    return {
                        "character": character,
                        "timbre": row[0], "pitch": row[1],
                        "emotion": row[2], "voice_model": row[3],
                        "rate": row[4], "locked": bool(row[5]),
                    }
            finally:
                conn.close()
        return None

    # ── Consistency & context (§3–§4, §13) ───────────────────────────

    def apply_glossary(self, text: str) -> str:
        """Enforce locked glossary/character translations in text."""
        result = text
        for table, key_col in [
            ("characters", "name"), ("glossary", "term"),
            ("brands", "name"), ("locations", "name"),
        ]:
            with self._lock:
                conn = self._conn()
                try:
                    rows = conn.execute(
                        f"SELECT {key_col}, translation FROM {table} WHERE locked=1"
                    ).fetchall()
                finally:
                    conn.close()
            for key, trans in rows:
                if key and trans:
                    result = re.sub(
                        re.escape(key), trans, result, flags=re.IGNORECASE
                    )
        return result

    def check_consistency(self, segments: list[str], source_segments: list[str]) -> list[dict]:
        """Detect contradictory translations (§13)."""
        issues: list[dict] = []
        char_map: dict[str, set[str]] = {}
        for src, tgt in zip(source_segments, segments):
            for entity in self._extract_entities(src):
                trans = self._find_translation_in_text(entity, src, tgt)
                if trans:
                    char_map.setdefault(entity.lower(), set()).add(trans)
        for entity, translations in char_map.items():
            if len(translations) > 1:
                issues.append({
                    "entity": entity,
                    "translations": sorted(translations),
                    "reason": "contradictory_translation",
                })
        return issues

    def build_context_prompt(self) -> str:
        """Build memory context for LLM prompts (§4)."""
        parts: list[str] = []
        chars = self._load_table("characters", "name")
        if chars:
            lines = [f"  {c['key']}: {c['value']}" for c in chars[:20]]
            parts.append("Characters:\n" + "\n".join(lines))
        glossary = self.get_glossary()[:30]
        if glossary:
            lines = [f"  {g['key']}: {g['value']}" for g in glossary]
            parts.append("Glossary:\n" + "\n".join(lines))
        style = self.get_style()
        if style:
            lines = [f"  {k}: {v}" for k, v in style.items()]
            parts.append("Style:\n" + "\n".join(lines))
        return "\n\n".join(parts)

    def lookup_translation(
        self,
        source_text: str,
        *,
        source_lang: str = "en",
        target_lang: str = "uk",
        context: str = "",
        task_type: str = "translate",
    ) -> str | None:
        """Check semantic cache before LLM (§2). Delegates to SemanticCache."""
        if not self._cache:
            return None
        hit = self._cache.lookup(
            source_text, source_lang=source_lang, target_lang=target_lang,
            context=context, task_type=task_type,
        )
        return hit.text if hit else None

    def store_translation(
        self,
        source_text: str,
        result_text: str,
        *,
        source_lang: str = "en",
        target_lang: str = "uk",
        context: str = "",
        task_type: str = "translate",
        **kwargs: Any,
    ) -> None:
        if self._cache:
            self._cache.store(
                source_text,
                result_text,
                source_lang=source_lang,
                target_lang=target_lang,
                context=context,
                task_type=task_type,
                **kwargs,
            )

    def get_status(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "series_id": self.series_id,
            "enabled": memory_enabled(),
            "characters": len(self._load_table("characters", "name")),
            "glossary": len(self.get_glossary()),
            "style_keys": len(self.get_style()),
            "cache": self._cache.to_dict() if self._cache else None,
            "project_db": str(self._project_db),
            "global_db": str(self._global_db),
        }

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _table_for(category: str) -> str:
        return {
            "character": "characters", "characters": "characters",
            "location": "locations", "locations": "locations",
            "brand": "brands", "brands": "brands",
            "glossary": "glossary", "voice": "voice_profiles",
            "style": "style_matrix",
        }.get(category, "glossary")

    @staticmethod
    def _key_col(category: str) -> str:
        return {
            "character": "name", "characters": "name",
            "location": "name", "locations": "name",
            "brand": "name", "brands": "name",
            "voice": "character", "style": "key",
        }.get(category, "term")

    def _load_table(self, table: str, key_col: str) -> list[dict[str, Any]]:
        val_col = "value" if table == "style_matrix" else "translation"
        results: list[dict[str, Any]] = []
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    f"SELECT {key_col}, {val_col}, locked FROM {table}"
                ).fetchall()
                for row in rows:
                    results.append({"key": row[0], "value": row[1], "locked": bool(row[2])})
            finally:
                conn.close()
        return results

    def _record_correction(self, field_type: str, original: str, corrected: str) -> None:
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT INTO user_corrections (field_type, original, corrected, locked, created_at) "
                    "VALUES (?, ?, ?, 1, ?)",
                    (field_type, original, corrected, time.time()),
                )
                conn.commit()
            finally:
                conn.close()

    def _save_project_json(self, learned: dict) -> None:
        try:
            data = {
                "project_id": self.project_id,
                "series_id": self.series_id,
                "updated_at": time.time(),
                "learned": learned,
                "characters": self._load_table("characters", "name"),
                "glossary": self.get_glossary(),
                "style": self.get_style(),
            }
            from engines.storage.atomic import atomic_write_json
            atomic_write_json(self._project_json, data)
        except Exception as exc:
            logger.warning("[MEMORY] project json save failed: %s", exc)

    @staticmethod
    def _extract_entities(text: str) -> list[str]:
        """Simple named-entity extraction (capitalised words/phrases)."""
        return re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b", text)

    @staticmethod
    def _find_translation_in_text(entity: str, source: str, target: str) -> str:
        """Heuristic: if entity appears in source, look for corresponding fragment in target."""
        if entity.lower() not in source.lower():
            return ""
        src_pos = source.lower().find(entity.lower())
        if src_pos < 0:
            return ""
        ratio = len(target) / max(len(source), 1)
        est_start = int(src_pos * ratio)
        est_end = int((src_pos + len(entity)) * ratio)
        fragment = target[est_start:est_end + 20].strip()
        words = fragment.split()
        return " ".join(words[:len(entity.split()) + 2]) if words else ""


_memory: dict[str, AIMemory] = {}
_memory_lock = threading.Lock()


def get_memory(
    project_id: str = "",
    *,
    app_dir: str | Path | None = None,
    series_id: str = "",
) -> AIMemory:
    key = project_id or "default"
    with _memory_lock:
        if key not in _memory:
            _memory[key] = AIMemory(project_id, app_dir=app_dir, series_id=series_id)
        return _memory[key]
