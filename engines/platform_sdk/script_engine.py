"""P720 Script Engine — automation interface (language runtimes later)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


SUPPORTED_LANGUAGES = ("python", "javascript", "lua")


@dataclass
class ScriptJob:
    language: str
    source: str
    name: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


class ScriptEngine:
    """
    Architecture hook for automation scripts.
    Concrete language runtimes are a separate stage — only Python eval sandbox stub here.
    """

    def __init__(self) -> None:
        self._hooks: dict[str, Callable[[ScriptJob], Any]] = {}

    def register_runtime(self, language: str, runner: Callable[[ScriptJob], Any]) -> None:
        self._hooks[language.lower()] = runner

    def supported(self) -> tuple[str, ...]:
        return SUPPORTED_LANGUAGES

    def run(self, job: ScriptJob) -> dict[str, Any]:
        lang = job.language.lower()
        if lang not in SUPPORTED_LANGUAGES:
            return {"ok": False, "error": f"unsupported language: {lang}"}
        runner = self._hooks.get(lang)
        if runner is None:
            return {
                "ok": False,
                "error": "runtime_not_installed",
                "language": lang,
                "message": "Language runtime is a follow-up stage",
            }
        try:
            result = runner(job)
            return {"ok": True, "result": result}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


_ENGINE: ScriptEngine | None = None


def get_script_engine() -> ScriptEngine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = ScriptEngine()
    return _ENGINE
