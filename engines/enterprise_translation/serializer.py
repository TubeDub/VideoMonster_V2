"""EntitySerializer — per-engine placeholder token formats."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from engines.enterprise_translation.config import SERIALIZER_BENCH_FILE

# Default formats per engine family
_DEFAULT_FORMATS: dict[str, str] = {
    "deepl": "bracket_double",      # [[PERSON_1]]
    "deep": "bracket_double",
    "google": "brace",              # {PERSON_1}
    "microsoft": "brace",
    "argos": "paren",               # (PERSON_1)
    "libre": "angle",               # <PERSON_1>
    "libretranslate": "angle",
    "nllb": "underscore",           # __PERSON_1__
    "marian": "underscore",
    "openai": "hash",               # #PERSON_1#
    "gemini": "hash",
    "default": "bracket_double",
}

_FORMAT_TEMPLATES = {
    "bracket_double": ("[[", "]]"),
    "brace": ("{", "}"),
    "paren": ("(", ")"),
    "angle": ("<", ">"),
    "underscore": ("__", "__"),
    "hash": ("#", "#"),
}


def _bench_path(app_dir: Path) -> Path:
    return app_dir / "data" / SERIALIZER_BENCH_FILE


class EntitySerializer:
    def __init__(self, app_dir: Path | None = None):
        self.app_dir = app_dir
        self._overrides: dict[str, str] = {}
        if app_dir:
            self._load_overrides()

    def _load_overrides(self) -> None:
        path = _bench_path(self.app_dir)
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._overrides = dict(data.get("engine_formats") or {})
        except Exception:
            pass

    def save_override(self, engine_id: str, format_key: str) -> None:
        self._overrides[engine_id.lower()] = format_key
        if not self.app_dir:
            return
        path = _bench_path(self.app_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "engine_formats": self._overrides,
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _format_key_for_engine(self, engine_id: str) -> str:
        e = engine_id.lower()
        if e in self._overrides:
            return self._overrides[e]
        for prefix, fmt in _DEFAULT_FORMATS.items():
            if prefix in e:
                return fmt
        return _DEFAULT_FORMATS["default"]

    def get_token_for_engine(self, entity_id: str, engine_id: str) -> str:
        fmt = self._format_key_for_engine(engine_id)
        left, right = _FORMAT_TEMPLATES.get(fmt, _FORMAT_TEMPLATES["bracket_double"])
        return f"{left}{entity_id}{right}"

    def serialize(self, text: str, engine_id: str, entity_ids: list[str]) -> str:
        """Replace entity_id substrings with engine tokens (ids already in text)."""
        out = text
        for eid in sorted(entity_ids, key=len, reverse=True):
            token = self.get_token_for_engine(eid, engine_id)
            out = out.replace(eid, token)
        return out

    def deserialize(self, text: str, engine_id: str) -> tuple[str, list[str]]:
        """Strip tokens; return text with entity_ids and list of found tokens."""
        fmt = self._format_key_for_engine(engine_id)
        left, right = _FORMAT_TEMPLATES.get(fmt, _FORMAT_TEMPLATES["bracket_double"])
        pattern = re.escape(left) + r"([A-Z][A-Z0-9_]+)" + re.escape(right)
        found: list[str] = []

        def repl(m: re.Match) -> str:
            found.append(m.group(0))
            return m.group(1)

        out = re.sub(pattern, repl, text)
        return out, found

    def all_format_keys(self) -> list[str]:
        return list(_FORMAT_TEMPLATES.keys())

    def token_pattern(self, engine_id: str) -> re.Pattern[str]:
        fmt = self._format_key_for_engine(engine_id)
        left, right = _FORMAT_TEMPLATES.get(fmt, _FORMAT_TEMPLATES["bracket_double"])
        return re.compile(re.escape(left) + r"[A-Z][A-Z0-9_]+" + re.escape(right))
